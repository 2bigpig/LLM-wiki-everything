"""从 Paddle 官方 BOS 源预下载模型，缓存布局依据 PaddleX 3.3 的模型管理器。"""

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path


def prepare() -> list[dict]:
    root = Path("/models")
    if os.getuid() == 0:
        os.chown(root, 10001, 10001)
        os.setgid(10001)
        os.setuid(10001)
    results = []
    for name in ("PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec"):
        target = root / "paddlex" / "official_models" / name
        if not (target / "inference.pdiparams").is_file():
            if target.exists():
                raise RuntimeError(f"已有不完整模型目录，请核对后重试：{target}")
            url = ("https://paddle-model-ecology.bj.bcebos.com/paddlex/"
                   f"official_inference_model/paddle3.0.0/{name}_infer.tar")
            with tempfile.TemporaryDirectory(dir=root) as folder:
                temporary = Path(folder)
                archive = temporary / "model.tar"
                with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as output:
                    while block := response.read(1024 * 1024):
                        output.write(block)
                unpacked = temporary / "unpacked"
                unpacked.mkdir()
                with tarfile.open(archive) as package:
                    package.extractall(unpacked, filter="data")
                weights = list(unpacked.rglob("inference.pdiparams"))
                if len(weights) != 1 or not (weights[0].parent / "inference.json").is_file():
                    raise RuntimeError(f"官方模型归档结构不符合预期：{name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(weights[0].parent), str(target))
        weight = target / "inference.pdiparams"
        results.append({"model": name, "path": str(target), "bytes": weight.stat().st_size,
                        "sha256": hashlib.file_digest(weight.open("rb"), "sha256").hexdigest()})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    return results


if __name__ == "__main__":
    prepare()
