import queue
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from classnote.courseware import CourseContext
from classnote.models import CourseResult
from classnote.recovery_inventory import list_recovery_candidates
from classnote.storage import CourseRepository
from classnote.temporary_audio import start_temporary_audio
import classnote.local_live as local_live
from classnote.local_live import LocalLiveCourseSession, common_prefix


def test_frozen_windows_registers_bundled_cuda_directories(monkeypatch, tmp_path: Path) -> None:
    bundle = tmp_path / "_internal"
    cublas = bundle / "nvidia" / "cublas" / "bin"
    cudnn = bundle / "nvidia" / "cudnn" / "bin"
    cublas.mkdir(parents=True)
    cudnn.mkdir(parents=True)
    registered: list[str] = []
    monkeypatch.setattr(local_live.sys, "frozen", True, raising=False)
    monkeypatch.setattr(local_live.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(local_live.site, "getsitepackages", lambda: [])
    monkeypatch.setattr(local_live.os, "add_dll_directory", lambda path: registered.append(path))
    monkeypatch.setattr(local_live, "_DLL_HANDLES", [])
    monkeypatch.setattr(local_live, "_REGISTERED_DLL_DIRS", set())
    monkeypatch.setenv("PATH", local_live.os.environ.get("PATH", ""))

    local_live._prepare_nvidia_dlls()

    assert registered == [str(cublas), str(cudnn)]
    first_path = local_live.os.environ["PATH"]
    local_live._prepare_nvidia_dlls()
    assert registered == [str(cublas), str(cudnn)]
    assert local_live.os.environ["PATH"] == first_path


def test_common_prefix_ignores_case_and_trailing_punctuation() -> None:
    assert common_prefix(
        "Today we discuss TCP congestion",
        "Today we discuss TCP congestion control.",
    ) == "Today we discuss TCP congestion"


def test_common_prefix_stops_at_first_changed_word() -> None:
    assert common_prefix("the concession window", "the congestion window") == "the"


def test_live_transcribe_does_not_repeat_vad_or_unused_word_timestamps() -> None:
    captured = {}

    class Model:
        def transcribe(self, audio, **kwargs):
            captured.update(kwargs)
            return [type("Part", (), {"text": " hello "})()], None

    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.course_context = CourseContext()
    text = session._transcribe(Model(), np.zeros(16000, dtype=np.float32))

    assert text == "hello"
    assert captured["word_timestamps"] is False
    assert captured["vad_filter"] is False


def test_local_session_opens_capture_before_loading_whisper() -> None:
    order: list[str] = []
    events: list[tuple[str, object]] = []

    class Stream:
        def start(self) -> None:
            order.append("capture")

        def stop(self) -> None:
            order.append("stop-stream")

        def close(self) -> None:
            order.append("close-stream")

    class Repository:
        def set_course_state(self, *args) -> None:
            order.append("persist-state")

        def delete_course(self, *args) -> None:
            order.append("delete")

    class Closer:
        def close_and_wait(self, timeout: float) -> None:
            order.append("close-translations")

        def close(self, timeout: float) -> None:
            order.append("close-summary")

    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.settings = SimpleNamespace(local_transcription_model="fake-model")
    session.device = SimpleNamespace(is_loopback=False)
    session.repository = Repository()
    session.result = SimpleNamespace(id="course", segments=[])
    session.event = lambda name, payload: events.append((name, payload))
    session.audio_queue = queue.Queue()
    session.ready_event = threading.Event()
    session.capture_ready_event = threading.Event()
    session.model_ready_event = threading.Event()
    session.started_monotonic = local_live.time.monotonic()
    session.stop_event = threading.Event()
    session.stream = None
    session.translations = Closer()
    session.live_summary = Closer()
    session._create_capture_stream = lambda: Stream()

    def load_runtime():
        order.append("load-model")
        assert session.capture_ready_event.is_set()
        return object(), object(), object(), False

    session._load_recognition_runtime = load_runtime
    session._recognition_loop = lambda *args: order.append("recognize")
    session._finalize = lambda: order.append("finalize")

    session._run()

    assert order.index("capture") < order.index("load-model") < order.index("recognize")
    assert session.ready_event.is_set()
    assert any(name == "capture_started" for name, _ in events)
    assert any(name == "model_ready" for name, _ in events)


def test_loading_buffer_reports_audio_before_model_is_ready() -> None:
    events: list[tuple[str, object]] = []

    class Monitor:
        def observe(self, pcm: bytes, dropped_ms: int) -> None:
            assert dropped_ms == 0

    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.audio_queue = queue.Queue(maxsize=100)
    session.stop_event = threading.Event()
    session.pause_event = threading.Event()
    session.model_ready_event = threading.Event()
    session.started_monotonic = local_live.time.monotonic()
    session.dropped_blocks = 0
    session._last_buffer_report_second = -1
    session.event = lambda name, payload: events.append((name, payload))
    session.audio_monitor = Monitor()

    session._callback(bytes(960), 480, None, None)

    assert session.audio_queue.qsize() == 1
    payloads = [payload for name, payload in events if name == "capture_buffer"]
    assert payloads[0]["buffered_ms"] == 30
    assert payloads[0]["capacity_ms"] == 300_000


def test_local_whisper_model_is_reused_for_the_next_class(monkeypatch, tmp_path: Path) -> None:
    created: list[tuple[object, dict[str, object]]] = []

    class WhisperModel:
        def __init__(self, *args, **kwargs) -> None:
            created.append((self, kwargs))

    class VadOptions:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    faster_whisper = types.ModuleType("faster_whisper")
    faster_whisper.WhisperModel = WhisperModel
    vad = types.ModuleType("faster_whisper.vad")
    vad.VadOptions = VadOptions
    vad.get_speech_timestamps = object()
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad)
    monkeypatch.setattr(local_live, "_prepare_nvidia_dlls", lambda: None)
    local_live._MODEL_CACHE.clear()

    first = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    first.settings = SimpleNamespace(
        local_transcription_model="fake-model", local_compute_type="float16",
        database_path=tmp_path / "classnote.db",
    )
    second = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    second.settings = first.settings

    preheated, preheat_reused = local_live.preload_local_model(
        "fake-model", "float16", tmp_path / "models"
    )
    first_model, _, _, first_reused = first._load_recognition_runtime()
    second_model, _, _, second_reused = second._load_recognition_runtime()

    assert preheat_reused is False
    assert len(created) == 1
    assert created[0][1]["download_root"] == str((tmp_path / "models").resolve())
    assert preheated is first_model is second_model
    assert first_reused is True
    assert second_reused is True
    local_live._MODEL_CACHE.clear()


def test_local_model_warmup_consumes_lazy_inference() -> None:
    calls: list[str] = []

    class Model:
        def transcribe(self, audio, **kwargs):
            assert audio.shape == (16000,)
            assert kwargs == {"language": "en", "beam_size": 1}

            def segments():
                calls.append("inference")
                yield object()

            return segments(), None

    local_live.warmup_local_model(Model())
    assert calls == ["inference"]


def test_buffered_opening_audio_keeps_its_original_timestamp() -> None:
    submitted: list[tuple[str, int, int]] = []

    class Translations:
        def submit(self, text: str, start_ms: int, end_ms: int, **kwargs) -> None:
            submitted.append((text, start_ms, end_ms))

    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    # Pretend the model became ready much later. The queued audio itself still
    # belongs to the first second of the class.
    session.started_monotonic = local_live.time.monotonic() - 20
    session.audio_queue = queue.Queue()
    session.audio_queue.put((bytes(16000 * 2), 1000))
    session.stop_event = threading.Event()
    session.stop_event.set()
    session.pause_event = threading.Event()
    session.settings = SimpleNamespace(local_refresh_ms=800)
    session.dropped_blocks = 0
    session.event = lambda *args: None
    session.translations = Translations()
    session._transcribe = lambda model, audio: "Opening sentence."

    def speech(audio, options):
        return [{"start": 0, "end": len(audio)}]

    session._recognition_loop(object(), speech, object())

    assert submitted == [("Opening sentence.", 0, 1000)]


def test_long_pause_splits_audio_without_allocating_silent_minutes() -> None:
    submitted: list[tuple[str, int, int]] = []
    inference_lengths: list[int] = []
    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.started_monotonic = local_live.time.monotonic() - 310
    session.audio_queue = queue.Queue()
    session.audio_queue.put((bytes(16000 * 2), 1000))
    session.audio_queue.put((bytes(16000 * 2), 301000))
    session.stop_event = threading.Event()
    session.stop_event.set()
    session.pause_event = threading.Event()
    session.settings = SimpleNamespace(local_refresh_ms=800)
    session.dropped_blocks = 0
    session.event = lambda *args: None
    session.translations = SimpleNamespace(
        submit=lambda text, start, end, **kwargs: submitted.append((text, start, end))
    )

    def transcribe(model, audio):
        inference_lengths.append(len(audio))
        return "Lecture sentence."

    session._transcribe = transcribe
    speech = lambda audio, options: [{"start": 0, "end": len(audio)}]

    session._recognition_loop(object(), speech, object())

    assert max(inference_lengths) == 16000
    assert submitted == [
        ("Lecture sentence.", 0, 1000),
        ("Lecture sentence.", 300000, 301000),
    ]


def test_pause_flush_processes_pending_audio_after_a_gap() -> None:
    submitted: list[tuple[str, int, int]] = []
    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.started_monotonic = local_live.time.monotonic() - 20
    session.audio_queue = queue.Queue()
    session.audio_queue.put((bytes(16000 * 2), 1000))
    session.audio_queue.put((bytes(16000 * 2), 5000))
    session.stop_event = threading.Event()
    session.pause_event = threading.Event()
    session.pause_event.set()
    session.settings = SimpleNamespace(local_refresh_ms=800)
    session.dropped_blocks = 0
    session.event = lambda *args: None

    def submit(text, start, end, **kwargs):
        submitted.append((text, start, end))
        if len(submitted) == 2:
            session.stop_event.set()

    session.translations = SimpleNamespace(submit=submit)
    session._transcribe = lambda model, audio: "Lecture sentence."
    speech = lambda audio, options: [{"start": 0, "end": len(audio)}]
    worker = threading.Thread(
        target=session._recognition_loop, args=(object(), speech, object()), daemon=True
    )
    worker.start()
    worker.join(timeout=2)
    if worker.is_alive():
        session.stop_event.set()
        session.pause_event.clear()
        worker.join(timeout=1)

    assert not worker.is_alive(), "暂停后的待处理片段不能卡住结束流程"
    assert submitted == [
        ("Lecture sentence.", 0, 1000),
        ("Lecture sentence.", 4000, 5000),
    ]


def test_vad_checks_new_audio_in_steps_instead_of_every_block() -> None:
    checks: list[int] = []
    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.audio_queue = queue.Queue()
    for block in range(40):
        session.audio_queue.put((bytes(960), (block + 1) * 30))
    session.stop_event = threading.Event()
    session.stop_event.set()
    session.pause_event = threading.Event()
    session.settings = SimpleNamespace(local_refresh_ms=800)
    session.dropped_blocks = 0
    session.event = lambda *args: None

    def no_speech(audio, options):
        checks.append(len(audio))
        return []

    session._recognition_loop(object(), no_speech, object())

    assert len(checks) <= 12  # 1.2 s / 120 ms, plus the final forced check.
    assert checks[-1] == 40 * 480


def test_backlog_warning_has_margin_before_downgrading() -> None:
    assert local_live._backlog_level(5000, 0) == 2
    assert local_live._backlog_level(4970, 2) == 2
    assert local_live._backlog_level(4000, 2) == 1
    assert local_live._backlog_level(1400, 1) == 1
    assert local_live._backlog_level(1000, 1) == 0


def test_partial_preview_refresh_backs_off_only_when_audio_is_queued() -> None:
    base = 0.8
    assert local_live._partial_refresh_seconds(base, 0) == base
    assert local_live._partial_refresh_seconds(base, 1499) == base
    assert local_live._partial_refresh_seconds(base, 1500) == 1.6
    assert local_live._partial_refresh_seconds(base, 5000) == 2.4
    assert local_live._partial_refresh_seconds(3.0, 5000) == 3.0


def test_backed_up_fast_speech_is_still_saved_on_final_pass() -> None:
    submitted: list[tuple[str, int, int]] = []
    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.started_monotonic = local_live.time.monotonic()
    session.audio_queue = queue.Queue()
    for block in range(200):
        session.audio_queue.put((bytes(960), (block + 1) * 30))
    session.stop_event = threading.Event()
    session.stop_event.set()
    session.pause_event = threading.Event()
    session.settings = SimpleNamespace(local_refresh_ms=800)
    session.dropped_blocks = 0
    session.event = lambda *args: None
    session.translations = SimpleNamespace(
        submit=lambda text, start, end, **kwargs: submitted.append((text, start, end))
    )
    session._transcribe = lambda model, audio: "Fast lecture."
    speech = lambda audio, options: [{"start": 0, "end": len(audio)}]

    session._recognition_loop(object(), speech, object())

    assert submitted == [("Fast lecture.", 0, 6000)]


def test_vad_final_pass_keeps_a_short_last_fragment() -> None:
    submitted: list[tuple[str, int, int]] = []
    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.started_monotonic = local_live.time.monotonic()
    session.audio_queue = queue.Queue()
    session.audio_queue.put((bytes(960), 30))
    session.stop_event = threading.Event()
    session.stop_event.set()
    session.pause_event = threading.Event()
    session.settings = SimpleNamespace(local_refresh_ms=800)
    session.dropped_blocks = 0
    session.event = lambda *args: None
    session.translations = SimpleNamespace(
        submit=lambda text, start, end, **kwargs: submitted.append((text, start, end))
    )
    session._transcribe = lambda model, audio: "Last word."
    speech = lambda audio, options: [{"start": 0, "end": len(audio)}]

    session._recognition_loop(object(), speech, object())

    assert submitted == [("Last word.", 0, 30)]


def test_asr_metrics_include_audio_waiting_behind_inference() -> None:
    events: list[tuple[str, object]] = []
    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.started_monotonic = local_live.time.monotonic()
    session.audio_queue = queue.Queue()
    session.audio_queue.put((bytes(16000 * 2), 1000))
    session.stop_event = threading.Event()
    session.stop_event.set()
    session.pause_event = threading.Event()
    session.settings = SimpleNamespace(local_refresh_ms=800)
    session.dropped_blocks = 0
    session.event = lambda name, payload: events.append((name, payload))
    session.translations = SimpleNamespace(submit=lambda *args, **kwargs: None)
    first_inference = True

    def transcribe(model, audio):
        nonlocal first_inference
        if first_inference:
            first_inference = False
            for block in range(6):
                session.audio_queue.put((bytes(960), 1030 + block * 30))
        return "Lecture sentence."

    session._transcribe = transcribe
    speech = lambda audio, options: [{"start": 0, "end": len(audio)}]

    session._recognition_loop(object(), speech, object())

    metrics = [payload for name, payload in events if name == "asr_metrics"]
    assert metrics[0]["queued_ms"] == 180


def test_backlog_status_updates_without_any_recognized_words() -> None:
    events: list[tuple[str, object]] = []
    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.audio_queue = queue.Queue()
    for block in range(200):
        session.audio_queue.put((bytes(960), (block + 1) * 30))
    session.stop_event = threading.Event()
    session.stop_event.set()
    session.pause_event = threading.Event()
    session.settings = SimpleNamespace(local_refresh_ms=800)
    session.dropped_blocks = 0
    session.event = lambda name, payload: events.append((name, payload))

    session._recognition_loop(object(), lambda audio, options: [], object())

    backlog = [payload for name, payload in events if name == "asr_backlog"]
    assert backlog[0]["queued_ms"] >= 5000
    assert backlog[0]["backlog_level"] == 2
    assert backlog[-1]["queued_ms"] <= 1000
    assert backlog[-1]["backlog_level"] == 0
    assert len(backlog) <= 5
    assert not any(name == "asr_metrics" for name, _ in events)


def test_stop_drains_buffered_audio_even_when_model_loaded_during_pause() -> None:
    submitted: list[tuple[str, int, int]] = []
    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.started_monotonic = local_live.time.monotonic() - 20
    session.audio_queue = queue.Queue()
    session.audio_queue.put((bytes(16000 * 2), 1000))
    session.stop_event = threading.Event()
    session.stop_event.set()
    session.pause_event = threading.Event()
    session.pause_event.set()
    session.settings = SimpleNamespace(local_refresh_ms=800)
    session.dropped_blocks = 0
    session.event = lambda *args: None
    session.translations = SimpleNamespace(
        submit=lambda text, start, end, **kwargs: submitted.append((text, start, end))
    )
    session._transcribe = lambda model, audio: "Opening sentence."
    speech = lambda audio, options: [{"start": 0, "end": len(audio)}]

    worker = threading.Thread(
        target=session._recognition_loop,
        args=(object(), speech, object()),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=1)
    stalled = worker.is_alive()
    if stalled:
        session.pause_event.clear()
        worker.join(timeout=1)

    assert not stalled, "结束课堂不应等待用户再次点击继续"
    assert submitted == [("Opening sentence.", 0, 1000)]


def test_pause_drains_queued_audio_as_one_sentence() -> None:
    submitted: list[tuple[str, int, int]] = []
    translated = threading.Event()
    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.started_monotonic = local_live.time.monotonic() - 20
    session.audio_queue = queue.Queue()
    session.audio_queue.put((bytes(8000 * 2), 500))
    session.audio_queue.put((bytes(8000 * 2), 1000))
    session.stop_event = threading.Event()
    session.pause_event = threading.Event()
    session.pause_event.set()
    session.settings = SimpleNamespace(local_refresh_ms=800)
    session.dropped_blocks = 0
    session.event = lambda *args: None

    def submit(text: str, start: int, end: int, **kwargs) -> None:
        submitted.append((text, start, end))
        translated.set()

    session.translations = SimpleNamespace(submit=submit)
    session._transcribe = lambda model, audio: "Opening sentence."
    speech = lambda audio, options: [{"start": 0, "end": len(audio)}]
    worker = threading.Thread(
        target=session._recognition_loop,
        args=(object(), speech, object()),
        daemon=True,
    )
    worker.start()
    try:
        assert translated.wait(timeout=1), "暂停后应整理已经录到的语音"
    finally:
        session.stop_event.set()
        worker.join(timeout=1)

    assert not worker.is_alive()
    assert submitted == [("Opening sentence.", 0, 1000)]


def test_model_load_failure_closes_capture_without_finalizing_empty_course() -> None:
    actions: list[str] = []
    events: list[tuple[str, object]] = []

    class Stream:
        def start(self) -> None:
            actions.append("capture")

        def stop(self) -> None:
            actions.append("stop")

        def close(self) -> None:
            actions.append("close")

    class Repository:
        def set_course_state(self, *args) -> None:
            actions.append("state")

        def delete_course(self, course_id: str) -> None:
            actions.append("delete")

    class Translations:
        def close_and_wait(self, timeout: float) -> None:
            actions.append("translations-closed")

    class Summary:
        def close(self, timeout: float) -> None:
            actions.append("summary-closed")

    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.settings = SimpleNamespace(local_transcription_model="broken-model")
    session.device = SimpleNamespace(is_loopback=False)
    session.repository = Repository()
    session.result = SimpleNamespace(id="course", segments=[])
    session.event = lambda name, payload: events.append((name, payload))
    session.audio_queue = queue.Queue()
    session.ready_event = threading.Event()
    session.capture_ready_event = threading.Event()
    session.model_ready_event = threading.Event()
    session.stop_event = threading.Event()
    session.stream = None
    session.translations = Translations()
    session.live_summary = Summary()
    session._create_capture_stream = lambda: Stream()
    session._load_recognition_runtime = lambda: (_ for _ in ()).throw(
        RuntimeError("CUDA unavailable")
    )
    session._finalize = lambda: actions.append("finalize")

    session._run()

    assert actions[0] == "capture"
    assert "delete" in actions
    assert "finalize" not in actions
    assert session.stop_event.is_set()
    assert any(name == "error" for name, _ in events)


def test_model_load_failure_keeps_opt_in_captured_audio_for_recovery(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "classnote.db")
    result = CourseResult("Lecture", "Physics", "microphone", [], "")
    repository.create_course(result)
    events: list[tuple[str, object]] = []
    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.settings = SimpleNamespace(local_transcription_model="broken-model")
    session.device = SimpleNamespace(is_loopback=False)
    session.repository = repository
    session.result = result
    session.event = lambda name, payload: events.append((name, payload))
    session.audio_queue = queue.Queue()
    session.ready_event = threading.Event()
    session.capture_ready_event = threading.Event()
    session.model_ready_event = threading.Event()
    session.stop_event = threading.Event()
    session.pause_event = threading.Event()
    session.started_monotonic = local_live.time.monotonic()
    session.dropped_blocks = 0
    session._last_buffer_report_second = -1
    session.audio_monitor = SimpleNamespace(observe=lambda *args: None)
    session.temporary_audio = start_temporary_audio(
        repository.database_path, result.id, session.SAMPLE_RATE,
        lambda message: events.append(("warning", message)),
    )
    assert session.temporary_audio is not None
    session.translations = SimpleNamespace(close_and_wait=lambda timeout: None)
    session.live_summary = SimpleNamespace(close=lambda timeout: None)

    class Stream:
        def start(self) -> None:
            session._callback(b"\x01\x00" * 16000, 16000, None, None)

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    session._create_capture_stream = lambda: Stream()
    session._load_recognition_runtime = lambda: (_ for _ in ()).throw(
        RuntimeError("CUDA unavailable")
    )

    session._run()

    row = repository.get_course(result.id)
    assert row is not None and row["status"] == "failed"
    candidates = list_recovery_candidates(repository)
    assert len(candidates) == 1
    assert candidates[0].course_id == result.id
    assert candidates[0].audio_path == session.temporary_audio.path
    assert any(name == "error" for name, _ in events)
    assert [name for name, _ in events].index("error") > max(
        index for index, (name, message) in enumerate(events)
        if name == "warning" and "临时音频保留" in str(message)
    )
    assert events[-1] == ("session_ended", result.id)


def test_translation_shutdown_error_still_closes_summary_and_ends_session() -> None:
    order: list[str] = []
    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.settings = SimpleNamespace(local_transcription_model="fake-model")
    session.device = SimpleNamespace(is_loopback=False)
    session.repository = SimpleNamespace(
        set_course_state=lambda *args: order.append("state")
    )
    session.result = SimpleNamespace(id="course", segments=[])
    session.event = lambda name, payload: order.append(name)
    session.audio_queue = queue.Queue()
    session.ready_event = threading.Event()
    session.capture_ready_event = threading.Event()
    session.model_ready_event = threading.Event()
    session.stop_event = threading.Event()
    session.stream = None
    session.temporary_audio = None
    session.translations = SimpleNamespace(
        close_and_wait=lambda timeout: (_ for _ in ()).throw(RuntimeError("worker failed"))
    )
    session.live_summary = SimpleNamespace(close=lambda timeout: order.append("summary-closed"))
    session._create_capture_stream = lambda: SimpleNamespace(
        start=lambda: None, stop=lambda: None, close=lambda: None,
    )
    session._load_recognition_runtime = lambda: (object(), object(), object(), False)
    session._recognition_loop = lambda *args: None
    session._finalize = lambda: order.append("finalize")

    session._run()

    assert "summary-closed" in order
    assert "finalize" not in order
    assert "error" in order
    assert order[-1] == "session_ended"


def test_pause_and_stop_explain_when_model_is_still_loading() -> None:
    messages: list[str] = []
    session = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    session.pause_event = threading.Event()
    session.stop_event = threading.Event()
    session.model_ready_event = threading.Event()
    session.audio_queue = queue.Queue()
    session.audio_queue.put((bytes(960), 30))
    session.event = lambda name, payload: messages.append(str(payload))

    session.pause()
    session.resume()
    session.stop()

    assert "模型仍在准备" in messages[0]
    assert "开场内容正在缓存" in messages[1]
    assert "已缓存的开场音频" in messages[2]
