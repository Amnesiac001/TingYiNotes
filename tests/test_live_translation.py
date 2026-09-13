from pathlib import Path
import threading
import time

from classnote.live_translation import LiveTranslationCoordinator, protected_tokens
from classnote.models import CourseResult
from classnote.storage import CourseRepository


class StreamingProcessor:
    def translate_stream(self, text: str, subject: str, terms: dict[str, str]):
        yield "拥塞"
        yield "窗口 15 TCP"

    def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
        raise AssertionError("streaming path should be used")

    def organize(self, title: str, subject: str, original: str, translation: str) -> str:
        return "# notes"


def test_live_translation_persists_english_then_updates_chinese(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "live.db")
    result = CourseResult("课", "网络", "local:mic", [], "")
    repository.create_course(result)
    events: list[tuple[str, object]] = []
    translated_callbacks = []
    coordinator = LiveTranslationCoordinator(
        result,
        repository,
        StreamingProcessor(),
        "网络",
        lambda name, payload: events.append((name, payload)),
        workers=1,
        on_translated=translated_callbacks.append,
    )
    coordinator.start()
    segment = coordinator.submit("TCP window 15", 0, 1000)
    coordinator.close_and_wait()

    with repository.connect() as connection:
        row = connection.execute("SELECT * FROM segments WHERE id = ?", (segment.id,)).fetchone()
    assert row["original_text"] == "TCP window 15"
    assert row["translated_text"] == "拥塞窗口 15 TCP"
    assert row["translation_status"] == "completed"
    assert any(name == "segment_original" for name, _ in events)
    assert any(name == "translation_delta" for name, _ in events)
    metric_payloads = [payload for name, payload in events if name == "metrics"]
    assert any("translation_active" in payload for payload in metric_payloads)
    assert metric_payloads[-1]["translation_queue"] == 0
    assert metric_payloads[-1]["last_translation_ms"] >= 0
    assert translated_callbacks == [segment]


def test_protected_tokens_extracts_numbers_and_acronyms() -> None:
    assert protected_tokens("TCP uses 15.5 ms") == {"tcp", "15.5"}


def test_failed_live_translation_retries_the_same_persisted_segment(tmp_path: Path) -> None:
    class FlakyProcessor:
        failing = True

        def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
            if self.failing:
                raise RuntimeError("temporary timeout")
            return "重试成功"

        def organize(self, title: str, subject: str, original: str, translation: str) -> str:
            return "# notes"

    repository = CourseRepository(tmp_path / "retry.db")
    result = CourseResult("课", "网络", "local:mic", [], "")
    repository.create_course(result)
    processor = FlakyProcessor()
    failed = threading.Event()
    completed = threading.Event()

    def receive(name: str, payload: object) -> None:
        if name == "translation_failed":
            failed.set()
        elif name == "segment_update":
            completed.set()

    coordinator = LiveTranslationCoordinator(
        result,
        repository,
        processor,  # type: ignore[arg-type]
        "网络",
        receive,
        workers=1,
    )
    coordinator.start()
    segment = coordinator.submit("Retry me", 0, 1000)
    assert failed.wait(timeout=3)
    processor.failing = False
    coordinator.retry(segment.id)
    assert completed.wait(timeout=3)
    coordinator.close_and_wait()

    rows = repository.get_course_segments(result.id)
    assert len(rows) == 1
    assert rows[0]["id"] == segment.id
    assert rows[0]["translation_status"] == "completed"
    assert rows[0]["translated_text"] == "重试成功"


def test_full_translation_queue_cannot_block_bounded_class_shutdown(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class SlowProcessor:
        def translate(self, text: str, subject: str, terms: dict[str, str]) -> str:
            entered.set()
            release.wait(timeout=3)
            return "已翻译"

    repository = CourseRepository(tmp_path / "backlog.db")
    result = CourseResult("积压课堂", "测试", "local:mic", [], "")
    repository.create_course(result)
    coordinator = LiveTranslationCoordinator(
        result, repository, SlowProcessor(), "测试", lambda *_: None,
        workers=1, max_queue=1,
    )
    coordinator.start()
    first = coordinator.submit("First", 0, 1000)
    assert entered.wait(timeout=1)
    second = coordinator.submit("Second", 1000, 2000)
    watchdog = threading.Timer(2, release.set)
    watchdog.daemon = True
    watchdog.start()

    started = time.monotonic()
    finished = coordinator.close_and_wait(timeout=0.1)
    elapsed = time.monotonic() - started
    release.set()
    watchdog.cancel()
    for worker in coordinator.threads:
        worker.join(timeout=2)

    rows = {row["id"]: row for row in repository.get_course_segments(result.id)}
    assert not finished
    assert elapsed < 1.2
    assert rows[first.id]["original_text"] == "First"
    assert rows[second.id]["original_text"] == "Second"
    assert rows[second.id]["translation_status"] == "retry"


def test_budget_paused_sentence_is_saved_without_entering_queue(tmp_path: Path) -> None:
    class PausedProcessor:
        budget_guard = staticmethod(lambda phase: False)

    repository = CourseRepository(tmp_path / "budget-queue.db")
    result = CourseResult("课", "网络", "local:mic", [], "")
    repository.create_course(result)
    events: list[tuple[str, object]] = []
    coordinator = LiveTranslationCoordinator(
        result, repository, PausedProcessor(), "网络",
        lambda name, payload: events.append((name, payload)), workers=1,
    )
    segment = coordinator.submit("Keep this English", 0, 1000)
    assert coordinator.queue.empty()
    row = repository.get_course_segments(result.id)[0]
    assert row["original_text"] == "Keep this English"
    assert row["translation_status"] == "retry"
    assert any(name == "translation_failed" and payload[0] == segment.id for name, payload in events)


def test_queue_overflow_marks_each_sentence_but_throttles_warning(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class SlowProcessor:
        def translate(self, *args):
            entered.set()
            release.wait(timeout=3)
            return "译文"

    repository = CourseRepository(tmp_path / "overflow.db")
    result = CourseResult("课", "网络", "local:mic", [], "")
    repository.create_course(result)
    events: list[tuple[str, object]] = []
    coordinator = LiveTranslationCoordinator(
        result, repository, SlowProcessor(), "网络",
        lambda name, payload: events.append((name, payload)),
        workers=1, max_queue=1,
    )
    coordinator.start()
    coordinator.submit("First", 0, 1000)
    assert entered.wait(timeout=1)
    coordinator.submit("Second", 1000, 2000)
    overflow_a = coordinator.submit("Third", 2000, 3000)
    overflow_b = coordinator.submit("Fourth", 3000, 4000)
    assert [name for name, _ in events].count("warning") == 1
    failed_ids = {payload[0] for name, payload in events if name == "translation_failed"}
    assert {overflow_a.id, overflow_b.id} <= failed_ids
    rows = {row["id"]: row for row in repository.get_course_segments(result.id)}
    assert rows[overflow_a.id]["translation_status"] == "retry"
    assert rows[overflow_b.id]["translation_status"] == "retry"
    release.set()
    coordinator.close_and_wait(timeout=5)


def test_queue_overflow_is_automatically_translated_after_short_pause(tmp_path: Path) -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    deferred_done = threading.Event()

    class SlowFirstProcessor:
        def translate(self, text: str, *args) -> str:
            if text == "First":
                first_started.set()
                release_first.wait(timeout=3)
            return f"译文：{text}"

    repository = CourseRepository(tmp_path / "catchup.db")
    result = CourseResult("课", "网络", "local:mic", [], "")
    repository.create_course(result)
    events: list[tuple[str, object]] = []

    def receive(name: str, payload: object) -> None:
        events.append((name, payload))
        if name == "segment_update" and payload.original_text == "Third":
            deferred_done.set()

    coordinator = LiveTranslationCoordinator(
        result, repository, SlowFirstProcessor(), "网络", receive,
        workers=1, max_queue=1, catchup_quiet_seconds=0.3,
    )
    coordinator.start()
    coordinator.submit("First", 0, 1000)
    assert first_started.wait(timeout=1)
    coordinator.submit("Second", 1000, 2000)
    deferred = coordinator.submit("Third", 2000, 3000)
    assert repository.get_course_segments(result.id)[2]["translation_status"] == "retry"
    release_first.set()
    assert deferred_done.wait(timeout=3)
    coordinator.close_and_wait(timeout=5)
    rows = {row["id"]: row for row in repository.get_course_segments(result.id)}
    assert rows[deferred.id]["translated_text"] == "译文：Third"
    assert rows[deferred.id]["translation_status"] == "completed"
    assert any(name == "segment_retrying" and payload == deferred.id for name, payload in events)
