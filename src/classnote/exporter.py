from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Iterable

from .marked_context import build_marked_contexts, marked_contexts_markdown
from .models import CourseResult
from .paragraphs import group_segments


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned[:100] or "课堂笔记"


def export_markdown(
    result: CourseResult,
    export_dir: Path,
    topics: Iterable[tuple[int, str]] = (),
) -> Path:
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
    topic_lines = [
        f"- {format_time(start_ms)}　{' '.join(title.split())}"
        for start_ms, title in sorted(topics, key=lambda item: item[0])
        if title.strip()
    ]
    topic_section = ""
    if topic_lines:
        topic_section = "## 课堂脉络\n\n" + "\n".join(topic_lines) + "\n\n---\n\n"
    marked_section = marked_contexts_markdown(build_marked_contexts(result.segments))
    if marked_section:
        marked_section = f"{marked_section}\n\n---\n\n"
    content = (
        f"{result.notes_markdown.rstrip()}\n\n---\n\n"
        f"{topic_section}"
        f"{marked_section}"
        f"## 英中对照记录\n\n{transcript}\n"
    )
    # A failed recovery/export must never truncate the previous readable note.
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=export_dir,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    return path


def format_time(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
