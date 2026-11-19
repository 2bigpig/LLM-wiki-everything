"""统一连接和事务边界；SQLite 仅用于无需外部服务的业务测试。"""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from wiki_app.config import get_settings


class Base(DeclarativeBase):
    pass


def build_engine(url: str):
    # 保留 SQL 结构用于排错，但避免异常堆栈把资料正文等绑定参数写入日志。
    kwargs = {"pool_pre_ping": True, "hide_parameters": True}
    if url.startswith("sqlite"):
        kwargs.update(connect_args={"check_same_thread": False})
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    return engine


engine = build_engine(get_settings().database_url)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def transaction() -> Generator[Session, None, None]:
    """成功提交，失败整体回滚，业务函数不得吞掉事务错误。"""
    with SessionLocal.begin() as session:
        yield session


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
