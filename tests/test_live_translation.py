from pathlib import Path
import threading

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
