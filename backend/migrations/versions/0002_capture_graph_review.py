"""统一录入、内容身份、图谱与站内回顾；保留原记录和来源 ID。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = ("resources", "capture_inputs", "topics", "record_topics", "capture_insights",
          "review_preferences", "resource_review_states", "review_batches", "review_cards")


def upgrade():
    from wiki_app import models
    from wiki_app.modules.capture.service import ensure_inputs
    from wiki_app.modules.capture.topics import sync_topics

    connection = op.get_bind()
    for name in TABLES:
        models.Base.metadata.tables[name].create(connection)
    op.add_column("sources", sa.Column("input_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_sources_capture_input", "sources", "capture_inputs", ["input_id"], ["id"])
    op.create_index("ix_sources_input_id", "sources", ["input_id"])
    op.add_column("analyses", sa.Column("topic_evidence", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("attachments", sa.Column("derived", sa.Boolean(), nullable=False, server_default=sa.false()))
    # PostgreSQL 为初始 SQL 自动命名唯一约束，按列定位而不是猜测约束名。
    for constraint in sa.inspect(connection).get_unique_constraints("records"):
        if constraint["column_names"] == ["url_key"]:
            op.drop_constraint(constraint["name"], "records", type_="unique")
    op.create_index("ix_records_url_key", "records", ["url_key"])
    with Session(bind=connection, expire_on_commit=False) as session:
        for attachment in session.scalars(sa.select(models.Attachment)):
            if attachment.storage_key.startswith("derived/platform-"):
                attachment.derived = True
        session.flush()
        for record in session.scalars(sa.select(models.Record)):
            # 旧短文里的引用链接仍属于原文，升级不把它们变成新的自动读取任务。
            inputs = ensure_inputs(session, record, legacy=True)
            for source in session.scalars(sa.select(models.Source).where(
                models.Source.record_id == record.id, models.Source.version == record.version
            )):
                matches = [item for item in inputs if item.origin == source.origin]
                if not matches and source.locator.get("attachment_id"):
                    matches = [item for item in inputs if item.attachment_id == source.locator["attachment_id"]]
                    attachment = session.get(models.Attachment, source.locator["attachment_id"])
                    if not matches and attachment and attachment.derived and record.url:
                        matches = [item for item in inputs if item.kind == "url"][:1]
                if not matches and source.locator.get("url"):
                    matches = [item for item in inputs if item.kind == "url"][:1]
                if matches:
                    source.input_id = matches[0].id
                    matches[0].status = "succeeded"
                    matches[0].fetched_at = record.updated_at
            if record.status in ("completed", "failed", "waiting_configuration", "budget_exceeded"):
                for item in inputs:
                    if item.status == "queued":
                        item.status = "failed"
                        item.error = "旧版本未保留该来源的成功读取结果，可单独重试。"
        for analysis in session.scalars(sa.select(models.Analysis).order_by(models.Analysis.created_at)):
            sync_topics(session, analysis)
        session.flush()


def downgrade():
    connection = op.get_bind()
    duplicate = connection.execute(sa.text(
        "SELECT url_key FROM records WHERE url_key IS NOT NULL GROUP BY url_key HAVING count(*) > 1 LIMIT 1"
    )).first()
    if duplicate:
        raise RuntimeError("已有重复收藏事件，不能恢复旧唯一约束；降级已停止，不删除收藏。")
    op.drop_index("ix_sources_input_id", table_name="sources")
    op.drop_constraint("fk_sources_capture_input", "sources", type_="foreignkey")
    op.drop_column("sources", "input_id")
    op.drop_column("analyses", "topic_evidence")
    op.drop_column("attachments", "derived")
    for name in reversed(TABLES):
        op.drop_table(name)
    op.drop_index("ix_records_url_key", table_name="records")
    op.create_unique_constraint("records_url_key_key", "records", ["url_key"])
