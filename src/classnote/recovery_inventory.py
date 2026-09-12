from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID
import wave

from .storage import CourseRepository


@dataclass(frozen=True)
class RecoveryCandidate:
    course_id: str | None
    title: str
    subject: str
    status: str
    segment_count: int
    pending_count: int
    audio_path: Path | None

    @property
    def can_resume(self) -> bool:
        return self.course_id is not None and self.segment_count > 0


def _playable_audio(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with wave.open(str(path), "rb") as recording:
            return recording.getnframes() > 0 and recording.getframerate() > 0
    except (OSError, EOFError, wave.Error):
        return False


def course_temporary_audio_path(repository: CourseRepository, course_id: str) -> Path | None:
    try:
        valid_id = str(UUID(course_id))
    except ValueError:
        return None
    path = repository.database_path.parent / "temporary-audio" / f"{valid_id}.wav"
    return path if _playable_audio(path) else None


def list_recovery_candidates(repository: CourseRepository, limit: int = 100) -> list[RecoveryCandidate]:
    """Find actionable unfinished courses and unlinked opt-in audio backups."""
    with repository.connect() as connection:
        rows = connection.execute(
            """SELECT c.id, c.title, c.subject, c.status,
                      COUNT(s.id) AS segment_count,
                      SUM(CASE WHEN s.id IS NOT NULL AND s.translation_status <> 'completed'
                          THEN 1 ELSE 0 END) AS pending_count
               FROM courses c LEFT JOIN segments s ON s.course_id = c.id
               WHERE c.status IN ('interrupted', 'needs_attention', 'failed')
               GROUP BY c.id ORDER BY c.updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    candidates: list[RecoveryCandidate] = []
    for row in rows:
        course_id = str(row["id"])
        audio = course_temporary_audio_path(repository, course_id)
        count = int(row["segment_count"])
        if not count and audio is None:
            continue
        candidates.append(RecoveryCandidate(
            course_id, str(row["title"]), str(row["subject"]), str(row["status"]),
            count, int(row["pending_count"] or 0), audio,
        ))

    audio_root = repository.database_path.parent / "temporary-audio"
    if audio_root.is_dir():
        available: list[tuple[float, Path]] = []
        for path in audio_root.glob("*.wav"):
            try:
                if _playable_audio(path):
                    available.append((path.stat().st_mtime, path))
            except OSError:
                continue
        for _, path in sorted(available, key=lambda item: item[0], reverse=True):
            if len(candidates) >= limit:
                break
            try:
                course_id = str(UUID(path.stem))
            except ValueError:
                continue
            if repository.get_course(course_id) is not None:
                continue
            candidates.append(RecoveryCandidate(
                None, "未关联的课堂录音", "通用课程", "audio_only", 0, 0, path,
            ))
    return candidates
