"""读取原件并生成带位置的证据片段；未取得的内容明确记录为缺失。"""

from collections import defaultdict
from zipfile import ZipFile

import pdfplumber
from docx import Document
from PIL import Image

from wiki_app.config import get_settings
from wiki_app.errors import AppError, ConfigurationRequired
from wiki_app.integrations.media import inference, probe, video_frames
from wiki_app.integrations.platform_video import is_platform_video, read_platform_video
from wiki_app.integrations.storage import storage_path
from wiki_app.integrations.web import read_webpage
from wiki_app.models import Attachment, Record


def split_text(text: str, size: int | None = None) -> list[str]:
    """优先按换行切分，超长段落切片；保留原文表达供引用核对。"""
    size = get_settings().chunk_chars if size is None else size
    result, current = [], ""
    for line in text.replace("\r\n", "\n").splitlines(keepends=True):
        for offset in range(0, len(line), size):
            part = line[offset : offset + size]
            if current and len(current) + len(part) > size:
                result.append(current.strip())
                current = ""
            current += part
    if current.strip():
        result.append(current.strip())
    return [part for part in result if part]


def extract_attachment(attachment: Attachment) -> tuple[list[dict], list[str]]:
    settings = get_settings()
    path = storage_path(attachment.storage_key)
    suffix = path.suffix.lower()
    pieces, notices = [], []

    def add(text: str, method: str, **locator):
        for chunk in split_text(text):
            pieces.append(
                {
                    "text": chunk,
                    "method": method,
                    "locator": {
                        "attachment_id": attachment.id,
                        "filename": attachment.filename,
                        **locator,
                    },
                }
            )

    if suffix in (".txt", ".md"):
        if path.stat().st_size > settings.text_max_bytes:
            raise AppError(f"文本文件超过 {settings.text_max_bytes} 字节上限，请拆分后上传。", "text_limit")
        try:
            add(path.read_text(encoding="utf-8-sig"), "text")
        except UnicodeDecodeError:
            add(path.read_text(encoding="gb18030"), "text")
    elif suffix == ".docx":
        with ZipFile(path) as archive:
            if (
                len(archive.infolist()) > settings.docx_max_entries
                or sum(item.file_size for item in archive.infolist()) > settings.docx_max_unpacked_bytes
            ):
                raise AppError("DOCX 解压后超过安全处理上限，请拆分文件。", "document_limit")
        document = Document(path)
        for index, paragraph in enumerate(document.paragraphs, 1):
            add(paragraph.text, "docx", paragraph=index)
        for index, table in enumerate(document.tables, 1):
            add(
                "\n".join(" | ".join(c.text for c in row.cells) for row in table.rows),
                "docx",
                table=index,
            )
        notices.append("DOCX 已读取段落和表格，未分析嵌入图片、公式或批注。")
    elif suffix == ".pdf":
        last_error = None
        with pdfplumber.open(path) as document:
            if len(document.pages) > settings.pdf_max_pages:
                notices.append(f"PDF 超过单次 {settings.pdf_max_pages} 页上限，仅读取前 {settings.pdf_max_pages} 页。")
            for page_number, page in enumerate(document.pages[:settings.pdf_max_pages], 1):
                text = page.extract_text() or ""
                if text.strip():
                    add(text, "pdf_text", page=page_number)
                if len(text.strip()) < settings.pdf_ocr_min_chars:
                    try:
                        folder = storage_path("derived/" + attachment.id)
                        folder.mkdir(parents=True, exist_ok=True)
                        image_path = folder / f"page-{page_number}.png"
                        page.to_image(resolution=settings.pdf_render_dpi).save(image_path)
                        image_key = image_path.relative_to(
                            get_settings().data_dir.resolve()
                        ).as_posix()
                        for item in inference("ocr", image_key)["segments"]:
                            add(item["text"], "pdf_ocr", page=page_number, bbox=item.get("bbox"))
                    except AppError as exc:
                        last_error = exc
                        notices.append(f"PDF 第 {page_number} 页 OCR 未完成：{exc.message}")
        if not pieces and last_error:
            raise last_error
        notices.append("PDF 已提取文字；图表、公式和复杂版式的含义未单独分析。")
    elif attachment.media_type.startswith("image/"):
        with Image.open(path) as image:
            if image.width * image.height > settings.image_max_pixels:
                raise AppError(f"图片超过 {settings.image_max_pixels} 像素上限，请缩小后上传。", "image_limit")
            image.verify()
        for item in inference("ocr", attachment.storage_key)["segments"]:
            add(item["text"], "ocr", bbox=item.get("bbox"))
        notices.append("图片仅完成文字识别，未分析非文字画面含义。")
    else:
        info = probe(path)
        if info["audio"]:
            for item in inference("asr", attachment.storage_key)["segments"]:
                add(
                    item["text"],
                    "asr",
                    start=item["start"],
                    end=item["end"],
                    speaker=item.get("speaker"),
                )
            notices.append("已转写音轨并保留原片时间位置；未确认说话人身份。")
        if info["video"]:
            frames = video_frames(attachment.storage_key, info["duration"])
            succeeded = 0
            for frame_key, second in frames:
                try:
                    for item in inference("ocr", frame_key)["segments"]:
                        add(
                            item["text"],
                            "video_ocr",
                            start=second,
                            frame=frame_key,
                            bbox=item.get("bbox"),
                        )
                    succeeded += 1
                except AppError as exc:
                    notices.append(exc.message)
                    break
            notices.append(
                f"视频仅采样 {succeeded}/{len(frames)} 个画面做 OCR，未覆盖完整画面或操作含义。"
            )
    return pieces, notices


def extract_record(record: Record, attachments: list[Attachment]) -> tuple[list[dict], dict]:
    pieces, notices, failures = [], [], []
    missing_configuration = False
    ordinals: dict[str, int] = defaultdict(int)

    def append(origin: str, raw: list[dict]):
        for piece in raw:
            if piece["text"].strip():
                pieces.append({**piece, "origin": origin, "ordinal": ordinals[origin]})
                ordinals[origin] += 1

    if record.content.strip():
        append(
            "body",
            [
                {"text": s, "method": "user_text", "locator": {"kind": "original_text"}}
                for s in split_text(record.content)
            ],
        )
    if record.notes.strip():
        append(
            "notes",
            [
                {"text": s, "method": "user_note", "locator": {"kind": "personal_note"}}
                for s in split_text(record.notes)
            ],
        )
    if record.url:
        try:
            if is_platform_video(record.url):
                segments, downloaded = read_platform_video(record.url, record.id, record.version)
                append(
                    "platform_subtitles",
                    [
                        {
                            "text": item["text"],
                            "method": "platform_subtitles",
                            "locator": {
                                "url": record.url,
                                "start": item["start"],
                                "end": item["end"],
                            },
                        }
                        for item in segments
                    ],
                )
                if downloaded:
                    from wiki_app.models import new_id

                    downloaded.id = new_id()
                    attachments.append(downloaded)
                else:
                    notices.append(
                        "复用平台字幕（可能为自动字幕），未分析视频画面；时间位置对应原片。"
                    )
            else:
                web = read_webpage(record.url)
                append(
                    "web",
                    [
                        {"text": s, "method": "web", "locator": {"url": web["url"]}}
                        for s in split_text(web["text"])
                    ],
                )
                notices.append(web["coverage"])
        except AppError as exc:
            failures.append(exc.message)
            missing_configuration |= isinstance(exc, ConfigurationRequired)
    for attachment in attachments:
        try:
            extracted, warnings = extract_attachment(attachment)
            append(attachment.id, extracted)
            notices.extend(warnings)
            if not extracted:
                failures.append(f"{attachment.filename} 未识别到可读文字或语音。")
        except AppError as exc:
            failures.append(f"{attachment.filename}：{exc.message}")
            missing_configuration |= isinstance(exc, ConfigurationRequired)
        except Exception:
            failures.append(f"{attachment.filename} 读取失败，文件可能损坏或格式暂不支持。")
    if not pieces:
        if missing_configuration:
            raise ConfigurationRequired("；".join(failures))
        raise AppError(
            "；".join(failures) or "未取得可分析的文字或语音，请补充素材。", "read_failed"
        )
    return pieces, {
        "status": "partial" if failures or notices else "complete",
        "details": list(dict.fromkeys(notices + failures)),
        "source_count": len(pieces),
    }
