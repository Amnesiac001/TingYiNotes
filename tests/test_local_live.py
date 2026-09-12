import queue
import sys
import threading
import types
from types import SimpleNamespace

import numpy as np

from classnote.courseware import CourseContext
import classnote.local_live as local_live
from classnote.local_live import LocalLiveCourseSession, common_prefix


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


def test_local_whisper_model_is_reused_for_the_next_class(monkeypatch) -> None:
    created: list[object] = []

    class WhisperModel:
        def __init__(self, *args, **kwargs) -> None:
            created.append(self)

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
        local_transcription_model="fake-model", local_compute_type="float16"
    )
    second = LocalLiveCourseSession.__new__(LocalLiveCourseSession)
    second.settings = first.settings

    first_model, _, _, first_reused = first._load_recognition_runtime()
    second_model, _, _, second_reused = second._load_recognition_runtime()

    assert len(created) == 1
    assert first_model is second_model
    assert first_reused is False
    assert second_reused is True
    local_live._MODEL_CACHE.clear()


def test_buffered_opening_audio_keeps_its_original_timestamp() -> None:
    submitted: list[tuple[str, int, int]] = []

    class Translations:
        def submit(self, text: str, start_ms: int, end_ms: int) -> None:
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
        submit=lambda text, start, end: submitted.append((text, start, end))
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

    def submit(text: str, start: int, end: int) -> None:
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
