"""重型依赖按任务加载，stdout 只输出 JSON，模型日志定向到 stderr。"""

import contextlib
import json
import sys

from wiki_app.config import get_settings
from wiki_app.integrations.storage import storage_path


def create_asr_model():
    """下载和推理共用构造参数，确保使用同一个 Docker 卷缓存。"""
    from faster_whisper import WhisperModel

    settings = get_settings()
    return WhisperModel(
        settings.asr_model,
        device=settings.inference_device,
        compute_type=settings.asr_compute_type,
        download_root=settings.asr_cache_dir,
    )


def transcribe(key: str) -> dict:
    settings = get_settings()
    model = create_asr_model()
    segments, info = model.transcribe(
        str(storage_path(key)), vad_filter=settings.asr_vad_filter, beam_size=settings.asr_beam_size,
        condition_on_previous_text=settings.asr_condition_on_previous_text,
    )
    return {
        "segments": [
            {"text": item.text.strip(), "start": item.start, "end": item.end, "speaker": None}
            for item in segments
            if item.text.strip()
        ],
        "language": info.language,
        "model": settings.asr_model,
    }


def create_ocr_engine():
    """只启用 OCR 必需的两个 mobile 模型，缓存目录由容器环境指定。"""
    from paddleocr import PaddleOCR

    settings = get_settings()
    return PaddleOCR(
        device="gpu:0" if settings.inference_device == "cuda" else "cpu",
        text_detection_model_name=settings.ocr_detection_model,
        text_recognition_model_name=settings.ocr_recognition_model,
        use_doc_orientation_classify=settings.ocr_doc_orientation,
        use_doc_unwarping=settings.ocr_doc_unwarping,
        use_textline_orientation=settings.ocr_textline_orientation,
    )


def ocr(key: str) -> dict:
    engine = create_ocr_engine()
    segments = []
    for prediction in engine.predict(str(storage_path(key))):
        raw = prediction.json
        if callable(raw):
            raw = raw()
        if isinstance(raw, str):
            raw = json.loads(raw)
        data = raw.get("res", raw)
        texts, boxes, scores = (
            data.get("rec_texts", []),
            data.get("rec_polys", []),
            data.get("rec_scores", []),
        )
        for index, value in enumerate(texts):
            if value.strip():
                box = boxes[index] if index < len(boxes) else None
                segments.append(
                    {
                        "text": value,
                        "bbox": box.tolist() if hasattr(box, "tolist") else box,
                        "confidence": float(scores[index]) if index < len(scores) else None,
                    }
                )
    settings = get_settings()
    return {"segments": segments,
            "model": f"{settings.ocr_detection_model}+{settings.ocr_recognition_model}"}


if __name__ == "__main__":
    kind, key = sys.argv[1:]
    with contextlib.redirect_stdout(sys.stderr):
        result = ocr(key) if kind == "ocr" else transcribe(key)
    print(json.dumps(result, ensure_ascii=False))
