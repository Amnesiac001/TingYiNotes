from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Segment:
    original_text: str
    translated_text: str = ""
    start_ms: int = 0
    end_ms: int = 0
    id: str = field(default_factory=new_id)
    marker: str = ""


@dataclass
class CourseResult:
    title: str
    subject: str
    source_path: str
    segments: list[Segment]
    notes_markdown: str
    id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=utc_now)

    @property
    def original_text(self) -> str:
        return "\n".join(s.original_text for s in self._ordered_segments()).strip()

    @property
    def translated_text(self) -> str:
        return "\n".join(s.translated_text for s in self._ordered_segments()).strip()

    def _ordered_segments(self) -> list[Segment]:
        return sorted(self.segments, key=lambda item: (item.start_ms, item.end_ms))

    @staticmethod
    def _marker_prefix(marker: str) -> str:
        return {
            "important": "[课堂手动标记：重点] ",
            "question": "[课堂手动标记：疑问] ",
        }.get(marker, "")

    @property
    def organized_original_text(self) -> str:
        return "\n".join(
            f"{self._marker_prefix(segment.marker)}{segment.original_text}"
            for segment in self._ordered_segments()
        ).strip()

    @property
    def organized_translated_text(self) -> str:
        return "\n".join(
            f"{self._marker_prefix(segment.marker)}{segment.translated_text}"
            for segment in self._ordered_segments()
            if segment.translated_text.strip()
        ).strip()
