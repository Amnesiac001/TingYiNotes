from __future__ import annotations

from bisect import bisect_left, bisect_right

from .models import Segment


class RecentSegmentIndex:
    """Index live subtitles by end time for bounded classroom review windows."""

    def __init__(self) -> None:
        self._source: list[Segment] | None = None
        self._seen_count = 0
        self._ordered: list[Segment] = []
        self._ends: list[int] = []

    def update(self, segments: list[Segment]) -> None:
        if segments is not self._source or len(segments) < self._seen_count:
            self._source = segments
            self._ordered = sorted(segments, key=lambda item: item.end_ms)
            self._ends = [item.end_ms for item in self._ordered]
            self._seen_count = len(segments)
            return
        for item in segments[self._seen_count:]:
            position = bisect_right(self._ends, item.end_ms)
            self._ends.insert(position, item.end_ms)
            self._ordered.insert(position, item)
        self._seen_count = len(segments)

    def recent(self, window_ms: int) -> list[Segment]:
        if not self._ordered:
            return []
        cutoff = self._ends[-1] - window_ms
        return self._ordered[bisect_left(self._ends, cutoff):]

    def clear(self) -> None:
        self._source = None
        self._seen_count = 0
        self._ordered.clear()
        self._ends.clear()
