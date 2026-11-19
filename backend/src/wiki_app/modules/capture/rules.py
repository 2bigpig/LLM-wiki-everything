"""确定性识别只解析输入，不联网、不调用模型，也不执行用户文本中的指令。"""

import hashlib
import re
from urllib.parse import parse_qs, urlsplit

from wiki_app.modules.library.service import normalize_url

URL_PATTERN = re.compile(r"(?:https?://|www\.)[^\s<>\"\u3000]+", re.IGNORECASE)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def link_identity(value: str) -> tuple[str, str]:
    """平台内容 ID 不含分享跟踪参数；Bilibili 分集保留，避免错误合并。"""
    url = normalize_url(value)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    query = parse_qs(parts.query)
    segments = parts.path.strip("/").split("/")
    if host == "youtu.be" and segments[0]:
        return "youtube:" + segments[0], url
    if host == "youtube.com":
        video = query.get("v", [""])[0] if parts.path == "/watch" else ""
        if len(segments) > 1 and segments[0] in ("shorts", "embed"):
            video = segments[1]
        if video:
            return "youtube:" + video, url
    if host == "bilibili.com" and len(segments) > 1 and segments[0] == "video":
        return "bilibili:" + segments[1] + ":p=" + query.get("p", ["1"])[0], url
    return "url:" + url, url


def recognize(text: str) -> dict:
    """保留完整原文，由位置剔除链接得到可分析文字；无效链接仅提示、不访问。"""
    from wiki_app.errors import AppError

    links, warnings, seen = [], [], set()
    body = []
    end = 0
    for match in URL_PATTERN.finditer(text):
        body.append(text[end:match.start()])
        end = match.end()
        raw = match.group().rstrip("。，；！？、,;!?'”’】》")
        while raw.endswith(")") and raw.count(")") > raw.count("("):
            raw = raw[:-1]
        raw = raw.rstrip("）")
        value = "https://" + raw if raw.lower().startswith("www.") else raw
        try:
            if len(value) > 4096:
                raise AppError("链接过长。")
            identity, url = link_identity(value)
        except AppError:
            warnings.append("有链接格式无效或含凭证，未安排自动读取；原始输入仍会保留。")
            continue
        if identity in seen:
            continue
        seen.add(identity)
        host = urlsplit(url).hostname or ""
        kind = "video" if identity.startswith(("youtube:", "bilibili:")) or host in ("b23.tv", "youtu.be") else "web"
        links.append({"url": url, "kind": kind, "identity": identity})
    body.append(text[end:])
    readable = "".join(body).strip()
    if not re.search(r"\w", readable):
        readable = ""
    return {"links": links, "body": readable, "warnings": list(dict.fromkeys(warnings))}
