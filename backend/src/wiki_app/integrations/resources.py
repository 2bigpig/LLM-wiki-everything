"""跨进程资源锁：所有本地 GPU 能力使用同一个 PostgreSQL 锁。"""

from contextlib import contextmanager

from sqlalchemy import text

from wiki_app import db

GPU_LOCK = 406008
BUDGET_LOCK = 406009
WIKI_LOCK = 406010


@contextmanager
def advisory_slot(key: int):
    """会话锁独立于业务事务，退出或进程中断时释放。"""
    if db.engine.dialect.name != "postgresql":
        yield
        return
    with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


@contextmanager
def gpu_slot():
    # ponytail: 单卡全局串行；多卡时按设备 ID 分配锁。
    with advisory_slot(GPU_LOCK):
        yield
