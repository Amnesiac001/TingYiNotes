from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterator, Protocol

from openai import OpenAI

from .models import CourseResult, Segment


ProgressCallback = Callable[[str], None]


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, subject: str) -> list[Segment]: ...


class TextProcessor(Protocol):
    def translate(self, text: str, subject: str, terms: dict[str, str]) -> str: ...

    def organize(
        self, title: str, subject: str, original: str, translation: str
    ) -> str: ...

    def translate_stream(
        self, text: str, subject: str, terms: dict[str, str]
    ) -> Iterator[str]: ...

    def summarize_live(
        self,
        previous: str,
        title: str,
        subject: str,
        original: str,
        translation: str,
    ) -> str: ...


def _live_summary_request(
    previous: str,
    title: str,
    subject: str,
    original: str,
    translation: str,
) -> tuple[str, str]:
    instructions = """你是课堂实时摘要器。把上一版摘要与最近已确认的课堂字幕合并为新的滚动摘要。
只能依据输入内容，不得臆测；忽略未完成的句子；保留技术名词、数字和限定条件。
只返回合法 JSON，不要 Markdown、解释或代码围栏，格式必须为：
{"topic":"当前主题的一句话概括","key_points":["要点"],"terms":["英文术语：中文含义"],"questions":["仍待解释或值得复习的问题"]}
topic 不超过 45 个汉字；key_points 最多 6 条；terms 最多 8 条；questions 最多 5 条。
没有可靠内容的字段返回空字符串或空数组。"""
    payload = (
        f"课程名称：{title}\n课程领域：{subject}\n\n"
        f"上一版滚动摘要：\n{previous or '{}'}\n\n"
        f"最近英文字幕：\n{original}\n\n最近中文翻译：\n{translation}"
    )
    return instructions, payload


class OpenAITranscriber:
    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    def transcribe(self, audio_path: Path, subject: str) -> list[Segment]:
        prompt = (
            f"This is an English university lecture about {subject}. "
            "Preserve technical terms, names, numbers, formulas, and code exactly."
        )
        with audio_path.open("rb") as audio_file:
            result = self.client.audio.transcriptions.create(
                model=self.model,
                file=audio_file,
                language="en",
                prompt=prompt,
            )
        text = getattr(result, "text", "").strip()
        if not text:
            raise RuntimeError("转写服务没有返回文本。")
        return [Segment(original_text=text)]


class LocalWhisperTranscriber:
    """Transcribe imported files on the local NVIDIA GPU."""

    def __init__(self, model: str, compute_type: str = "float16"):
        self.model = model
        self.compute_type = compute_type

    def transcribe(self, audio_path: Path, subject: str) -> list[Segment]:
        # Imported lazily so demo mode and cloud mode can still start without CUDA.
        from .local_live import _prepare_nvidia_dlls

        _prepare_nvidia_dlls()
        from faster_whisper import WhisperModel

        whisper = WhisperModel(
            self.model,
            device="cuda",
            compute_type=self.compute_type,
            download_root=str(Path("data/models").resolve()),
        )
        raw_segments, _ = whisper.transcribe(
            str(audio_path),
            language="en",
            task="transcribe",
            beam_size=3,
            temperature=0.0,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={
                "threshold": 0.5,
                "min_speech_duration_ms": 160,
                "min_silence_duration_ms": 350,
                "speech_pad_ms": 220,
            },
        )
        segments = [
            Segment(
                original_text=value.text.strip(),
                start_ms=int(value.start * 1000),
                end_ms=int(value.end * 1000),
            )
            for value in raw_segments
            if value.text.strip()
        ]
        if not segments:
            raise RuntimeError("本地识别没有检测到有效英文语音。")
        return segments


class MLXWhisperTranscriber:
    """Transcribe imported files locally on Apple Silicon."""

    def __init__(self, model: str):
        self.model = model

    def transcribe(self, audio_path: Path, subject: str) -> list[Segment]:
        import mlx_whisper

        result = mlx_whisper.transcribe(
            str(audio_path),
            path_or_hf_repo=self.model,
            language="en",
            task="transcribe",
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=(
                f"English university lecture about {subject}. Preserve names, numbers, and technical terms."
            ),
            verbose=None,
        )
        segments = [
            Segment(
                original_text=str(value.get("text", "")).strip(),
                start_ms=int(float(value.get("start", 0)) * 1000),
                end_ms=int(float(value.get("end", 0)) * 1000),
            )
            for value in result.get("segments", [])
            if str(value.get("text", "")).strip()
        ]
        if not segments:
            text = str(result.get("text", "")).strip()
            if text:
                segments = [Segment(original_text=text)]
        if not segments:
            raise RuntimeError("Mac 本地识别没有检测到有效英文语音。")
        return segments


class OpenAITextProcessor:
    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    def _respond(
        self, instructions: str, input_text: str, timeout: float = 60.0, retries: int = 1
    ) -> str:
        client = (
            self.client.with_options(timeout=timeout, max_retries=retries)
            if hasattr(self.client, "with_options")
            else self.client
        )
        response = client.responses.create(
            model=self.model,
            instructions=instructions,
            input=input_text,
            store=False,
        )
        output = response.output_text.strip()
        if not output:
            raise RuntimeError("文本模型没有返回内容。")
        return output

    def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
        terminology = json.dumps(terms, ensure_ascii=False, indent=2)
        instructions = """你是英文课堂记录翻译助手。请把输入完整翻译为简体中文。
要求：忠实原意，不添加信息；保留数字、公式、代码和单位；技术术语首次出现时可保留英文；
保持原文段落结构；只输出译文，不要解释。"""
        payload = f"课程领域：{subject}\n术语表：{terminology}\n\n英文课堂原文：\n{text}"
        return self._respond(instructions, payload)

    def organize(
        self, title: str, subject: str, original: str, translation: str
    ) -> str:
        instructions = """你是严谨的课堂笔记整理助手。根据英文原文及其中文译文生成中文 Markdown 笔记。
必须包含：课程概述、分主题内容、核心概念、老师给出的例子、重要术语、待复习问题。
没有出现的内容写“未明确提及”，不要臆造老师强调、考试范围、数字、公式或结论。
用清晰标题和列表组织；直接输出 Markdown，不要放入代码围栏。"""
        payload = (
            f"课程名称：{title}\n课程领域：{subject}\n\n"
            f"英文原文：\n{original}\n\n中文译文：\n{translation}"
        )
        return self._respond(instructions, payload)

    def summarize_live(
        self,
        previous: str,
        title: str,
        subject: str,
        original: str,
        translation: str,
    ) -> str:
        instructions, payload = _live_summary_request(
            previous, title, subject, original, translation
        )
        return self._respond(instructions, payload, timeout=15.0, retries=0)


class CompatibleTextProcessor:
    """Text processing through an OpenAI-compatible Chat Completions endpoint."""

    def __init__(
        self,
        client: OpenAI,
        model: str,
        extra_body: dict[str, object] | None = None,
    ):
        self.client = client
        self.model = model
        self.extra_body = extra_body or {}

    def _respond(
        self, instructions: str, input_text: str, timeout: float = 60.0, retries: int = 1
    ) -> str:
        client = (
            self.client.with_options(timeout=timeout, max_retries=retries)
            if hasattr(self.client, "with_options")
            else self.client
        )
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": input_text},
            ],
            stream=False,
            extra_body=self.extra_body,
        )
        output = response.choices[0].message.content
        if not output or not output.strip():
            raise RuntimeError("文本服务没有返回内容。")
        return output.strip()

    def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
        terminology = json.dumps(terms, ensure_ascii=False, indent=2)
        instructions = """你是英文课堂记录翻译助手。请把输入完整翻译为简体中文。
要求：忠实原意，不添加信息；保留数字、公式、代码和单位；技术术语首次出现时可保留英文；
保持原文段落结构；只输出译文，不要解释。"""
        payload = f"课程领域：{subject}\n术语表：{terminology}\n\n英文课堂原文：\n{text}"
        return self._respond(instructions, payload)

    def translate_stream(
        self, text: str, subject: str, terms: dict[str, str]
    ) -> Iterator[str]:
        """Yield text deltas so the UI can show Chinese before a sentence finishes."""
        terminology = json.dumps(terms, ensure_ascii=False, separators=(",", ":"))
        instructions = """你是英文课堂实时翻译器。只把当前片段翻译为自然、准确的简体中文。
不得添加解释或总结；保留数字、单位、公式、代码和专有名词；遵守术语表；只输出译文。"""
        payload = f"课程领域：{subject}\n术语表：{terminology}\n\n当前英文片段：\n{text}"
        stream = self.client.with_options(timeout=12.0, max_retries=0).chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": payload},
            ],
            stream=True,
            max_tokens=max(96, min(512, len(text) * 2)),
            extra_body=self.extra_body,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            content = chunk.choices[0].delta.content
            if content:
                yield content

    def organize(
        self, title: str, subject: str, original: str, translation: str
    ) -> str:
        instructions = """你是严谨的课堂笔记整理助手。根据英文原文及其中文译文生成中文 Markdown 笔记。
必须包含：课程概述、分主题内容、核心概念、老师给出的例子、重要术语、待复习问题。
没有出现的内容写“未明确提及”，不要臆造老师强调、考试范围、数字、公式或结论。
用清晰标题和列表组织；直接输出 Markdown，不要放入代码围栏。"""
        payload = (
            f"课程名称：{title}\n课程领域：{subject}\n\n"
            f"英文原文：\n{original}\n\n中文译文：\n{translation}"
        )
        return self._respond(instructions, payload)

    def summarize_live(
        self,
        previous: str,
        title: str,
        subject: str,
        original: str,
        translation: str,
    ) -> str:
        instructions, payload = _live_summary_request(
            previous, title, subject, original, translation
        )
        return self._respond(instructions, payload, timeout=15.0, retries=0)


def create_text_processor(
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
    openai_client: OpenAI | None = None,
) -> TextProcessor:
    if provider == "openai":
        if not api_key:
            raise RuntimeError("文本服务缺少 API 密钥。请配置 OPENAI_API_KEY 或 TEXT_API_KEY。")
        client = openai_client or OpenAI(api_key=api_key, base_url=base_url)
        return OpenAITextProcessor(client, model)
    if provider not in {"deepseek", "compatible"}:
        raise ValueError("TEXT_PROVIDER 只能是 openai、deepseek 或 compatible。")
    if not api_key:
        variable = "DEEPSEEK_API_KEY" if provider == "deepseek" else "TEXT_API_KEY"
        raise RuntimeError(f"文本服务缺少 API 密钥。请配置 {variable}。")
    if not base_url:
        raise RuntimeError("兼容文本服务需要配置 TEXT_BASE_URL。")
    if not model:
        raise RuntimeError("兼容文本服务需要配置 TEXT_MODEL。")
    client = OpenAI(api_key=api_key, base_url=base_url)
    extra = {"thinking": {"type": "disabled"}} if provider == "deepseek" else None
    return CompatibleTextProcessor(client, model, extra)


class DemoTranscriber:
    def transcribe(self, audio_path: Path, subject: str) -> list[Segment]:
        return [
            Segment(
                start_ms=0,
                end_ms=18000,
                original_text=(
                    "Today we will discuss TCP congestion control. The congestion window "
                    "limits how much unacknowledged data a sender can have in the network."
                ),
            ),
            Segment(
                start_ms=18000,
                end_ms=39000,
                original_text=(
                    "During slow start, the congestion window grows quickly. After reaching "
                    "the threshold, TCP enters congestion avoidance and grows more cautiously."
                ),
            ),
        ]


class DemoTextProcessor:
    def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
        return (
            "今天我们将讨论 TCP 拥塞控制。拥塞窗口限制发送方在网络中尚未得到确认的"
            "数据量。在慢启动阶段，拥塞窗口增长得很快。达到阈值后，TCP 进入拥塞避免"
            "阶段，并以更谨慎的方式增长。"
        )

    def organize(
        self, title: str, subject: str, original: str, translation: str
    ) -> str:
        return f"""# {title}

## 课程概述

本节介绍 TCP 拥塞控制中拥塞窗口、慢启动和拥塞避免的基本关系。

## 分主题内容

### 拥塞窗口

- 拥塞窗口限制发送方在网络中尚未得到确认的数据量。

### 慢启动与拥塞避免

- 慢启动阶段的窗口增长较快。
- 达到阈值后进入拥塞避免阶段，窗口增长更谨慎。

## 核心概念

- 拥塞窗口（congestion window）
- 慢启动（slow start）
- 拥塞避免（congestion avoidance）

## 老师给出的例子

未明确提及。

## 重要术语

- TCP：传输控制协议
- threshold：阈值

## 待复习问题

1. 拥塞窗口限制的具体对象是什么？
2. TCP 在什么条件下从慢启动进入拥塞避免？
"""

    def summarize_live(
        self,
        previous: str,
        title: str,
        subject: str,
        original: str,
        translation: str,
    ) -> str:
        return json.dumps(
            {
                "topic": "TCP 拥塞控制",
                "key_points": [
                    "拥塞窗口限制未确认数据量",
                    "TCP 从慢启动转入拥塞避免后增长更谨慎",
                ],
                "terms": ["congestion window：拥塞窗口", "slow start：慢启动"],
                "questions": ["TCP 在什么条件下切换拥塞控制阶段？"],
            },
            ensure_ascii=False,
        )


class CoursePipeline:
    def __init__(
        self,
        transcriber: Transcriber,
        text_processor: TextProcessor,
        progress: ProgressCallback | None = None,
    ):
        self.transcriber = transcriber
        self.text_processor = text_processor
        self.progress = progress or (lambda _: None)

    def run(
        self,
        audio_path: Path,
        title: str,
        subject: str,
        terms: dict[str, str] | None = None,
    ) -> CourseResult:
        if not audio_path.exists():
            raise FileNotFoundError(f"找不到文件：{audio_path}")
        self.progress("正在转写英文音频……")
        segments = self.transcriber.transcribe(audio_path, subject)
        original = "\n".join(segment.original_text for segment in segments)

        self.progress("正在翻译为简体中文……")
        translation = self.text_processor.translate(original, subject, terms or {})
        # 文件模式有时只返回一个长段落；首版将完整译文绑定到第一段，避免错误切句。
        if segments:
            segments[0].translated_text = translation

        self.progress("正在整理结构化课堂笔记……")
        notes = self.text_processor.organize(title, subject, original, translation)
        self.progress("处理完成，正在保存……")
        return CourseResult(
            title=title,
            subject=subject,
            source_path=str(audio_path.resolve()),
            segments=segments,
            notes_markdown=notes,
        )

    def run_incremental(
        self,
        audio_path: Path,
        title: str,
        subject: str,
        repository: "CourseRepository",
        terms: dict[str, str] | None = None,
    ) -> CourseResult:
        """Persist imported-file progress before each expensive downstream stage."""
        # Local import avoids making the service protocol depend on SQLite at import time.
        from .storage import CourseRepository

        if not isinstance(repository, CourseRepository):
            raise TypeError("repository 必须是 CourseRepository。")
        if not audio_path.exists():
            raise FileNotFoundError(f"找不到文件：{audio_path}")

        result = CourseResult(title, subject, str(audio_path.resolve()), [], "")
        repository.create_course(result, "transcribing")
        try:
            self.progress("正在转写英文音频……")
            result.segments = self.transcriber.transcribe(audio_path, subject)
            if not result.segments:
                raise RuntimeError("没有检测到有效英文语音。")
            for index, segment in enumerate(result.segments):
                repository.add_segment(result.id, segment, index, "pending")

            repository.set_course_state(result.id, "translating")
            total = len(result.segments)
            completed = 0
            failures: list[str] = []

            def translate_one(segment: Segment) -> tuple[Segment, str]:
                repository.set_translation_state(segment.id, "translating")
                translated = self.text_processor.translate(
                    segment.original_text, subject, terms or {}
                ).strip()
                if not translated:
                    raise RuntimeError("翻译服务没有返回内容。")
                return segment, translated

            # Two requests preserve classroom order in storage while avoiding a very
            # slow one-request-at-a-time import for long recordings.
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="classnote-file-translation") as pool:
                futures = {pool.submit(translate_one, segment): segment for segment in result.segments}
                for future in as_completed(futures):
                    segment = futures[future]
                    try:
                        _, translated = future.result()
                        segment.translated_text = translated
                        repository.set_translation_state(
                            segment.id, "completed", translated
                        )
                    except Exception as exc:
                        repository.set_translation_state(
                            segment.id, "failed", error=str(exc)
                        )
                        failures.append(f"{segment.start_ms // 1000}s：{exc}")
                    completed += 1
                    self.progress(f"正在翻译课堂内容…… {completed}/{total}")

            if failures:
                message = (
                    f"有 {len(failures)} 段翻译失败；英文已保存，可在课程库中补译。"
                )
                repository.set_course_state(result.id, "needs_attention", message)
                raise RuntimeError(message)

            repository.set_course_state(result.id, "organizing")
            self.progress("正在整理结构化课堂笔记……")
            result.notes_markdown = self.text_processor.organize(
                title,
                subject,
                result.organized_original_text,
                result.organized_translated_text,
            )
            repository.finalize_course(result.id, result.notes_markdown)
            self.progress("处理完成，正在保存……")
            return result
        except Exception as exc:
            row = repository.get_course(result.id)
            if row is not None and str(row["status"]) not in {"needs_attention", "completed"}:
                status = "needs_attention" if result.segments else "failed"
                repository.set_course_state(result.id, status, str(exc))
            raise
