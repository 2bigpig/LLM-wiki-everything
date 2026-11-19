"""已知视频平台优先复用字幕；无字幕时在明确上限内下载本地素材。"""

import html
import re
import subprocess
import sys
from urllib.parse import urlsplit

from wiki_app.config import get_settings
from wiki_app.errors import AppError
from wiki_app.integrations.storage import file_type, storage_path
from wiki_app.integrations.web import public_address
from wiki_app.models import Attachment


def is_platform_video(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in ("youtu.be", "b23.tv"):
        return True
    if host == "youtube.com" or host.endswith(".youtube.com"):
        return parts.path == "/watch" or parts.path.startswith("/shorts/")
    return (host == "bilibili.com" or host.endswith(".bilibili.com")) and parts.path.startswith(
        "/video/"
    )


def seconds(value: str) -> float:
    result = 0.0
    for part in value.replace(",", ".").split(":"):
        result = result * 60 + float(part)
    return result


def parse_vtt(content: str) -> list[dict]:
    """保留字幕原始时间，移除显示标签与完全重复的连续字幕。"""
    segments = []
    for block in re.split(r"\n\s*\n", content.replace("\r\n", "\n")):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            match = re.match(r"([\d:.,]+)\s+-->\s+([\d:.,]+)", line)
            if match:
                value = html.unescape(re.sub(r"<[^>]+>", "", "\n".join(lines[index + 1 :]))).strip()
                if value and (not segments or segments[-1]["text"] != value):
                    segments.append(
                        {"text": value, "start": seconds(match[1]), "end": seconds(match[2])}
                    )
                break
    return segments


def run_downloader(args: list[str], timeout: int):
    try:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "yt_dlp",
                "--ignore-config",
                "--no-playlist",
                "--no-progress",
                "--socket-timeout",
                "20",
                "--retries",
                "2",
                *args,
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AppError("平台素材获取超时，请补充字幕或上传本地文件。", "platform_failed") from exc


def read_platform_video(
    url: str, record_id: str, version: int
) -> tuple[list[dict], Attachment | None]:
    if not is_platform_video(url):
        raise AppError("请提交支持平台的具体视频地址。")
    parts = urlsplit(url)
    public_address(parts.hostname, parts.port or 443)
    settings = get_settings()
    folder = storage_path(f"derived/platform-{record_id}-v{version}")
    folder.mkdir(parents=True, exist_ok=True)
    common = [
        "--paths",
        str(folder),
        "-o",
        "source.%(ext)s",
        "--match-filter",
        f"duration <= {settings.max_media_minutes * 60}",
    ]
    run_downloader(
        [
            *common,
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "zh.*,en.*",
            "--sub-format",
            "vtt",
            "--skip-download",
            "--",
            url,
        ],
        120,
    )
    subtitles = sorted(
        folder.glob("*.vtt"), key=lambda path: (0 if ".zh" in path.name else 1, path.name)
    )
    for path in subtitles:
        segments = parse_vtt(path.read_text(encoding="utf-8-sig"))
        if segments:
            return segments, None
    result = run_downloader(
        [
            *common,
            "--max-filesize",
            f"{settings.max_upload_mb}M",
            "-f",
            "bv*[height<=720]+ba/b[height<=720]/b",
            "--",
            url,
        ],
        1200,
    )
    media = [
        path
        for path in folder.iterdir()
        if path.suffix.lower() in (".mp4", ".webm", ".mkv", ".m4a", ".mp3")
    ]
    if result.returncode or not media:
        raise AppError(
            "平台内容暂时无法读取，可能需要登录、站点适配或更换素材；请上传文件或补充正文。",
            "platform_failed",
        )
    path = media[0]
    if path.stat().st_size > settings.max_upload_mb * 1024 * 1024:
        path.unlink()
        raise AppError("下载素材超过配置的大小上限，请上传裁剪后的文件。", "too_large")
    _, media_type = file_type(path.name)
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return [], Attachment(
        record_id=record_id,
        filename=path.name,
        storage_key=path.relative_to(settings.data_dir.resolve()).as_posix(),
        media_type=media_type,
        size=path.stat().st_size,
        sha256=digest.hexdigest(),
    )
