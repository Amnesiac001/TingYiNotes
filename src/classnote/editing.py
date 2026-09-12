from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .exporter import export_markdown
from .models import CourseResult, Segment
from .storage import CourseRepository


@dataclass(frozen=True)
class CorrectionResult:
    changed: bool
    export_path: Path | None = None
    export_error: str = ""


def correct_course_segment(
    repository: CourseRepository,
    course_id: str,
    segment_id: str,
    original_text: str,
    translated_text: str,
    expected_original: str,
    expected_translation: str,
    settings: Settings | None = None,
    keep_translation_confirmed: bool = False,
) -> CorrectionResult:
    """Save a correction without an API call, then refresh the local transcript export."""
    changed = repository.correct_segment(
        course_id,
        segment_id,
        original_text,
        translated_text,
        expected_original,
        expected_translation,
        keep_translation_confirmed,
    )
    if not changed:
        return CorrectionResult(False)

    return _refresh_corrected_export(
        repository, course_id, settings,
        "字幕已人工校对，整理笔记可能过时；请在课程库重新整理。",
    )


def undo_course_segment_correction(
    repository: CourseRepository,
    course_id: str,
    segment_id: str,
    expected_original: str,
    expected_translation: str,
    settings: Settings | None = None,
) -> CorrectionResult:
    changed = repository.undo_last_segment_correction(
        course_id, segment_id, expected_original, expected_translation
    )
    if not changed:
        return CorrectionResult(False)
    return _refresh_corrected_export(
        repository, course_id, settings,
        "字幕校对已撤销，整理笔记可能过时；请在课程库重新整理。",
    )


def _refresh_corrected_export(
    repository: CourseRepository,
    course_id: str,
    settings: Settings | None,
    status_message: str,
) -> CorrectionResult:
    row = repository.get_course(course_id)
    if row is None:
        raise RuntimeError("课程记录不存在，可能已被删除。")
    segments = [
        Segment(
            original_text=str(item["original_text"]),
            translated_text=str(item["translated_text"]),
            start_ms=int(item["start_ms"]),
            end_ms=int(item["end_ms"]),
            id=str(item["id"]),
            marker=str(item["marker"] or ""),
        )
        for item in repository.get_course_segments(course_id)
    ]
    previous_notes = str(row["notes_markdown"] or "").strip()
    warning = "> 校对提示：下方英中记录已更新；原 AI 整理笔记尚未重新生成。请在课程库重新整理。"
    result = CourseResult(
        title=str(row["title"]),
        subject=str(row["subject"]),
        source_path=str(row["source_path"]),
        segments=segments,
        notes_markdown=f"{warning}\n\n{previous_notes}" if previous_notes else warning,
        id=str(row["id"]),
        created_at=str(row["created_at"]),
    )
    topics = [
        (int(item["start_ms"]), str(item["title"]))
        for item in repository.get_course_topics(course_id)
    ]
    try:
        path = export_markdown(result, (settings or Settings.load()).export_dir, topics).resolve()
    except Exception as exc:
        return CorrectionResult(True, export_error=str(exc))
    repository.set_course_state(
        course_id,
        "needs_attention",
        status_message,
        str(path),
    )
    return CorrectionResult(True, path)
