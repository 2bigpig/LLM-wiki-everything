"""人工内容独立保存，所有页面变更创建可恢复的版本。"""

import re
import unicodedata

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from wiki_app.errors import AppError
from wiki_app.models import Relation, Source, WikiPage, WikiRevision, now
from wiki_app.schemas import RelationDraft, WikiDraft


def title_key(title: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", title).casefold().split())


def contains_quote(text: str, quote: str) -> bool:
    normalized = re.sub(r"\s+", "", quote)
    return len(normalized) >= 4 and normalized in re.sub(r"\s+", "", text)


def save_relation(
    session: Session,
    record_id: str,
    draft: RelationDraft,
    current_sources: dict[str, Source],
    candidates: dict[str, dict],
):
    source = current_sources.get(draft.source_id)
    target = candidates.get(draft.target_source_id)
    if (
        not source
        or not target
        or target["record_id"] != draft.target_id
        or draft.target_id == record_id
        or not contains_quote(source.text, draft.source_quote)
        or not contains_quote(target["text"], draft.target_quote)
    ):
        raise AppError("模型给出的关联证据无法核对，未保存该结果。", "invalid_evidence", 502)
    owner, other = record_id, draft.target_id
    if draft.kind in ("same_topic", "contradicts", "duplicate", "complements", "experience"):
        owner, other = sorted((record_id, draft.target_id))
    evidence = [
        {"source_id": source.id, "quote": draft.source_quote},
        {"source_id": draft.target_source_id, "quote": draft.target_quote},
    ]
    insert = postgres_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(Relation).values(
        record_id=owner,
        target_id=other,
        kind=draft.kind,
        explanation=draft.explanation,
        evidence=evidence,
    )
    # 双侧同时发现同一关系时由数据库合并；人工忽略和认可状态不被覆盖。
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["record_id", "target_id", "kind"],
            set_={
                "explanation": statement.excluded.explanation,
                "evidence": statement.excluded.evidence,
            },
            where=Relation.feedback != "ignored",
        )
    )


def snapshot(session: Session, page: WikiPage, actor: str, note: str, run_id: str | None = None):
    session.add(
        WikiRevision(
            page_id=page.id,
            run_id=run_id,
            version=page.version,
            generated=page.generated,
            personal=page.personal,
            source_ids=page.source_ids,
            actor=actor,
            change_note=note,
        )
    )


def apply_wiki(
    session: Session,
    draft: WikiDraft,
    run_id: str,
    allowed_sources: dict[str, Source],
    expected_versions: dict[str, int],
) -> WikiPage:
    page = (
        session.get(WikiPage, draft.page_id)
        if draft.page_id
        else session.scalar(select(WikiPage).where(WikiPage.title_key == title_key(draft.title)))
    )
    if page and session.scalar(
        select(WikiRevision.id).where(
            WikiRevision.page_id == page.id,
            WikiRevision.run_id == run_id,
        )
    ):
        return page
    if draft.page_id and draft.page_id not in expected_versions:
        raise AppError("模型选择了未提供的 Wiki 页面。", "invalid_evidence", 502)
    if page and page.id not in expected_versions:
        raise AppError("同名页面已被创建，重试后将综合最新内容。", "version_conflict", 409)
    source_ids = list(
        dict.fromkeys(sid for section in draft.sections for sid in section.source_ids)
    )
    if any(sid not in allowed_sources for sid in source_ids):
        raise AppError("Wiki 引用了未提供的资料，未保存该版本。", "invalid_evidence", 502)
    sections = []
    for section in draft.sections:
        citations = " · ".join(
            f"[来源 {source_ids.index(sid) + 1}](/records/{allowed_sources[sid].record_id}#source-{sid})"
            for sid in dict.fromkeys(section.source_ids)
        )
        sections.append(f"## {section.heading}\n\n{section.content}\n\n{citations}")
    generated = "\n\n".join(sections)
    if page:
        expected = expected_versions[page.id]
        changed = session.execute(
            update(WikiPage)
            .where(
                WikiPage.id == page.id,
                WikiPage.version == expected,
            )
            .values(
                generated=generated, source_ids=source_ids, version=expected + 1, updated_at=now()
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise AppError("Wiki 在生成期间被修改，请重试以使用最新版本。", "version_conflict", 409)
        session.refresh(page)
    else:
        page = WikiPage(
            title=draft.title.strip(),
            title_key=title_key(draft.title),
            generated=generated,
            personal="",
            source_ids=source_ids,
            version=1,
        )
        session.add(page)
        session.flush()
    snapshot(session, page, "ai", "依据新资料综合更新；个人笔记保持独立。", run_id)
    return page


def edit_personal(session: Session, page_id: str, version: int, personal: str) -> WikiPage:
    changed = session.execute(
        update(WikiPage)
        .where(
            WikiPage.id == page_id,
            WikiPage.version == version,
        )
        .values(personal=personal, version=version + 1, updated_at=now())
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise AppError("页面已更新或不存在，请刷新后再保存。", "version_conflict", 409)
    page = session.get(WikiPage, page_id, populate_existing=True)
    snapshot(session, page, "user", "修改个人笔记。")
    return page


def restore_page(session: Session, page_id: str, version: int, revision: int) -> WikiPage:
    previous = session.scalar(
        select(WikiRevision).where(
            WikiRevision.page_id == page_id,
            WikiRevision.version == revision,
        )
    )
    if not previous:
        raise AppError("历史版本不存在。", "not_found", 404)
    changed = session.execute(
        update(WikiPage)
        .where(
            WikiPage.id == page_id,
            WikiPage.version == version,
        )
        .values(
            generated=previous.generated,
            personal=previous.personal,
            source_ids=previous.source_ids,
            version=version + 1,
            updated_at=now(),
        )
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise AppError("页面已更新，请刷新后再恢复。", "version_conflict", 409)
    page = session.get(WikiPage, page_id, populate_existing=True)
    snapshot(session, page, "restore", f"从第 {revision} 版恢复。")
    return page
