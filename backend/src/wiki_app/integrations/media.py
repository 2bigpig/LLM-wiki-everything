"""宿主机处理媒体，通过回环端口调用模型；GPU 锁由模型容器持有。"""

import json
import logging
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

from wiki_app.config import get_settings
from wiki_app.errors import AppError, ConfigurationRequired
from wiki_app.integrations.storage import storage_path

logger = logging.getLogger(__name__)


def command(args: list[str], timeout: float | None = None) -> str:
    """执行本地 FFmpeg 工具，记录工具名和失败类型，不记录用户文件内容。"""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=get_settings().media_command_timeout_seconds if timeout is None else timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ConfigurationRequired("缺少 FFmpeg/FFprobe，请运行本地环境准备脚本或加入 PATH。") from exc
    except subprocess.TimeoutExpired as exc:
        raise AppError("媒体处理超时，原文件已保留，请拆分素材后重试。", "media_timeout") from exc
    if result.returncode:
        logger.warning("媒体工具失败 tool=%s exit_code=%d stderr=%s",
                       args[0], result.returncode, result.stderr[-2000:])
        raise AppError("文件无法解码，可能已损坏或编码暂不支持。", "decode_failed")
    return result.stdout


def probe(path: Path) -> dict:
    info = json.loads(
        command(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
        )
    )
    duration = float(info.get("format", {}).get("duration") or 0)
    if not duration or duration > get_settings().max_media_minutes * 60:
        raise AppError("无法读取时长或素材超过配置的时长上限，请拆分后上传。", "media_limit")
    return {
        "duration": duration,
        "audio": any(s["codec_type"] == "audio" for s in info["streams"]),
        "video": any(s["codec_type"] == "video" for s in info["streams"]),
    }


def inference(kind: str, key: str) -> dict:
    """key 在宿主机和容器中使用相同相对路径，不能传 Windows 绝对路径。"""
    if kind not in ("ocr", "asr"):
        raise ValueError("不支持的推理类型")
    settings = get_settings()
    url = settings.ocr_url if kind == "ocr" else settings.asr_url
    if not url:
        name = "OCR" if kind == "ocr" else "语音转写"
        raise ConfigurationRequired(f"未启用{name}服务，请检查 config.yaml 的 models 开关并启动容器。")
    storage_path(key)  # 调用前也校验路径，服务端将再次检查。
    request_id, started = uuid4().hex, time.monotonic()
    logger.info("推理请求 request_id=%s kind=%s storage_key=%s", request_id, kind, key)
    try:
        # GPU 锁由推理服务持有，客户端断开后仍能保护正在运行的模型子进程。
        with httpx.Client(timeout=httpx.Timeout(settings.inference_client_timeout_seconds,
                                              connect=settings.model_connect_timeout_seconds),
                          trust_env=False) as client:
            response = client.post(url.rstrip("/") + "/infer", json={"storage_key": key},
                                   headers={"X-Request-ID": request_id})
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
                raise ValueError("missing segments")
            logger.info("推理完成 request_id=%s kind=%s segments=%d elapsed=%.2fs",
                        request_id, kind, len(result["segments"]), time.monotonic() - started)
            return result
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("推理请求失败 request_id=%s kind=%s error=%s elapsed=%.2fs",
                       request_id, kind, type(exc).__name__, time.monotonic() - started)
        raise AppError(
            f"{kind.upper()} 服务不可用或处理失败，请按请求 ID {request_id} 检查容器日志。",
            "inference_failed", 502
        ) from exc


def video_frames(key: str, duration: float) -> list[tuple[str, float]]:
    settings = get_settings()
    folder = storage_path("derived/" + Path(key).stem)
    folder.mkdir(parents=True, exist_ok=True)
    count = min(settings.max_video_frames,
                max(1, int(duration // settings.video_frame_interval_seconds) + 1))
    frames = []
    for index in range(count):
        second = round((index + 0.5) * duration / count, 3)
        path = folder / f"frame-{index}.jpg"
        command(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-ss",
                str(second),
                "-i",
                str(storage_path(key)),
                "-frames:v",
                "1",
                "-vf",
                f"scale={settings.video_frame_width}:-2",
                str(path),
            ]
        )
        frames.append((path.relative_to(settings.data_dir.resolve()).as_posix(), second))
    return frames
