"""上传使用随机存储名；任何文件访问均限制在数据目录内。"""

from pathlib import Path

from wiki_app.config import get_settings
from wiki_app.errors import AppError

FILE_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
}


def storage_path(key: str) -> Path:
    """key 始终是相对资料目录的路径；宿主机与容器根目录不同，但 key 必须一致。"""
    root = get_settings().data_dir.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root) or path == root:
        raise AppError("文件路径无效。")
    return path


def file_type(filename: str) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix not in FILE_TYPES:
        raise AppError("暂不支持此文件类型，请上传文档、图片、音频或视频。")
    return suffix, FILE_TYPES[suffix]
