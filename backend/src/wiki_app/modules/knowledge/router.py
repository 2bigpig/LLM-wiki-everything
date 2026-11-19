"""Wiki 阅读、个人笔记、历史恢复与 Markdown 导出。"""

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import select

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError
from wiki_app.models import Source, WikiPage, WikiRevision
from wiki_app.modules.knowledge.service import edit_personal, restore_page
from wiki_app.presentation import columns, source_out
from wiki_app.schemas import WikiEdit, WikiRestore

router = APIRouter(tags=["Wiki"])


def page_out(page):
    return columns(page, "id title generated personal version source_ids updated_at")


def require_page(session, page_id):
    page = session.get(WikiPage, page_id)
    if not page:
        raise AppError("Wiki 页面不存在。", "not_found", 404)
    return page


@router.get("/wiki")
def list_pages(q: str = Query(default="", max_length=200)):
    with db.SessionLocal() as session:
        query = select(WikiPage).order_by(WikiPage.updated_at.desc())
        if q:
            query = query.where(WikiPage.title.contains(q, autoescape=True))
        return {"items": [page_out(page) for page in session.scalars(query.limit(get_settings().wiki_list_limit))]}


@router.get("/wiki/{page_id}")
def page_detail(page_id: str):
    with db.SessionLocal() as session:
        page = require_page(session, page_id)
        sources = list(session.scalars(select(Source).where(Source.id.in_(page.source_ids))))
        revisions = list(
            session.scalars(
                select(WikiRevision)
                .where(WikiRevision.page_id == page_id)
                .order_by(WikiRevision.version.desc())
                .limit(get_settings().wiki_revision_limit)
            )
        )
        return {
            **page_out(page),
            "sources": source_out(session, sources),
            "revisions": [columns(r, "id version actor change_note created_at") for r in revisions],
        }


@router.put("/wiki/{page_id}")
def save_personal(page_id: str, data: WikiEdit):
    with db.transaction() as session:
        require_page(session, page_id)
        return page_out(edit_personal(session, page_id, data.version, data.personal))


@router.get("/wiki/{page_id}/revisions/{version}")
def revision_detail(page_id: str, version: int):
    with db.SessionLocal() as session:
        revision = session.scalar(
            select(WikiRevision).where(
                WikiRevision.page_id == page_id, WikiRevision.version == version
            )
        )
        if not revision:
            raise AppError("历史版本不存在。", "not_found", 404)
        return columns(
            revision, "version generated personal source_ids actor change_note created_at"
        )


@router.post("/wiki/{page_id}/restore")
def restore(page_id: str, data: WikiRestore):
    with db.transaction() as session:
        require_page(session, page_id)
        return page_out(restore_page(session, page_id, data.version, data.revision))


@router.get("/wiki/{page_id}/export")
def export_markdown(page_id: str):
    with db.SessionLocal() as session:
        page = require_page(session, page_id)
        sources = list(session.scalars(select(Source).where(Source.id.in_(page.source_ids))))
        references = "\n".join(
            f"- 来源 {s.id}：记录 {s.record_id}，版本 {s.version}，位置 {s.locator}"
            for s in sources
        )
        content = f"# {page.title}\n\n{page.generated}\n\n## 个人笔记\n\n{page.personal}\n\n## 来源索引\n\n{references}\n"
        return Response(
            content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="wiki-{page.id}.md"'},
        )
