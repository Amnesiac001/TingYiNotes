from pathlib import Path
import sqlite3
import pytest

from classnote.models import CourseResult, Segment
from classnote.storage import CourseRepository


def test_repository_saves_course_and_segments(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "classnote.db")
    result = CourseResult(
        title="课程",
        subject="学科",
        source_path="lesson.mp3",
        segments=[Segment("Hello", "你好")],
        notes_markdown="# 课程",
    )
    repository.save(result)
    assert repository.count_courses() == 1
    with repository.connect() as connection:
        row = connection.execute("SELECT * FROM segments").fetchone()
        assert row["original_text"] == "Hello"
        assert row["translated_text"] == "你好"


def test_repository_incrementally_saves_live_course(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "live.db")
    result = CourseResult("实时课", "网络", "microphone:test", [], "")
    repository.create_course(result)
    segment = Segment("Live text", "实时文本", 0, 10000)
    repository.add_segment(result.id, segment, 0)
    repository.finalize_course(result.id, "# 实时课")
    with repository.connect() as connection:
        course = connection.execute("SELECT * FROM courses WHERE id = ?", (result.id,)).fetchone()
        saved_segment = connection.execute(
            "SELECT * FROM segments WHERE course_id = ?", (result.id,)
        ).fetchone()
    assert course["notes_markdown"] == "# 实时课"
    assert course["status"] == "completed"
    assert saved_segment["original_text"] == "Live text"


def test_repository_saves_english_before_translation_and_updates_it(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "pending.db")
    result = CourseResult("实时课", "网络", "microphone:test", [], "")
    repository.create_course(result)
    segment = Segment("Congestion window", "", 1000, 2000)

    repository.add_segment(result.id, segment, 0, "pending")
    assert repository.pending_segments(result.id)[0]["original_text"] == "Congestion window"

    repository.set_translation_state(segment.id, "completed", "拥塞窗口")
    with repository.connect() as connection:
        saved = connection.execute("SELECT * FROM segments WHERE id = ?", (segment.id,)).fetchone()
    assert saved["translated_text"] == "拥塞窗口"
    assert saved["translation_status"] == "completed"


def test_repository_lists_searches_and_reads_courses(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "library.db")
    first = CourseResult(
        "计算机网络",
        "工科",
        "network.mp3",
        [Segment("Packet", "数据包", 0, 1000)],
        "# 网络笔记",
    )
    second = CourseResult("文学导论", "人文", "literature.mp3", [], "# 文学笔记")
    repository.save(first)
    repository.save(second)

    rows = repository.list_courses(query="网络")
    segments = repository.get_course_segments(first.id)

    assert len(rows) == 1
    assert rows[0]["title"] == "计算机网络"
    assert rows[0]["segment_count"] == 1
    assert rows[0]["duration_ms"] == 1000
    assert rows[0]["pending_count"] == 0
    assert len(segments) == 1
    assert segments[0]["translated_text"] == "数据包"


def test_repository_deletes_course_and_cascades_segments(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "delete.db")
    keep = CourseResult("保留", "课程", "keep", [Segment("Keep", "保留")], "# 保留")
    remove = CourseResult("删除", "课程", "remove", [Segment("Delete", "删除")], "# 删除")
    repository.save(keep)
    repository.save(remove)

    assert repository.delete_course(remove.id) is True
    assert repository.delete_course(remove.id) is False
    assert [row["title"] for row in repository.list_courses()] == ["保留"]
    assert repository.get_course_segments(remove.id) == []
    assert len(repository.get_course_segments(keep.id)) == 1


def test_repository_migrates_legacy_segments_without_losing_text(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE courses (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, subject TEXT NOT NULL,
                source_path TEXT NOT NULL, created_at TEXT NOT NULL,
                notes_markdown TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE segments (
                id TEXT PRIMARY KEY, course_id TEXT NOT NULL, start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL, original_text TEXT NOT NULL,
                translated_text TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL
            );
            INSERT INTO courses VALUES ('c', '旧课', '网络', 'old', 'now', '');
            INSERT INTO segments VALUES ('s', 'c', 0, 1000, 'Original survives', '', 0);
            """
        )

    repository = CourseRepository(path)
    with repository.connect() as connection:
        row = connection.execute("SELECT * FROM segments WHERE id = 's'").fetchone()
    assert row["original_text"] == "Original survives"
    assert row["translation_status"] == "pending"
    assert row["marker"] == ""
    with repository.connect() as connection:
        course = connection.execute("SELECT * FROM courses WHERE id = 'c'").fetchone()
    assert course["status"] == "needs_attention"
    assert course["updated_at"] == "now"


def test_repository_persists_and_validates_classroom_markers(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "markers.db")
    result = CourseResult("课堂", "网络", "mic", [], "")
    repository.create_course(result)
    segment = Segment("Important concept", "重要概念", 0, 1000)
    repository.add_segment(result.id, segment, 0)

    repository.set_segment_marker(segment.id, "important")
    assert repository.get_course_segments(result.id)[0]["marker"] == "important"
    with pytest.raises(ValueError, match="重点"):
        repository.set_segment_marker(segment.id, "exam")


def test_repository_tracks_lifecycle_and_marks_abandoned_work(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "lifecycle.db")
    result = CourseResult("课堂", "网络", "mic", [], "")
    repository.create_course(result)
    repository.set_course_state(result.id, "transcribing")

    assert repository.mark_active_courses_interrupted() == 1
    row = repository.get_course(result.id)
    assert row is not None
    assert row["status"] == "interrupted"
    assert "上次退出" in row["error_message"]


def test_repository_searches_transcript_and_reports_pending(tmp_path: Path) -> None:
    repository = CourseRepository(tmp_path / "search.db")
    result = CourseResult("课堂", "工科", "mic", [], "")
    repository.create_course(result)
    segment = Segment("Congestion window", "", 500, 2500)
    repository.add_segment(result.id, segment, 0, "retry")

    rows = repository.list_courses(query="Congestion")
    assert len(rows) == 1
    assert rows[0]["pending_count"] == 1
    assert rows[0]["duration_ms"] == 2500


def test_course_topics_survive_reload_and_follow_course_deletion(tmp_path: Path) -> None:
    path = tmp_path / "topics.db"
    repository = CourseRepository(path)
    result = CourseResult("课堂", "网络", "mic", [], "")
    repository.create_course(result)
    repository.add_course_topic(result.id, 0, " TCP   基础 ")
    repository.add_course_topic(result.id, 125_000, "慢启动")

    reopened = CourseRepository(path)
    rows = reopened.get_course_topics(result.id)
    assert [(row["start_ms"], row["title"]) for row in rows] == [
        (0, "TCP 基础"), (125_000, "慢启动")
    ]
    assert reopened.delete_course(result.id)
    assert reopened.get_course_topics(result.id) == []
