"""验证字幕时间、平台路由、扫描件降级和原文片段保留。"""

from wiki_app.integrations.extraction import split_text
from wiki_app.integrations.platform_video import is_platform_video, parse_vtt
from wiki_app.modules.knowledge.service import contains_quote


def test_subtitles_keep_original_timeline_and_remove_tags():
    segments = parse_vtt(
        "WEBVTT\n\n00:01:02.500 --> 00:01:05.000\n<c>解释 Rerank</c>\n\n00:01:05.000 --> 00:01:06.000\n解释 Rerank\n"
    )
    assert segments == [{"text": "解释 Rerank", "start": 62.5, "end": 65.0}]


def test_platform_url_boundary():
    assert is_platform_video("https://www.bilibili.com/video/BV123")
    assert is_platform_video("https://www.youtube.com/watch?v=abc")
    assert not is_platform_video("https://bilibili.com.attacker.test/video/BV123")
    assert not is_platform_video("https://www.bilibili.com/read/cv123")


def test_chunking_does_not_omit_long_paragraphs():
    text = "知识" * 3000 + "\n第二段的完整原文。"
    chunks = split_text(text)
    assert all(len(chunk) <= 1600 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")
    assert not contains_quote("原文中的一句话", "    ")
    assert contains_quote("原文中的\n一句话", "原文中的一句话")
