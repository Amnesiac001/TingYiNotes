import queue
from types import SimpleNamespace

import pytest

import classnote.live as chunked_live
import classnote.realtime_live as realtime_live


def test_chunked_session_reports_end_only_after_audio_cleanup(monkeypatch) -> None:
    order: list[str] = []
    session = chunked_live.ChunkedLiveCourseSession.__new__(
        chunked_live.ChunkedLiveCourseSession
    )
    session.pending = queue.Queue()
    session.pending.put(None)
    session.live_summary = SimpleNamespace(close=lambda timeout: order.append("summary"))
    session._finalize = lambda: order.append("finished")
    session.temporary_audio = object()
    session.repository = object()
    session.result = SimpleNamespace(id="chunked-course")
    session.settings = SimpleNamespace(retain_audio_for_review=False)
    session.event = lambda name, payload: order.append(name)
    monkeypatch.setattr(
        chunked_live, "finish_temporary_audio",
        lambda *args, **kwargs: order.append("audio-closed"),
    )

    session._process_chunks()

    assert order == ["summary", "finished", "audio-closed", "session_ended"]


def test_realtime_session_reports_end_only_after_audio_cleanup(monkeypatch) -> None:
    order: list[str] = []
    session = realtime_live.RealtimeLiveCourseSession.__new__(
        realtime_live.RealtimeLiveCourseSession
    )
    session.stream = None
    session._stop_audio_sender = lambda timeout: True
    session.connection = SimpleNamespace(
        input_audio_buffer=SimpleNamespace(commit=lambda: None)
    )
    session._close_transport = lambda: order.append("transport")
    session.receiver_thread = None
    session.translations = SimpleNamespace(close_and_wait=lambda timeout: order.append("translations"))
    session.live_summary = SimpleNamespace(close=lambda timeout: order.append("summary"))
    session._finalize = lambda: order.append("finished")
    session.temporary_audio = object()
    session.repository = object()
    session.result = SimpleNamespace(id="realtime-course")
    session.settings = SimpleNamespace(retain_audio_for_review=False)
    session.event = lambda name, payload: order.append(name)
    monkeypatch.setattr(realtime_live.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        realtime_live, "finish_temporary_audio",
        lambda *args, **kwargs: order.append("audio-closed"),
    )

    session._finish()

    assert order == [
        "transport", "translations", "summary", "finished", "audio-closed", "session_ended"
    ]


def test_chunked_recorder_stop_error_still_releases_processor() -> None:
    events: list[str] = []
    session = chunked_live.ChunkedLiveCourseSession.__new__(
        chunked_live.ChunkedLiveCourseSession
    )
    session.pending = queue.Queue()
    session.recorder = SimpleNamespace(stop=lambda: (_ for _ in ()).throw(OSError("device gone")))
    session.event = lambda name, payload: events.append(name)

    session._finish_recording()

    assert session.pending.get_nowait() is None
    assert events == ["warning"]


def test_chunked_recorder_closes_stream_and_joins_collector_after_stop_error() -> None:
    order: list[str] = []
    recorder = chunked_live.ChunkedMicrophoneRecorder.__new__(
        chunked_live.ChunkedMicrophoneRecorder
    )
    recorder.stop_event = SimpleNamespace(set=lambda: order.append("signal-stop"))
    recorder.stream = SimpleNamespace(
        stop=lambda: (_ for _ in ()).throw(OSError("device gone")),
        close=lambda: order.append("stream-closed"),
    )
    recorder.thread = SimpleNamespace(join=lambda timeout: order.append("collector-joined"))
    recorder.chunk_seconds = 10

    with pytest.raises(OSError, match="device gone"):
        recorder.stop()

    assert order == ["signal-stop", "stream-closed", "collector-joined"]


def test_chunked_summary_failure_still_closes_backup_and_reports_end(monkeypatch) -> None:
    order: list[str] = []
    session = chunked_live.ChunkedLiveCourseSession.__new__(
        chunked_live.ChunkedLiveCourseSession
    )
    session.pending = queue.Queue()
    session.pending.put(None)
    session.live_summary = SimpleNamespace(
        close=lambda timeout: (_ for _ in ()).throw(RuntimeError("summary stopped"))
    )
    session.repository = SimpleNamespace(
        set_course_state=lambda *args: order.append("needs-attention")
    )
    session.result = SimpleNamespace(id="chunked-course")
    session.settings = SimpleNamespace(retain_audio_for_review=False)
    session.temporary_audio = object()
    session.event = lambda name, payload: order.append(name)
    monkeypatch.setattr(
        chunked_live, "finish_temporary_audio",
        lambda *args, **kwargs: order.append("audio-closed"),
    )

    session._process_chunks()

    assert order == ["needs-attention", "error", "audio-closed", "session_ended"]


def test_realtime_stream_stop_error_still_closes_transport_and_backup(monkeypatch) -> None:
    order: list[str] = []
    session = realtime_live.RealtimeLiveCourseSession.__new__(
        realtime_live.RealtimeLiveCourseSession
    )
    session.stream = SimpleNamespace(
        stop=lambda: (_ for _ in ()).throw(OSError("device gone")),
        close=lambda: order.append("stream-closed"),
    )
    session._stop_audio_sender = lambda timeout: True
    session.connection = SimpleNamespace(
        input_audio_buffer=SimpleNamespace(commit=lambda: None)
    )
    session._close_transport = lambda: order.append("transport-closed")
    session.receiver_thread = None
    session.translations = SimpleNamespace(close_and_wait=lambda timeout: None)
    session.live_summary = SimpleNamespace(close=lambda timeout: None)
    session._finalize = lambda: order.append("finished")
    session.repository = SimpleNamespace(
        set_course_state=lambda *args: order.append("needs-attention")
    )
    session.result = SimpleNamespace(id="realtime-course")
    session.settings = SimpleNamespace(retain_audio_for_review=False)
    session.temporary_audio = object()
    session.event = lambda name, payload: order.append(name)
    monkeypatch.setattr(realtime_live.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        realtime_live, "finish_temporary_audio",
        lambda *args, **kwargs: order.append("audio-closed"),
    )

    session._finish()

    assert "stream-closed" in order
    assert "transport-closed" in order
    assert order[-2:] == ["audio-closed", "session_ended"]
