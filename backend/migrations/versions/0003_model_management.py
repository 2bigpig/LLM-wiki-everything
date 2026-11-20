"""运行时模型配置、阶段修订和 1024 维空间；保留旧向量列兼容历史数据。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None
TABLES = ("model_connections", "managed_models", "model_revisions", "model_preferences",
          "model_probes", "embedding_spaces", "source_vectors")


def upgrade():
    from wiki_app import models
    from wiki_app.modules.model_management.service import preferences

    connection = op.get_bind()
    op.add_column("processing_runs", sa.Column("model_snapshot", sa.JSON(), nullable=True))
    for name in ("connection_id", "revision_id"):
        op.add_column("model_calls", sa.Column(name, sa.String(36), nullable=True))
    op.add_column("model_calls", sa.Column("operation", sa.String(30), nullable=False, server_default="llm"))
    op.alter_column("model_calls", "run_id", existing_type=sa.String(36), nullable=True)
    for name in TABLES:
        models.Base.metadata.tables[name].create(connection)
    # 不同空间共享固定维度，查询始终附 space_id；旧 HNSW 索引暂保留供回滚兼容。
    op.execute("CREATE INDEX ix_source_vectors_embedding ON source_vectors USING hnsw (embedding vector_cosine_ops)")
    with Session(bind=connection) as session:
        preferences(session)
        session.flush()


def downgrade():
    # 模型配置和加密凭据是用户数据，不能通过降级悄悄删除。
    raise RuntimeError("此迁移保留模型配置与向量历史；请使用升级前数据库备份回退。")
