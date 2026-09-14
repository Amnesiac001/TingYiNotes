from __future__ import annotations

import sqlite3
import json
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from .models import CourseResult, Segment, utc_now

if TYPE_CHECKING:
    from .usage import TextUsage


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
    export_path TEXT NOT NULL DEFAULT '',
    notes_draft_ready INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS segment_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id TEXT NOT NULL,
    previous_original_text TEXT NOT NULL,
    previous_translated_text TEXT NOT NULL,
    corrected_at TEXT NOT NULL,
    FOREIGN KEY (segment_id) REFERENCES segments(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_segment_corrections_latest
ON segment_corrections(segment_id, id);
CREATE TABLE IF NOT EXISTS course_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    title TEXT NOT NULL,
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_course_topics_order
ON course_topics(course_id, start_ms, id);
CREATE TABLE IF NOT EXISTS subject_terms (
    subject_key TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    english_key TEXT NOT NULL,
    english TEXT NOT NULL,
    chinese TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subject_key, english_key)
);
CREATE INDEX IF NOT EXISTS idx_subject_terms_subject
ON subject_terms(subject_key, english_key);
CREATE TABLE IF NOT EXISTS text_usage_events (
    course_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    request_id TEXT NOT NULL,
    model TEXT NOT NULL,
    phase TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cached_input_tokens INTEGER,
    estimated_cost_usd TEXT,
    used_at TEXT NOT NULL,
    PRIMARY KEY (course_id, provider, request_id),
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_text_usage_course
ON text_usage_events(course_id, used_at);
CREATE TABLE IF NOT EXISTS course_quality_metrics (
    course_id TEXT PRIMARY KEY,
    summary_json TEXT NOT NULL,
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
);
"""


class CourseRepository:
    MAX_SUBJECT_TERMS = 80

    def save_course_quality_metrics(self, course_id: str, summary: dict[str, object]) -> None:
        """Persist only bounded timing aggregates, never audio or transcript copies."""
        encoded = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > 4096:
            raise ValueError("课堂性能统计过大，已拒绝保存。")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO course_quality_metrics (course_id, summary_json)
                   VALUES (?, ?) ON CONFLICT(course_id) DO UPDATE SET summary_json = excluded.summary_json""",
                (course_id, encoded),
            )

    def get_course_quality_metrics(self, course_id: str) -> dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT summary_json FROM course_quality_metrics WHERE course_id = ?", (course_id,)
            ).fetchone()
        if row is None:
            return {}
        try:
            value = json.loads(str(row["summary_json"]))
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

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

    @staticmethod
    def _term_key(value: str) -> str:
        return " ".join(value.split()).casefold()

    def get_subject_terms(self, subject: str) -> dict[str, str]:
        key = self._term_key(subject)
        if not key:
            return {}
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT english, chinese FROM subject_terms
                   WHERE subject_key = ? ORDER BY english_key LIMIT ?""",
                (key, self.MAX_SUBJECT_TERMS),
            ).fetchall()
        return {str(row["english"]): str(row["chinese"]) for row in rows}

    def save_subject_term(self, subject: str, english: str, chinese: str) -> None:
        subject = " ".join(subject.split())
        english = " ".join(english.split())
        chinese = " ".join(chinese.split())
        if not subject or not english or not chinese:
            raise ValueError("课程领域、英文术语和中文释义都不能为空。")
        if len(subject) > 80 or len(english) > 100 or len(chinese) > 160:
            raise ValueError("术语过长，请缩短课程领域、英文或中文释义。")
        subject_key, english_key = self._term_key(subject), self._term_key(english)
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM subject_terms WHERE subject_key = ? AND english_key = ?",
                (subject_key, english_key),
            ).fetchone()
            if existing is None:
                count = connection.execute(
                    "SELECT COUNT(*) FROM subject_terms WHERE subject_key = ?",
                    (subject_key,),
                ).fetchone()[0]
                if count >= self.MAX_SUBJECT_TERMS:
                    raise ValueError(f"每个课程领域最多保存 {self.MAX_SUBJECT_TERMS} 条术语。")
            connection.execute(
                """INSERT INTO subject_terms
                   (subject_key, subject_name, english_key, english, chinese, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(subject_key, english_key) DO UPDATE SET
                     subject_name=excluded.subject_name, english=excluded.english,
                     chinese=excluded.chinese, updated_at=excluded.updated_at""",
                (subject_key, subject, english_key, english, chinese, utc_now()),
            )

    def delete_subject_term(self, subject: str, english: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM subject_terms WHERE subject_key = ? AND english_key = ?",
                (self._term_key(subject), self._term_key(english)),
            )
            return cursor.rowcount > 0

    def add_text_usage(
        self, course_id: str, usage: TextUsage, estimated_cost_usd: Decimal | None,
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO text_usage_events
                   (course_id, provider, request_id, model, phase, input_tokens,
                    output_tokens, cached_input_tokens, estimated_cost_usd, used_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    course_id, usage.provider, usage.request_id, usage.model, usage.phase,
                    usage.input_tokens, usage.output_tokens, usage.cached_input_tokens,
                    str(estimated_cost_usd) if estimated_cost_usd is not None else None,
                    usage.used_at.isoformat(),
                ),
            )
            return cursor.rowcount > 0

    def get_text_usage_summary(self, course_id: str) -> dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT phase, input_tokens, output_tokens, cached_input_tokens,
                          estimated_cost_usd FROM text_usage_events WHERE course_id = ?""",
                (course_id,),
            ).fetchall()
        exact = [row for row in rows if row["input_tokens"] is not None and row["output_tokens"] is not None]
        priced = [row for row in exact if row["estimated_cost_usd"] is not None]
        phases = {}
        for phase in {str(row["phase"]) for row in rows}:
            phase_rows = [row for row in rows if row["phase"] == phase]
            phase_exact = [
                row for row in phase_rows
                if row["input_tokens"] is not None and row["output_tokens"] is not None
            ]
            phases[phase] = {
                "requests": len(phase_rows),
                "input_tokens": sum(int(row["input_tokens"]) for row in phase_exact),
                "output_tokens": sum(int(row["output_tokens"]) for row in phase_exact),
            }
        return {
            "requests": len(rows),
            "exact_requests": len(exact),
            "unknown_requests": len(rows) - len(exact),
            "unpriced_requests": len(exact) - len(priced),
            "input_tokens": sum(int(row["input_tokens"]) for row in exact),
            "output_tokens": sum(int(row["output_tokens"]) for row in exact),
            "cached_input_tokens": sum(int(row["cached_input_tokens"] or 0) for row in exact),
            "estimated_cost_usd": sum(
                (Decimal(str(row["estimated_cost_usd"])) for row in priced), Decimal("0")
            ),
            "phases": phases,
        }

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
                "notes_draft_ready": "ALTER TABLE courses ADD COLUMN notes_draft_ready INTEGER NOT NULL DEFAULT 0",
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
            connection.execute(
                "UPDATE courses SET notes_draft_ready = 0 WHERE id = ?", (course_id,)
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

    def correct_segment(
        self,
        course_id: str,
        segment_id: str,
        original_text: str,
        translated_text: str,
        expected_original: str,
        expected_translation: str,
        keep_translation_confirmed: bool = False,
    ) -> bool:
        """Atomically save a post-class correction and flag generated notes as stale."""
        original = original_text.strip()
        translation = translated_text.strip()
        if not original:
            raise ValueError("英文原文不能为空。")
        with self.connect() as connection:
            row = connection.execute(
                """SELECT s.original_text, s.translated_text, s.translation_status, c.status
                   FROM segments s JOIN courses c ON c.id = s.course_id
                   WHERE s.id = ? AND s.course_id = ?""",
                (segment_id, course_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("这句课堂记录不存在，请刷新课程库。")
            if str(row["status"]) in {"recording", "transcribing", "translating", "organizing"}:
                raise RuntimeError("课堂仍在处理，请结束后再校对字幕。")
            if str(row["translation_status"]) == "translating":
                raise RuntimeError("这句仍在后台翻译，请稍后再校对。")
            if (str(row["original_text"]), str(row["translated_text"])) != (
                expected_original, expected_translation
            ):
                raise RuntimeError("这句内容已被其他操作更新，请重新打开校对窗口。")
            if (
                original != expected_original
                and translation == expected_translation
                and translation
                and not keep_translation_confirmed
            ):
                raise ValueError("英文已修改：请同时校对中文，或清空中文以便稍后补译。")
            if (original, translation) == (expected_original, expected_translation):
                return False
            connection.execute(
                """INSERT INTO segment_corrections
                   (segment_id, previous_original_text, previous_translated_text, corrected_at)
                   VALUES (?, ?, ?, ?)""",
                (segment_id, expected_original, expected_translation, utc_now()),
            )
            cursor = connection.execute(
                """UPDATE segments
                   SET original_text = ?, translated_text = ?, translation_status = ?,
                       translation_error = ''
                   WHERE id = ? AND course_id = ?
                     AND original_text = ? AND translated_text = ?""",
                (
                    original, translation, "completed" if translation else "retry",
                    segment_id, course_id, expected_original, expected_translation,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("这句内容已被其他操作更新，请重新打开校对窗口。")
            connection.execute(
                """UPDATE courses
                   SET status = 'needs_attention', updated_at = ?,
                       error_message = '字幕已人工校对，整理笔记可能过时；请在课程库重新整理。',
                       notes_draft_ready = 0
                   WHERE id = ?""",
                (utc_now(), course_id),
            )
        return True

    def has_segment_corrections(self, segment_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM segment_corrections WHERE segment_id = ? LIMIT 1",
                (segment_id,),
            ).fetchone()
            return row is not None

    def undo_last_segment_correction(
        self,
        course_id: str,
        segment_id: str,
        expected_original: str,
        expected_translation: str,
    ) -> bool:
        """Restore the most recent saved version without losing older revisions."""
        with self.connect() as connection:
            row = connection.execute(
                """SELECT s.original_text, s.translated_text, s.translation_status, c.status
                   FROM segments s JOIN courses c ON c.id = s.course_id
                   WHERE s.id = ? AND s.course_id = ?""",
                (segment_id, course_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("这句课堂记录不存在，请刷新课程库。")
            if str(row["status"]) in {"recording", "transcribing", "translating", "organizing"}:
                raise RuntimeError("课堂仍在处理，请结束后再撤销校对。")
            if str(row["translation_status"]) == "translating":
                raise RuntimeError("这句仍在后台翻译，请稍后再撤销。")
            if (str(row["original_text"]), str(row["translated_text"])) != (
                expected_original, expected_translation
            ):
                raise RuntimeError("这句内容已被其他操作更新，请重新打开校对窗口。")
            revision = connection.execute(
                """SELECT id, previous_original_text, previous_translated_text
                   FROM segment_corrections WHERE segment_id = ? ORDER BY id DESC LIMIT 1""",
                (segment_id,),
            ).fetchone()
            if revision is None:
                return False
            previous_original = str(revision["previous_original_text"])
            previous_translation = str(revision["previous_translated_text"])
            cursor = connection.execute(
                """UPDATE segments SET original_text = ?, translated_text = ?,
                   translation_status = ?, translation_error = ''
                   WHERE id = ? AND course_id = ?
                     AND original_text = ? AND translated_text = ?""",
                (previous_original, previous_translation,
                 "completed" if previous_translation else "retry", segment_id, course_id,
                 expected_original, expected_translation),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("这句内容已被其他操作更新，请重新打开校对窗口。")
            connection.execute("DELETE FROM segment_corrections WHERE id = ?", (revision["id"],))
            connection.execute(
                """UPDATE courses SET status = 'needs_attention', updated_at = ?,
                   error_message = '字幕校对已撤销，整理笔记可能过时；请在课程库重新整理。',
                   notes_draft_ready = 0
                   WHERE id = ?""",
                (utc_now(), course_id),
            )
        return True

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
                connection.execute(
                    """UPDATE courses SET notes_draft_ready = 0
                       WHERE id = (SELECT course_id FROM segments WHERE id = ?)""",
                    (segment_id,),
                )

    def pending_segments(self, course_id: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """SELECT * FROM segments
                       WHERE course_id = ?
                         AND translation_status IN ('pending', 'retry', 'failed', 'translating')
                       ORDER BY start_ms, end_ms, sort_order""",
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
                       error_message = '', notes_draft_ready = 0,
                       export_path = CASE WHEN ? <> '' THEN ? ELSE export_path END
                   WHERE id = ?""",
                (notes_markdown, utc_now(), export_path, export_path, course_id),
            )

    def save_notes_draft(
        self, course_id: str, notes_markdown: str, *, reusable: bool = True
    ) -> None:
        """Keep organized notes recoverable until their file export succeeds."""
        with self.connect() as connection:
            connection.execute(
                """UPDATE courses SET notes_markdown = ?, notes_draft_ready = ?,
                   updated_at = ? WHERE id = ?""",
                (notes_markdown, int(reusable), utc_now(), course_id),
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
                    """SELECT * FROM segments WHERE course_id = ?
                       ORDER BY start_ms, end_ms, sort_order""",
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

    def delete_course_if_inactive(self, course_id: str) -> bool:
        """Atomically refuse user deletion while a class is still processing."""
        with self.connect() as connection:
            cursor = connection.execute(
                """DELETE FROM courses WHERE id = ?
                   AND status NOT IN ('recording', 'transcribing', 'translating', 'organizing')""",
                (course_id,),
            )
            return cursor.rowcount > 0
