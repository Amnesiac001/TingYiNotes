from __future__ import annotations

import re
from pathlib import Path

from .models import CourseResult
from .paragraphs import group_segments


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned[:100] or "课堂笔记"


def export_markdown(result: CourseResult, export_dir: Path) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"{safe_filename(result.title)}-{result.id[:8]}.md"
    blocks = []
    for paragraph in group_segments(result.segments):
        first, last = paragraph[0], paragraph[-1]
        english = " ".join(segment.original_text.strip() for segment in paragraph)
        chinese = " ".join(
            segment.translated_text.strip() for segment in paragraph if segment.translated_text.strip()
        )
        markers = {segment.marker for segment in paragraph}
        marker_text = ""
        if "important" in markers:
            marker_text += " · ⭐ 重点"
        if "question" in markers:
            marker_text += " · ❓ 疑问"
        block = (
            f"### {format_time(first.start_ms)}–{format_time(last.end_ms)} · {len(paragraph)} 句{marker_text}\n\n"
            f"**English**\n\n{english}"
        )
        if chinese:
            block += f"\n\n**中文**\n\n{chinese}"
        blocks.append(block)
    transcript = "\n\n---\n\n".join(blocks)
    content = (
        f"{result.notes_markdown.rstrip()}\n\n---\n\n"
        f"## 英中对照记录\n\n{transcript}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def format_time(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
