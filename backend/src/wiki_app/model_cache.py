"""在 Docker 持久化卷中预下载并加载模型，使用与正式推理相同的缓存。"""

import argparse
import contextlib
import json
import sys
from importlib.metadata import version

from wiki_app.config import get_settings


def prepare(kind: str) -> dict:
    """构造模型即完成首次下载和加载；命令退出后释放显存。"""
    settings = get_settings()
    if kind == "ocr":
        import paddle

        from wiki_app.inference_task import create_ocr_engine

        engine = create_ocr_engine()
        return {"kind": kind, "models": [settings.ocr_detection_model, settings.ocr_recognition_model],
                "cache": settings.ocr_cache_dir, "device": paddle.device.get_device(),
                "paddleocr": version("paddleocr"), "paddle": paddle.__version__,
                "loaded": engine is not None}
    if kind == "asr":
        from faster_whisper.vad import get_vad_model

        from wiki_app.inference_task import create_asr_model

        model = create_asr_model()
        vad = get_vad_model()
        return {"kind": kind, "model": get_settings().asr_model,
                "cache": settings.asr_cache_dir, "device": str(model.model.device),
                "compute_type": str(model.model.compute_type),
                "faster_whisper": version("faster-whisper"), "vad_loaded": vad is not None}
    # 后端只做 HTTP 联通验证；权重下载/加载由 bge 容器 --prepare 完成。
    from wiki_app.modules.search.service import embed

    vector = embed(["检索与知识整理"])
    return {"kind": kind, "model": get_settings().embedding_model,
            "url": get_settings().embedding_url, "dimensions": len(vector[0]), "device": "cpu"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="预下载并加载 Docker 卷中的推理模型")
    parser.add_argument("kind", choices=["ocr", "asr", "embeddings"])
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        result = prepare(args.kind)
    print(json.dumps(result, ensure_ascii=False))
