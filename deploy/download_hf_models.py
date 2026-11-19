"""用轻量 Docker 容器预下载公开权重，缓存布局与正式推理镜像一致。"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path


def download(kind: str) -> dict:
    """模型卷以应用 UID 写入，避免预下载后正式容器无法维护缓存。"""
    root = Path("/models")
    if os.getuid() == 0:
        os.chown(root, 10001, 10001)
        os.setgid(10001)
        os.setuid(10001)
    os.environ.setdefault("HF_HOME", "/models/hf")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from huggingface_hub import HfApi, snapshot_download

    repo = "Systran/faster-whisper-medium" if kind == "asr" else "BAAI/bge-m3"
    cache = "/models/whisper" if kind == "asr" else "/models/hf/hub"
    info = HfApi().model_info(repo, token=False)
    names = {file.rfilename for file in info.siblings}
    if kind == "asr":
        patterns = ["*.json", "*.bin", "*.txt", "*.model"]
    else:
        weight = "model.safetensors" if "model.safetensors" in names else "pytorch_model.bin"
        patterns = [weight, "*.json", "*.model", "1_Pooling/*", "2_Normalize/*"]
    snapshot = Path(snapshot_download(repo, cache_dir=cache, allow_patterns=patterns,
                                      max_workers=2, token=False))
    weights = []
    for path in sorted(snapshot.rglob("*")):
        if path.is_file() and path.suffix in (".bin", ".safetensors"):
            digest = hashlib.sha256()
            with path.open("rb") as source:
                while block := source.read(4 * 1024 * 1024):
                    digest.update(block)
            sha256 = digest.hexdigest()
            blob = path.resolve().name
            if re.fullmatch(r"[a-f0-9]{64}", blob) and sha256 != blob:
                raise RuntimeError(f"权重校验失败：{path.name}")
            weights.append({"file": path.name, "bytes": path.stat().st_size, "sha256": sha256})
    if not weights:
        raise RuntimeError("未找到模型权重，下载结果不完整")
    return {"model": repo, "snapshot": str(snapshot), "revision": snapshot.name, "weights": weights}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["asr", "embeddings"])
    arguments = parser.parse_args()
    print(json.dumps(download(arguments.kind), ensure_ascii=False), flush=True)
