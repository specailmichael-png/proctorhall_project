import uuid
import re
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
from django.core.validators import RegexValidator


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, name, password=None, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        if (
            extra_fields.get("role", "student") == "student"
            and not extra_fields.get("student_id")
            and re.fullmatch(r"BIT\d{8}", name)
        ):
            extra_fields["student_id"] = name
        user = self.model(email=self.normalize_email(email), name=name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, name, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "admin")
        if extra_fields.get("is_staff") is not True or extra_fields.get("is_superuser") is not True:
            raise ValueError("Superusers must have staff and superuser status.")
        return self.create_user(email, name, password, **extra_fields)


class User(AbstractUser):
    username = None
    ROLE_CHOICES = [("student", "Student"), ("admin", "Administrator")]
    name = models.CharField(max_length=120)
    email = models.EmailField(unique=True)
    student_id = models.CharField(
        max_length=11,
        unique=True,
        null=True,
        blank=True,
        validators=[RegexValidator(r"^BIT\d{8}$", "Enter a University of Lusaka student ID in the format BIT23240508.")],
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="student")
    legacy_password_hash = models.CharField(max_length=255, blank=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]
    objects = UserManager()

    @property
    def is_admin(self):
        return self.role == "admin" or self.is_superuser

    @property
    def initial(self):
        return (self.name or self.email or "?").strip()[:1].upper()

    def __str__(self):
        return self.name or self.email


class Exam(models.Model):
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="exams")
    created_at = models.DateTimeField(default=timezone.now)
    is_published = models.BooleanField(default=False)
    access_password_hash = models.CharField(max_length=128, blank=True)

    @property
    def has_access_password(self):
        return bool(self.access_password_hash)

    def set_access_password(self, raw_password):
        self.access_password_hash = make_password(raw_password)

    def check_access_password(self, raw_password):
        return self.has_access_password and check_password(raw_password, self.access_password_hash)

    @property
    def total_marks(self):
        return sum(question.marks for question in self.questions.all())

    @property
    def submitted_attempts(self):
        return [attempt for attempt in self.attempts.all() if attempt.status != Attempt.Status.IN_PROGRESS]

    @property
    def pass_rate(self):
        done = self.submitted_attempts
        if not done or not self.total_marks:
            return 0
        return round(sum(1 for attempt in done if (attempt.score or 0) >= self.total_marks * 0.5) / len(done) * 100)

    def __str__(self):
        return self.title


class Question(models.Model):
    class Kind(models.TextChoices):
        MCQ = "mcq", "Multiple choice"
        PROGRAMMING = "programming", "Programming"

    LANGUAGES = [
        ("python", "Python"), ("javascript", "JavaScript"),
        ("java", "Java"), ("cpp", "C++"), ("csharp", "C#"),
    ]
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.MCQ)
    option_a = models.CharField(max_length=255, blank=True)
    option_b = models.CharField(max_length=255, blank=True)
    option_c = models.CharField(max_length=255, blank=True)
    option_d = models.CharField(max_length=255, blank=True)
    correct_option = models.CharField(max_length=1, blank=True)
    programming_language = models.CharField(max_length=20, choices=LANGUAGES, blank=True)
    marks = models.PositiveSmallIntegerField(default=1)
    code_editor_required = models.BooleanField(default=False)
    code_execution_allowed = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return f"{self.exam.title}: {self.text[:80]}"


class Attempt(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        SUBMITTED = "submitted", "Submitted"

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="attempts")
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    score = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    session_nonce = models.UUIDField(default=uuid.uuid4, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["exam", "student"], name="uq_attempt_exam_student")]
        ordering = ["-started_at"]

    @property
    def violation_count(self):
        return self.logs.count()

    def __str__(self):
        return f"{self.student} - {self.exam}"


class QuestionSnapshot(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="snapshots")
    question = models.ForeignKey(Question, null=True, on_delete=models.SET_NULL, related_name="snapshots")
    question_id_at_start = models.PositiveIntegerField()
    text = models.TextField()
    kind = models.CharField(max_length=20, choices=Question.Kind.choices)
    option_a = models.CharField(max_length=255, blank=True)
    option_b = models.CharField(max_length=255, blank=True)
    option_c = models.CharField(max_length=255, blank=True)
    option_d = models.CharField(max_length=255, blank=True)
    correct_option = models.CharField(max_length=1, blank=True)
    programming_language = models.CharField(max_length=20, blank=True)
    marks = models.PositiveSmallIntegerField(default=1)
    code_editor_required = models.BooleanField(default=False)
    code_execution_allowed = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attempt", "question_id_at_start"], name="uq_snapshot_attempt_question")]
        ordering = ["id"]


class Answer(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, null=True, on_delete=models.SET_NULL, related_name="answers")
    question_id_at_start = models.PositiveIntegerField()
    selected_option = models.CharField(max_length=1, blank=True)
    source_code = models.TextField(blank=True)
    programming_language = models.CharField(max_length=20, blank=True)
    code_submitted_at = models.DateTimeField(null=True, blank=True)
    marks_awarded = models.PositiveSmallIntegerField(null=True, blank=True)
    lecturer_feedback = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attempt", "question_id_at_start"], name="uq_answer_attempt_question")]

    @property
    def final_version(self):
        return self.versions.filter(is_final=True).order_by("-saved_at").first()


class CodeVersion(models.Model):
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name="versions")
    source_code = models.TextField()
    programming_language = models.CharField(max_length=20)
    saved_at = models.DateTimeField(default=timezone.now)
    is_final = models.BooleanField(default=False)

    class Meta:
        ordering = ["saved_at", "id"]


class ProctorLog(models.Model):
    EVENT_TYPES = [(name, name.replace("_", " ").title()) for name in (
        "EXAM_STARTED", "EXAM_SUBMITTED", "CODE_SAVED", "CODE_SUBMITTED", "CODE_RUN",
        "TAB_SWITCH", "WINDOW_BLUR", "COPY_ATTEMPT", "PASTE_ATTEMPT",
        "CUT_ATTEMPT", "FULLSCREEN_EXIT", "MULTIPLE_LOGIN",
        "CAMERA_INTERRUPTION", "NO_FACE", "MULTIPLE_FACE", "LOOK_AWAY",
        "DEVICE_DETECTED", "MODEL_ERROR", "LOCKDOWN_WARNING", "EXAM_AUTO_SUBMITTED",
        "LEGACY_EVENT",
    )]
    SEVERITIES = [("info", "Info"), ("warning", "Warning"), ("critical", "Critical")]
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="logs")
    event_type = models.CharField(max_length=40, choices=EVENT_TYPES)
    detail = models.CharField(max_length=255, blank=True)
    severity = models.CharField(max_length=10, choices=SEVERITIES, default="warning")
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["timestamp", "id"]


class ActiveAttemptSession(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="active_sessions")
    session_token = models.CharField(max_length=64)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attempt", "session_token"], name="uq_active_attempt_session")]
