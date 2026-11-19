"""数据库连接从环境读取，迁移配置不存放密码。"""

from logging.config import fileConfig

from alembic import context

from wiki_app import db, models  # noqa: F401

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)


def run():
    with db.engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=db.Base.metadata, compare_type=True,
            version_table_schema=context.config.attributes.get("version_table_schema"),
        )
        with context.begin_transaction():
            context.run_migrations()


run()
