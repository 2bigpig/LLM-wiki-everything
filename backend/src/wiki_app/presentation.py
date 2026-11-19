"""序列化只暴露产品数据，不返回向量、文件真实路径或服务端密钥。"""

from sqlalchemy import select

from wiki_app.models import Attachment, Record, Source


def columns(instance, names: str) -> dict:
    return {name: getattr(instance, name) for name in names.split()}


def record_out(record: Record, analysis=None) -> dict:
    if analysis and analysis.version != record.version:
        analysis = None
    return {
        **columns(
            record,
            "id kind title content url notes purpose version status coverage archived created_at updated_at",
        ),
        "summary": analysis.summary if analysis else "",
        "topics": analysis.topics if analysis else [],
    }


def run_out(run) -> dict:
    return columns(
        run, "id record_id version status stage attempts error created_at updated_at finished_at"
    )


def analysis_out(analysis) -> dict:
    return columns(
        analysis,
        "id run_id record_id version kind summary topics points observations model created_at",
    )


def source_out(session, sources: list[Source]) -> list[dict]:
    ids = {s.record_id for s in sources}
    records = {r.id: r for r in session.scalars(select(Record).where(Record.id.in_(ids)))}
    return [
        {
            **columns(s, "id record_id version text locator method"),
            "record_title": records[s.record_id].title,
            "stale": s.version != records[s.record_id].version,
            "archived": records[s.record_id].archived,
        }
        for s in sources
    ]


def attachments_out(session, record_id: str) -> list[dict]:
    return [
        columns(a, "id record_id filename media_type size sha256")
        for a in session.scalars(select(Attachment).where(Attachment.record_id == record_id))
    ]
