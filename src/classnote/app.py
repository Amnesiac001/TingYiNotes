from __future__ import annotations

from pathlib import Path
import sys

from openai import OpenAI

from .config import Settings
from .courseware import CourseContext, merge_subject_context
from .exporter import export_markdown
from .models import CourseResult
from .services import (
    CoursePipeline,
    DemoTextProcessor,
    DemoTranscriber,
    LocalWhisperTranscriber,
    MLXWhisperTranscriber,
    OpenAITranscriber,
    ProgressCallback,
    create_text_processor,
)
from .storage import CourseRepository


SUPPORTED_SUFFIXES = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"}


def process_course(
    audio_path: Path,
    title: str,
    subject: str,
    terms: dict[str, str] | None = None,
    demo: bool = False,
    progress: ProgressCallback | None = None,
) -> tuple[CourseResult, Path]:
    settings = Settings.load()
    settings.ensure_directories()
    if not demo and audio_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"不支持的文件格式：{audio_path.suffix or '无扩展名'}")

    if demo:
        pipeline = CoursePipeline(DemoTranscriber(), DemoTextProcessor(), progress)
    else:
        client = OpenAI(api_key=settings.api_key) if settings.api_key else None
        if settings.live_mode == "local":
            transcriber = (
                MLXWhisperTranscriber(settings.mac_transcription_model)
                if sys.platform == "darwin"
                else LocalWhisperTranscriber(
                    settings.local_transcription_model,
                    settings.local_compute_type,
                )
            )
        else:
            if client is None:
                raise RuntimeError("云端语音转写需要 OPENAI_API_KEY；也可以在设置中改用本地识别。")
            transcriber = OpenAITranscriber(client, settings.transcription_model)
        pipeline = CoursePipeline(
            transcriber,
            create_text_processor(
                settings.text_provider,
                settings.text_model,
                settings.text_api_key,
                settings.text_base_url,
                client if settings.text_provider == "openai" else None,
            ),
            progress,
            usage_provider=settings.text_provider,
        )

    repository = CourseRepository(settings.database_path)
    remembered = repository.get_subject_terms(subject)
    merged_terms = merge_subject_context(CourseContext(terms=terms or {}), remembered).terms
    result = pipeline.run_incremental(audio_path, title, subject, repository, merged_terms)
    try:
        exported_path = export_markdown(result, settings.export_dir).resolve()
        repository.finalize_course(result.id, result.notes_markdown, str(exported_path))
    except Exception as exc:
        repository.set_course_state(result.id, "needs_attention", f"导出失败：{exc}")
        raise
    return result, exported_path.resolve()
