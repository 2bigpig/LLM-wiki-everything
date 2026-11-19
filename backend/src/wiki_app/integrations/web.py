"""公网网页读取：每次重定向校验地址，并固定连接到已校验的 IP，防止 DNS 重绑定。"""

import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import quote, urljoin, urlsplit

import trafilatura

from wiki_app.config import get_settings
from wiki_app.errors import AppError
from wiki_app.modules.library.service import normalize_url


def public_address(host: str, port: int) -> str:
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(item[4][0] for item in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise AppError("出于本地数据保护，网页读取仅支持公网地址。", "unsafe_url")
        return ips[0]
    except socket.gaierror as exc:
        raise AppError("无法解析网页地址，请检查链接或稍后重试。", "fetch_failed") from exc


def fetch_html(url: str, max_bytes: int = 5_000_000) -> tuple[str, str]:
    current = normalize_url(url)
    for _ in range(5):
        parts = urlsplit(current)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        ip = public_address(parts.hostname, port)
        sock = socket.create_connection((ip, port), timeout=25)
        connection = http.client.HTTPConnection(parts.hostname, port, timeout=25)
        try:
            if parts.scheme == "https":
                sock = ssl.create_default_context().wrap_socket(
                    sock, server_hostname=parts.hostname
                )
            connection.sock = sock
            path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
            connection.request(
                "GET",
                path + ("?" + parts.query if parts.query else ""),
                headers={
                    "User-Agent": "PersonalWiki/0.1",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Encoding": "identity",
                },
            )
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                current = normalize_url(urljoin(current, response.getheader("Location", "")))
                continue
            if response.status != 200:
                raise AppError(
                    f"网页返回 HTTP {response.status}，可补充原文或上传文件。", "fetch_failed"
                )
            if not any(
                t in response.getheader("Content-Type", "")
                for t in ("text/html", "application/xhtml")
            ):
                raise AppError(
                    "此链接未返回网页正文，请下载后上传支持的文件。", "unsupported_content"
                )
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise AppError("网页超过读取大小上限，请补充需要整理的正文。", "too_large")
            encoding = response.headers.get_content_charset() or "utf-8"
            try:
                return body.decode(encoding, errors="replace"), current
            except LookupError:
                return body.decode("utf-8", errors="replace"), current
        finally:
            connection.close()
            sock.close()
    raise AppError("网页重定向次数过多，请直接提交最终链接。", "fetch_failed")


def read_webpage(url: str) -> dict:
    try:
        html, final_url = fetch_html(url)
        extracted = trafilatura.extract(
            html,
            output_format="json",
            with_metadata=True,
            include_tables=True,
            include_comments=False,
            favor_precision=True,
        )
        data = json.loads(extracted) if extracted else {}
        if len(data.get("text", "").strip()) < 80 and get_settings().browser_enabled:
            html = browser_html(final_url)
            extracted = trafilatura.extract(
                html,
                output_format="json",
                with_metadata=True,
                include_tables=True,
                include_comments=False,
                favor_precision=True,
            )
            data = json.loads(extracted) if extracted else {}
        if len(data.get("text", "").strip()) < 80:
            raise AppError(
                "网页未取得足够正文，可能需要登录或动态渲染；请补充正文或本地素材。", "partial_read"
            )
        return {
            "text": data["text"],
            "title": data.get("title") or "",
            "url": final_url,
            "coverage": "已提取可读正文；未读取评论、外链、内嵌音视频或图像含义。",
        }
    except AppError:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise AppError("网页读取失败，请检查网络、重试或补充原文。", "fetch_failed") from exc


def browser_html(url: str) -> str:
    """浏览器回退只允许访问固定公网 IP 的原始站点，阻止跨站和内网子请求。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        from wiki_app.errors import ConfigurationRequired

        raise ConfigurationRequired(
            "动态网页读取未安装 Playwright，请在本地安装 backend[browser] 扩展和 Chromium。"
        ) from exc
    parts = urlsplit(url)
    address = public_address(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    target = f"[{address}]" if ":" in address else address
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    f"--host-resolver-rules=MAP {parts.hostname} {target}, MAP * ~NOTFOUND",
                    "--disable-quic",
                ],
            )
            try:
                context = browser.new_context(service_workers="block", accept_downloads=False)

                def guard(route):
                    parsed = urlsplit(route.request.url)
                    if (
                        parsed.scheme in ("http", "https")
                        and parsed.hostname == parts.hostname
                        and parsed.port in (None, 80, 443)
                    ):
                        route.continue_()
                    else:
                        route.abort()

                context.route("**/*", guard)
                context.route_web_socket("**/*", lambda websocket: websocket.close())
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=35000)
                content = page.content()
                if len(content.encode()) > 5_000_000:
                    raise AppError("渲染后的页面超过大小上限，请补充正文。", "too_large")
                return content
            finally:
                browser.close()
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            "动态页面读取失败或跨站资源被限制，请补充正文或本地素材。", "partial_read"
        ) from exc
