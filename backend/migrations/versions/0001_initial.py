"""首版固定 SQL 快照，后续修改通过新迁移完成。"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    # 显式降级只删除本应用的业务表，框架表和 extension 独立管理。
    for table in (
        "wiki_revisions",
        "model_calls",
        "analyses",
        "relations",
        "attachments",
        "sources",
        "processing_runs",
        "wiki_pages",
        "records",
    ):
        op.drop_table(table)
