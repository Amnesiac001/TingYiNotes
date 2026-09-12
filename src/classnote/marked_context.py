from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from .models import Segment


MARKER_LABELS = {"important": "⭐ 重点", "question": "❓ 疑问"}


@dataclass(frozen=True)
class MarkedContext:
    marker: str
    anchor_id: str
    start_ms: int
    end_ms: int
    english: str
    chinese: str
    pending_english: str

    @property
    def label(self) -> str:
        return MARKER_LABELS[self.marker]


def build_marked_contexts(
    segments: list[Segment], before_ms: int = 20_000, after_ms: int = 20_000
) -> list[MarkedContext]:
    """Expand each manual marker into nearby saved subtitles, without model calls."""
    if not any(item.marker in MARKER_LABELS for item in segments):
        return []
    ordered = sorted(segments, key=lambda item: (item.start_ms, item.end_ms))
    starts = [item.start_ms for item in ordered]
    prefix_max_ends: list[int] = []
    latest_end = 0
    for item in ordered:
        latest_end = max(latest_end, item.end_ms)
        prefix_max_ends.append(latest_end)
    contexts: list[MarkedContext] = []
    for index, anchor in enumerate(ordered):
        if anchor.marker not in MARKER_LABELS:
            continue
        if anchor.end_ms > anchor.start_ms:
            window_start = max(0, anchor.start_ms - before_ms)
            window_end = anchor.end_ms + after_ms
            first = bisect_left(prefix_max_ends, window_start)
            last = bisect_right(starts, window_end)
            nearby = [
                item for item in ordered[first:last]
                if item.end_ms > item.start_ms
                and item.end_ms >= window_start
                and item.start_ms <= window_end
            ]
        else:
            # Older imported records may have no usable timestamps.
            nearby = ordered[max(0, index - 2):index + 3]
        if anchor not in nearby:
            nearby.append(anchor)
            nearby.sort(key=lambda item: (item.start_ms, item.end_ms))
        contexts.append(
            MarkedContext(
                marker=anchor.marker,
                anchor_id=anchor.id,
                start_ms=nearby[0].start_ms,
                end_ms=max(item.end_ms for item in nearby),
                english=" ".join(item.original_text.strip() for item in nearby if item.original_text.strip()),
                chinese=" ".join(item.translated_text.strip() for item in nearby if item.translated_text.strip()),
                pending_english=" ".join(
                    item.original_text.strip()
                    for item in nearby
                    if item.original_text.strip() and not item.translated_text.strip()
                ),
            )
        )
    return contexts


def marked_contexts_markdown(contexts: list[MarkedContext]) -> str:
    if not contexts:
        return ""
    def stamp(milliseconds: int) -> str:
        seconds = max(0, milliseconds // 1000)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    blocks = []
    for context in contexts:
        block = (
            f"### {context.label} · {stamp(context.start_ms)}–{stamp(context.end_ms)}\n\n"
            f"**English**\n\n{context.english}"
        )
        if context.chinese:
            block += f"\n\n**中文**\n\n{context.chinese}"
        blocks.append(block)
    return "## 课堂手动标记与前后语境\n\n" + "\n\n---\n\n".join(blocks)
