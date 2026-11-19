"""本地 API/Worker 分别落盘；统一中文日志格式，不记录密钥或用户正文。"""

import logging
from logging.handlers import RotatingFileHandler

from wiki_app.config import get_settings


def configure_logging(component: str):
    """component 区分 api/worker；按配置限制文件大小和历史份数。"""
    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("wiki_app")
    logger.setLevel(settings.log_level.upper())
    logger.propagate = False
    # 热重载/测试会重复启动生命周期，先关闭旧 handler，避免重复日志及文件句柄泄漏。
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    for handler in (
        logging.StreamHandler(),
        RotatingFileHandler(settings.log_dir / f"{component}.log", maxBytes=settings.log_max_bytes,
                            backupCount=settings.log_backup_count, encoding="utf-8"),
    ):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
