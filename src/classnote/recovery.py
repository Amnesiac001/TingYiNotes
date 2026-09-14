from __future__ import annotations

from pathlib import Path
from typing import Callable

from .config import Settings
from .courseware import relevant_terms
from .exporter import export_markdown
from .models import CourseResult, Segment
from .services import TextProcessor, create_text_processor
from .storage import CourseRepository
from .usage import bind_course_usage


Progress = Callable[[str], None]


def recover_course(
    course_id: str,
    repository: CourseRepository,
    settings: Settings | None = None,
    progress: Progress | None = None,
    text_processor: TextProcessor | None = None,
) -> tuple[CourseResult, Path]:
    """Resume failed translations, rebuild notes, and refresh the exported Markdown."""
    notify = progress or (lambda _: None)
    current_settings = settings or Settings.load()
    row = repository.get_course(course_id)
    if row is None:
        raise RuntimeError("课程记录不存在，可能已经被删除。")
    if str(row["status"]) in {"recording", "transcribing", "translating", "organizing"}:
        raise RuntimeError("课堂仍在录音或处理，请结束并等待收尾完成后再补译。")
    saved = repository.get_course_segments(course_id)
    if not saved:
        raise RuntimeError("这条课程没有可恢复的英文字幕。请重新开始课堂或导入原录音。")

    pending = repository.pending_segments(course_id)
    saved_draft = (
        str(row["notes_markdown"]).strip()
        if not pending and int(row["notes_draft_ready"]) else ""
    )
    processor = None
    if not saved_draft:
        processor = text_processor or create_text_processor(
            current_settings.text_provider,
            current_settings.text_model,
            current_settings.text_api_key,
            current_settings.text_base_url,
        )
        bind_course_usage(processor, repository, course_id, current_settings.text_provider)
    remembered_terms = repository.get_subject_terms(str(row["subject"]))
    if pending:
        repository.set_course_state(course_id, "translating")
    failures: list[str] = []
    consecutive_failures = 0
    stopped_early = False
    for index, item in enumerate(pending, start=1):
        segment_id = str(item["id"])
        notify(f"正在补译课堂内容…… {index}/{len(pending)}")
        repository.set_translation_state(segment_id, "translating")
        try:
            assert processor is not None
            translated = processor.translate(
                str(item["original_text"]), str(row["subject"]),
                relevant_terms(str(item["original_text"]), remembered_terms),
            ).strip()
            if not translated:
                raise RuntimeError("翻译服务没有返回内容。")
            repository.set_translation_state(segment_id, "completed", translated)
            consecutive_failures = 0
        except Exception as exc:
            repository.set_translation_state(segment_id, "failed", error=str(exc))
            failures.append(str(exc))
            consecutive_failures += 1
            if consecutive_failures >= 2:
                stopped_early = True
                break

    if failures:
        remaining = len(repository.pending_segments(course_id))
        message = (
            f"连续 2 句补译失败，已暂停后续请求；仍有 {remaining} 句待补译。"
            if stopped_early else f"仍有 {remaining} 句待补译。"
        )
        message += f"请检查网络、密钥和模型后重试。最近错误：{failures[-1][:200]}"
        repository.set_course_state(course_id, "needs_attention", message)
        raise RuntimeError(message)

    refreshed = repository.get_course_segments(course_id)
    segments = [
        Segment(
            original_text=str(item["original_text"]),
            translated_text=str(item["translated_text"]),
            start_ms=int(item["start_ms"]),
            end_ms=int(item["end_ms"]),
            id=str(item["id"]),
            marker=str(item["marker"] or ""),
        )
        for item in refreshed
    ]
    result = CourseResult(
        title=str(row["title"]),
        subject=str(row["subject"]),
        source_path=str(row["source_path"]),
        segments=segments,
        notes_markdown="",
        id=str(row["id"]),
        created_at=str(row["created_at"]),
    )
    notify(
        "已找到保存的课堂笔记，正在重新导出……" if saved_draft
        else "翻译已补齐，正在重新整理课堂笔记……"
    )
    repository.set_course_state(course_id, "organizing")
    try:
        if saved_draft:
            result.notes_markdown = saved_draft
        else:
            assert processor is not None
            result.notes_markdown = processor.organize(
                result.title,
                result.subject,
                result.organized_original_text,
                result.organized_translated_text,
            )
            repository.save_notes_draft(course_id, result.notes_markdown)
        topics = [
            (int(topic["start_ms"]), str(topic["title"]))
            for topic in repository.get_course_topics(course_id)
        ]
        path = export_markdown(result, current_settings.export_dir, topics).resolve()
        repository.finalize_course(course_id, result.notes_markdown, str(path))
        notify("补译和课堂整理已完成。")
        return result, path
    except Exception as exc:
        repository.set_course_state(course_id, "needs_attention", f"重新整理失败：{exc}")
        raise
