from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import CourseResult, Segment, utc_now


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    subject TEXT NOT NULL,
    source_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    notes_markdown TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'completed',
    updated_at TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    export_path TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    original_text TEXT NOT NULL,
    translated_text TEXT NOT NULL DEFAULT '',
    translation_status TEXT NOT NULL DEFAULT 'completed',
    translation_error TEXT NOT NULL DEFAULT '',
    marker TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL,
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_segments_course_order
ON segments(course_id, sort_order);
CREATE TABLE IF NOT EXISTS course_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    title TEXT NOT NULL,
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_course_topics_order
ON course_topics(course_id, start_ms, id);
"""


class CourseRepository:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            course_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(courses)").fetchall()
            }
            course_migrations = {
                "status": "ALTER TABLE courses ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'",
                "updated_at": "ALTER TABLE courses ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
                "error_message": "ALTER TABLE courses ADD COLUMN error_message TEXT NOT NULL DEFAULT ''",
                "export_path": "ALTER TABLE courses ADD COLUMN export_path TEXT NOT NULL DEFAULT ''",
            }
            for column, statement in course_migrations.items():
                if column not in course_columns:
                    connection.execute(statement)
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(segments)").fetchall()
            }
            if "translation_status" not in columns:
                connection.execute(
                    "ALTER TABLE segments ADD COLUMN translation_status TEXT NOT NULL DEFAULT 'completed'"
                )
            if "translation_error" not in columns:
                connection.execute(
                    "ALTER TABLE segments ADD COLUMN translation_error TEXT NOT NULL DEFAULT ''"
                )
            if "marker" not in columns:
                connection.execute(
                    "ALTER TABLE segments ADD COLUMN marker TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                """UPDATE segments SET translation_status = 'pending'
                   WHERE translated_text = '' AND translation_status = 'completed'"""
            )
            connection.execute(
                """UPDATE segments
                   SET translation_status = CASE
                       WHEN translated_text = '' THEN 'pending'
                       ELSE 'completed'
                   END
                   WHERE translation_status NOT IN ('pending', 'translating', 'completed', 'retry', 'failed')"""
            )
            connection.execute(
                "UPDATE courses SET updated_at = created_at WHERE updated_at = ''"
            )
            # Legacy rows had no lifecycle field. Preserve completed notes and make
            # incomplete records visible instead of pretending they succeeded.
            connection.execute(
                """UPDATE courses
                   SET status = CASE
                       WHEN notes_markdown <> '' THEN 'completed'
                       WHEN EXISTS (SELECT 1 FROM segments s WHERE s.course_id = courses.id)
                           THEN 'needs_attention'
                       ELSE 'interrupted'
                   END
                   WHERE status NOT IN (
                       'recording', 'transcribing', 'translating', 'organizing',
                       'completed', 'needs_attention', 'interrupted', 'failed'
                   ) OR (status = 'completed' AND notes_markdown = '')"""
            )
            connection.execute(
                """UPDATE courses
                   SET status = 'needs_attention', updated_at = ?,
                       error_message = CASE WHEN error_message = ''
                           THEN '部分中文尚未完成，可在课程库中补译并重新整理。'
                           ELSE error_message END
                   WHERE status = 'completed' AND EXISTS (
                       SELECT 1 FROM segments s
                       WHERE s.course_id = courses.id
                         AND s.translation_status <> 'completed'
                   )""",
                (utc_now(),),
            )

    def save(self, result: CourseResult) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO courses
                   (id, title, subject, source_path, created_at, notes_markdown,
                    status, updated_at, error_message, export_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '')""",
                (
                    result.id,
                    result.title,
                    result.subject,
                    result.source_path,
                    result.created_at,
                    result.notes_markdown,
                    "completed" if result.notes_markdown else "needs_attention",
                    now,
                ),
            )
            connection.executemany(
                """INSERT INTO segments
                   (id, course_id, start_ms, end_ms, original_text,
                    translated_text, translation_status, translation_error, marker, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)""",
                [
                    (
                        segment.id,
                        result.id,
                        segment.start_ms,
                        segment.end_ms,
                        segment.original_text,
                        segment.translated_text,
                        "completed" if segment.translated_text else "pending",
                        segment.marker,
                        index,
                    )
                    for index, segment in enumerate(result.segments)
                ],
            )

    def create_course(self, result: CourseResult, status: str = "recording") -> None:
        """Create an empty live course before audio processing begins."""
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO courses
                   (id, title, subject, source_path, created_at, notes_markdown,
                    status, updated_at, error_message, export_path)
                   VALUES (?, ?, ?, ?, ?, '', ?, ?, '', '')""",
                (
                    result.id,
                    result.title,
                    result.subject,
                    result.source_path,
                    result.created_at,
                    status,
                    utc_now(),
                ),
            )

    def set_course_state(
        self,
        course_id: str,
        status: str,
        error_message: str = "",
        export_path: str | None = None,
    ) -> None:
        """Persist lifecycle progress so interrupted work can be recovered later."""
        with self.connect() as connection:
            if export_path is None:
                connection.execute(
                    """UPDATE courses
                       SET status = ?, updated_at = ?, error_message = ?
                       WHERE id = ?""",
                    (status, utc_now(), error_message, course_id),
                )
            else:
                connection.execute(
                    """UPDATE courses
                       SET status = ?, updated_at = ?, error_message = ?, export_path = ?
                       WHERE id = ?""",
                    (status, utc_now(), error_message, export_path, course_id),
                )

    def mark_active_courses_interrupted(self) -> int:
        """Mark work left active by a previous process without touching completed data."""
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE courses
                   SET status = 'interrupted', updated_at = ?,
                       error_message = CASE WHEN error_message = ''
                           THEN '软件上次退出时课堂尚未完成，可在课程库中继续处理。'
                           ELSE error_message END
                   WHERE status IN ('recording', 'transcribing', 'translating', 'organizing')""",
                (utc_now(),),
            )
            connection.execute(
                """UPDATE segments SET translation_status = 'retry'
                   WHERE translation_status = 'translating'"""
            )
            return cursor.rowcount

    def add_segment(
        self,
        course_id: str,
        segment: Segment,
        sort_order: int,
        translation_status: str | None = None,
    ) -> None:
        """Persist stable English immediately, independently of translation."""
        status = translation_status or ("completed" if segment.translated_text else "pending")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO segments
                   (id, course_id, start_ms, end_ms, original_text,
                    translated_text, translation_status, translation_error, marker, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)""",
                (
                    segment.id,
                    course_id,
                    segment.start_ms,
                    segment.end_ms,
                    segment.original_text,
                    segment.translated_text,
                    status,
                    segment.marker,
                    sort_order,
                ),
            )

    def set_segment_marker(self, segment_id: str, marker: str) -> None:
        if marker not in {"", "important", "question"}:
            raise ValueError("字幕标记只能是重点、疑问或空白。")
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE segments SET marker = ? WHERE id = ?", (marker, segment_id)
            )
            if cursor.rowcount == 0:
                raise RuntimeError("没有找到需要标记的课堂字幕。")

    def set_translation_state(
        self,
        segment_id: str,
        status: str,
        translated_text: str | None = None,
        error: str = "",
    ) -> None:
        """Update a translation without replacing the already-safe English text."""
        with self.connect() as connection:
            if translated_text is None:
                connection.execute(
                    "UPDATE segments SET translation_status = ?, translation_error = ? WHERE id = ?",
                    (status, error, segment_id),
                )
            else:
                connection.execute(
                    """UPDATE segments
                       SET translated_text = ?, translation_status = ?, translation_error = ?
                       WHERE id = ?""",
                    (translated_text, status, error, segment_id),
                )

    def pending_segments(self, course_id: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM segments
                       WHERE course_id = ?
                         AND translation_status IN ('pending', 'retry', 'failed', 'translating')
                       ORDER BY sort_order, start_ms""",
                    (course_id,),
                ).fetchall()
            )

    def finalize_course(
        self,
        course_id: str,
        notes_markdown: str,
        export_path: str = "",
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE courses
                   SET notes_markdown = ?, status = 'completed', updated_at = ?,
                       error_message = '', export_path = CASE WHEN ? <> '' THEN ? ELSE export_path END
                   WHERE id = ?""",
                (notes_markdown, utc_now(), export_path, export_path, course_id),
            )

    def count_courses(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM courses").fetchone()
            return int(row["count"])

    def list_courses(self, limit: int = 100, query: str = "") -> list[sqlite3.Row]:
        aggregate = """SELECT c.*, COUNT(s.id) AS segment_count,
                               COALESCE(MAX(s.end_ms), 0) AS duration_ms,
                               COALESCE(SUM(CASE WHEN s.id IS NOT NULL
                                   AND s.translation_status <> 'completed' THEN 1 ELSE 0 END), 0)
                                   AS pending_count
                        FROM courses c
                        LEFT JOIN segments s ON s.course_id = c.id"""
        with self.connect() as connection:
            if query.strip():
                pattern = f"%{query.strip()}%"
                return list(
                    connection.execute(
                        aggregate
                        + """ WHERE c.title LIKE ? OR c.subject LIKE ?
                              OR c.notes_markdown LIKE ?
                              OR EXISTS (
                                  SELECT 1 FROM segments search_segments
                                  WHERE search_segments.course_id = c.id
                                    AND (search_segments.original_text LIKE ?
                                         OR search_segments.translated_text LIKE ?)
                              )
                           GROUP BY c.id
                           ORDER BY c.created_at DESC
                           LIMIT ?""",
                        (pattern, pattern, pattern, pattern, pattern, limit),
                    ).fetchall()
                )
            return list(
                connection.execute(
                    aggregate
                    + """ GROUP BY c.id
                           ORDER BY c.created_at DESC
                           LIMIT ?""",
                    (limit,),
                ).fetchall()
            )

    def get_course(self, course_id: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM courses WHERE id = ?", (course_id,)
            ).fetchone()

    def get_course_segments(self, course_id: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM segments WHERE course_id = ? ORDER BY sort_order, start_ms",
                    (course_id,),
                ).fetchall()
            )

    def add_course_topic(self, course_id: str, start_ms: int, title: str) -> int:
        clean_title = " ".join(title.split())[:80]
        if not clean_title:
            raise ValueError("课堂主题不能为空。")
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO course_topics (course_id, start_ms, title) VALUES (?, ?, ?)",
                (course_id, max(0, int(start_ms)), clean_title),
            )
            return int(cursor.lastrowid)

    def get_course_topics(self, course_id: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(
                "SELECT id, start_ms, title FROM course_topics WHERE course_id = ? ORDER BY start_ms, id",
                (course_id,),
            ).fetchall())

    def delete_course(self, course_id: str) -> bool:
        """Delete one in-app course and its segments; exported files are intentionally untouched."""
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM courses WHERE id = ?", (course_id,))
            return cursor.rowcount > 0
