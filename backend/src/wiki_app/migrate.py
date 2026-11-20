"""部署启动前运行一次：业务迁移、工作流检查点和队列各自初始化。"""

import asyncio
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import text

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.worker import app


async def setup_queue():
    async with app.open_async():
        await app.schema_manager.apply_schema_async()


def main():
    config = Path.cwd() / "alembic.ini"
    if not config.exists():
        config = Path(__file__).resolve().parents[2] / "alembic.ini"
    command.upgrade(Config(str(config)), "head")
    from wiki_app.modules.model_management.scheduling import prepare_schema

    prepare_schema(db.engine)
    with PostgresSaver.from_conn_string(get_settings().postgres_dsn) as saver:
        saver.setup()
    with db.engine.connect() as connection:
        exists = connection.scalar(text("SELECT to_regclass('procrastinate_jobs')"))
    if not exists:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(setup_queue())


if __name__ == "__main__":
    main()
