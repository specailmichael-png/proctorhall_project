import sqlite3
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from exams.models import Answer, Attempt, Exam, ProctorLog, Question, QuestionSnapshot, User

EVENT_MAP = {
    "no_face": "NO_FACE", "multiple_faces": "MULTIPLE_FACE", "look_away": "LOOK_AWAY",
    "phone_detected": "DEVICE_DETECTED", "device_detected": "DEVICE_DETECTED",
    "tab_blur": "TAB_SWITCH", "window_blur": "WINDOW_BLUR",
    "fullscreen_exit": "FULLSCREEN_EXIT", "no_camera": "CAMERA_INTERRUPTION",
    "copy_attempt": "COPY_ATTEMPT", "paste_attempt": "PASTE_ATTEMPT",
}


def parse_datetime(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


class Command(BaseCommand):
    help = "Import existing ProctorHall Flask/SQLite users, exams, attempts, answers, and logs."

    def add_arguments(self, parser):
        default_path = Path(__file__).resolve().parents[3] / "instance" / "proctorhall.db"
        parser.add_argument("--source", default=str(default_path), help="Path to the legacy Flask SQLite database")

    def handle(self, *args, **options):
        path = Path(options["source"])
        if not path.is_file():
            raise CommandError(f"Legacy SQLite database not found: {path}")
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"user", "exam", "question", "attempt", "question_snapshot", "answer", "proctor_log"}
        if not required.issubset(tables):
            connection.close()
            raise CommandError("Database is missing one or more expected Flask tables.")
        counts = {}
        with connection, transaction.atomic():
            for row in connection.execute("SELECT * FROM user"):
                User.objects.update_or_create(pk=row["id"], defaults={
                    "email": row["email"], "name": row["name"], "role": row["role"],
                    "legacy_password_hash": row["password_hash"], "password": "!",
                    "is_active": True, "is_staff": row["role"] == "admin",
                })
            counts["users"] = connection.execute("SELECT COUNT(*) FROM user").fetchone()[0]

            for row in connection.execute("SELECT * FROM exam"):
                Exam.objects.update_or_create(pk=row["id"], defaults={
                    "title": row["title"], "description": row["description"] or "",
                    "duration_minutes": row["duration_minutes"],
                    "created_by_id": row["created_by"],
                    "created_at": parse_datetime(row["created_at"]),
                    "is_published": bool(row["is_published"]),
                })
            counts["exams"] = connection.execute("SELECT COUNT(*) FROM exam").fetchone()[0]

            for row in connection.execute("SELECT * FROM question"):
                Question.objects.update_or_create(pk=row["id"], defaults={
                    "exam_id": row["exam_id"], "text": row["text"], "kind": Question.Kind.MCQ,
                    "option_a": row["option_a"], "option_b": row["option_b"],
                    "option_c": row["option_c"], "option_d": row["option_d"],
                    "correct_option": row["correct_option"], "marks": 1,
                })
            counts["questions"] = connection.execute("SELECT COUNT(*) FROM question").fetchone()[0]

            for row in connection.execute("SELECT * FROM attempt"):
                Attempt.objects.update_or_create(pk=row["id"], defaults={
                    "exam_id": row["exam_id"], "student_id": row["student_id"],
                    "started_at": parse_datetime(row["started_at"]) or timezone.now(),
                    "duration_minutes": Exam.objects.filter(pk=row["exam_id"]).values_list("duration_minutes", flat=True).first() or 30,
                    "submitted_at": parse_datetime(row["submitted_at"]),
                    "score": row["score"],
                    "status": Attempt.Status.IN_PROGRESS if row["status"] == "in_progress" else Attempt.Status.SUBMITTED,
                })
            counts["attempts"] = connection.execute("SELECT COUNT(*) FROM attempt").fetchone()[0]

            for row in connection.execute("SELECT * FROM question_snapshot"):
                QuestionSnapshot.objects.update_or_create(
                    attempt_id=row["attempt_id"], question_id_at_start=row["question_id"], defaults={
                        "question_id": row["question_id"], "text": row["text"], "kind": Question.Kind.MCQ,
                        "option_a": row["option_a"], "option_b": row["option_b"],
                        "option_c": row["option_c"], "option_d": row["option_d"],
                        "correct_option": row["correct_option"], "marks": 1,
                    },
                )
            counts["snapshots"] = connection.execute("SELECT COUNT(*) FROM question_snapshot").fetchone()[0]

            for row in connection.execute("SELECT * FROM answer"):
                Answer.objects.update_or_create(
                    attempt_id=row["attempt_id"], question_id_at_start=row["question_id"], defaults={
                        "question_id": row["question_id"], "selected_option": row["selected_option"] or "",
                    },
                )
            counts["answers"] = connection.execute("SELECT COUNT(*) FROM answer").fetchone()[0]

            for row in connection.execute("SELECT * FROM proctor_log"):
                old_type = row["event_type"].lower()
                ProctorLog.objects.update_or_create(pk=row["id"], defaults={
                    "attempt_id": row["attempt_id"], "event_type": EVENT_MAP.get(old_type, "LEGACY_EVENT"),
                    "detail": row["detail"] or "", "severity": row["severity"] or "warning",
                    "timestamp": parse_datetime(row["timestamp"]) or timezone.now(),
                })
            counts["activity logs"] = connection.execute("SELECT COUNT(*) FROM proctor_log").fetchone()[0]
        connection.close()
        self.stdout.write(self.style.SUCCESS("Imported legacy data: " + ", ".join(f"{key}={value}" for key, value in counts.items())))
