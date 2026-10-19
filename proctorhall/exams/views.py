import csv
import io
import json
import re
import secrets
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from werkzeug.security import check_password_hash

from .models import (
    ActiveAttemptSession, Answer, Attempt, CodeVersion, Exam, ProctorLog,
    Question, QuestionSnapshot, User,
)

MAX_SOURCE_LENGTH = 200_000
STUDENT_ID_PATTERN = re.compile(r"^BIT\d{8}$")
SESSION_STALE_AFTER = timedelta(seconds=90)
# Code is executed in the student's own browser (Pyodide/WebAssembly).
# The server never executes submitted source; it only records that a run happened.
BROWSER_RUNNER_LANGUAGE = "python"
# How many times a student may leave the secure exam screen before the
# exam is submitted automatically by the browser lock-down.
LOCKDOWN_MAX_WARNINGS = 3
EVENT_ALIASES = {
    "tab_blur": "TAB_SWITCH", "window_blur": "WINDOW_BLUR",
    "fullscreen_exit": "FULLSCREEN_EXIT", "multiple_faces": "MULTIPLE_FACE",
    "no_face": "NO_FACE", "no_camera": "CAMERA_INTERRUPTION",
    "camera_interruption": "CAMERA_INTERRUPTION", "look_away": "LOOK_AWAY",
    "device_detected": "DEVICE_DETECTED", "model_error": "MODEL_ERROR",
}
ALLOWED_EVENTS = {choice[0] for choice in ProctorLog.EVENT_TYPES}


def is_admin(user):
    return user.is_authenticated and user.is_admin


def admin_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_admin(request.user):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def attempt_deadline(attempt):
    return attempt.started_at + timedelta(minutes=attempt.duration_minutes)


def log_event(attempt, event_type, detail="", severity="info"):
    ProctorLog.objects.create(
        attempt=attempt,
        event_type=event_type if event_type in ALLOWED_EVENTS else "LEGACY_EVENT",
        detail=str(detail)[:255],
        severity=severity if severity in {"info", "warning", "critical"} else "warning",
    )


def ensure_snapshots(attempt):
    if attempt.snapshots.exists():
        return
    for question in attempt.exam.questions.order_by("position", "id"):
        QuestionSnapshot.objects.create(
            attempt=attempt,
            question=question,
            question_id_at_start=question.id,
            text=question.text,
            kind=question.kind,
            option_a=question.option_a,
            option_b=question.option_b,
            option_c=question.option_c,
            option_d=question.option_d,
            correct_option=question.correct_option.upper(),
            programming_language=question.programming_language,
            marks=question.marks,
            code_editor_required=question.code_editor_required,
            code_execution_allowed=question.code_execution_allowed,
        )


def get_answer(attempt, snapshot):
    answer, _ = Answer.objects.get_or_create(
        attempt=attempt,
        question_id_at_start=snapshot.question_id_at_start,
        defaults={"question": snapshot.question},
    )
    return answer


def save_version(answer, source_code, language, is_final=False):
    latest = answer.versions.order_by("-saved_at", "-id").first()
    if latest and latest.source_code == source_code and latest.programming_language == language:
        if is_final:
            answer.versions.filter(is_final=True).update(is_final=False)
            latest.is_final = True
            latest.save(update_fields=["is_final"])
        changed = False
    else:
        if is_final:
            answer.versions.filter(is_final=True).update(is_final=False)
        CodeVersion.objects.create(
            answer=answer,
            source_code=source_code,
            programming_language=language,
            is_final=is_final,
        )
        changed = True
    answer.source_code = source_code
    answer.programming_language = language
    if is_final:
        answer.code_submitted_at = timezone.now()
    answer.save(update_fields=["source_code", "programming_language", "code_submitted_at"] if is_final else ["source_code", "programming_language"])
    return changed


def finalize_attempt(attempt, payload_answers=None, code_answers=None):
    payload_answers = payload_answers or {}
    code_answers = code_answers or {}
    snapshots = list(attempt.snapshots.all())
    valid_by_id = {str(row.question_id_at_start): row for row in snapshots}
    points = 0

    for question_key, selected in payload_answers.items():
        snapshot = valid_by_id.get(str(question_key))
        if snapshot and snapshot.kind == Question.Kind.MCQ and selected in {"A", "B", "C", "D"}:
            answer = get_answer(attempt, snapshot)
            answer.selected_option = selected
            answer.marks_awarded = snapshot.marks if selected == snapshot.correct_option.upper() else 0
            answer.save(update_fields=["selected_option", "marks_awarded"])

    for snapshot in snapshots:
        answer = get_answer(attempt, snapshot)
        if snapshot.kind == Question.Kind.MCQ:
            points += answer.marks_awarded or 0
            continue

        submitted = code_answers.get(str(snapshot.question_id_at_start))
        if isinstance(submitted, dict):
            source = submitted.get("source_code", submitted.get("code", answer.source_code))
            language = submitted.get("language", snapshot.programming_language)
        else:
            source = submitted if isinstance(submitted, str) else answer.source_code
            language = snapshot.programming_language
        if not isinstance(source, str) or len(source) > MAX_SOURCE_LENGTH:
            raise ValueError("Programming source is too large or invalid.")
        if language != snapshot.programming_language:
            raise ValueError("Programming language does not match the question configuration.")
        if source:
            save_version(answer, source, language, is_final=True)
            log_event(attempt, "CODE_SUBMITTED", f"Final source submitted for question {snapshot.question_id_at_start}.")
        if answer.marks_awarded is not None:
            points += answer.marks_awarded

    attempt.score = points
    attempt.submitted_at = timezone.now()
    attempt.status = Attempt.Status.SUBMITTED
    log_event(attempt, "EXAM_SUBMITTED", "Examination submitted.")
    attempt.save(update_fields=["score", "submitted_at", "status"])


@require_GET
def home(request):
    return render(request, "index.html")


def register(request):
    if request.user.is_authenticated:
        return redirect("admin_dashboard" if request.user.is_admin else "student_dashboard")
    if request.method == "GET":
        return render(request, "register.html")
    name = request.POST.get("student_id", "").strip().upper()
    email = request.POST.get("email", "").strip().lower()
    password = request.POST.get("password", "")
    if not STUDENT_ID_PATTERN.fullmatch(name):
        messages.error(request, "Enter a valid University of Lusaka student ID in the format BIT23240508.")
        return redirect("register")
    if not email or len(password) < 8:
        messages.error(request, "Enter your email and use a password of at least 8 characters.")
        return redirect("register")
    if User.objects.filter(email=email).exists() or User.objects.filter(student_id=name).exists():
        messages.error(request, "An account with that email or student ID already exists.")
        return redirect("register")
    User.objects.create_user(email=email, name=name, password=password, role="student")
    messages.success(request, "Account created. Please sign in.")
    return redirect("login")


def login_view(request):
    if request.method == "GET":
        return render(request, "login.html")
    email = request.POST.get("email", "").strip().lower()
    password = request.POST.get("password", "")
    user = User.objects.filter(email=email).first()
    if not user and STUDENT_ID_PATTERN.fullmatch(email.upper()):
        user = User.objects.filter(student_id=email.upper()).first()
    if user:
        email = user.email
    authenticated = authenticate(request, username=email, password=password)
    if not authenticated and user and user.legacy_password_hash:
        try:
            if check_password_hash(user.legacy_password_hash, password):
                user.set_password(password)
                user.legacy_password_hash = ""
                user.save(update_fields=["password", "legacy_password_hash"])
                authenticated = user
        except (ValueError, TypeError):
            authenticated = None
    if not authenticated:
        messages.error(request, "Incorrect email or password.")
        return redirect("login")
    if not authenticated.is_admin and not (
        authenticated.student_id and STUDENT_ID_PATTERN.fullmatch(authenticated.student_id)
    ):
        messages.error(request, "This student account needs a valid University of Lusaka student ID. Contact an administrator.")
        return redirect("login")
    login(request, authenticated)
    return redirect("admin_dashboard" if authenticated.is_admin else "student_dashboard")


@login_required
def logout_view(request):
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    logout(request)
    messages.info(request, "Signed out.")
    return redirect("home")


@require_GET
@login_required
def student_dashboard(request):
    exams = Exam.objects.filter(
        is_published=True, access_password_hash__gt="",
    ).prefetch_related("questions").order_by("-created_at")
    my_attempts = {attempt.exam_id: attempt for attempt in Attempt.objects.filter(student=request.user)}
    for exam in exams:
        exam.student_attempt = my_attempts.get(exam.id)
    done = [attempt for attempt in my_attempts.values() if attempt.status != Attempt.Status.IN_PROGRESS]
    percentages = [round((attempt.score or 0) / attempt.exam.total_marks * 100) for attempt in done if attempt.exam.total_marks]
    return render(request, "student/dashboard.html", {
        "exams": exams, "my_attempts": my_attempts, "completed_count": len(done),
        "avg_score_pct": round(sum(percentages) / len(percentages)) if percentages else 0,
    })


@login_required
@require_http_methods(["GET", "POST"])
def start_exam(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, is_published=True)
    attempt = Attempt.objects.filter(exam=exam, student=request.user).first()
    if attempt:
        if attempt.status != Attempt.Status.IN_PROGRESS:
            messages.info(request, "You have already submitted this exam.")
            return redirect("student_dashboard")
        return redirect("take_exam", attempt_id=attempt.id)
    if not exam.has_access_password:
        messages.error(request, "This exam is not available yet. Contact your administrator.")
        return redirect("student_dashboard")
    if request.method == "GET":
        return render(request, "student/start_exam.html", {"exam": exam})
    if not exam.check_access_password(request.POST.get("exam_password", "")):
        messages.error(request, "Incorrect exam password.")
        return redirect("start_exam", exam_id=exam.id)
    attempt, created = Attempt.objects.get_or_create(
        exam=exam,
        student=request.user,
        defaults={"duration_minutes": exam.duration_minutes},
    )
    if created:
        ensure_snapshots(attempt)
        log_event(attempt, "EXAM_STARTED", "Examination started.")
        attempt.save()
    elif attempt.status != Attempt.Status.IN_PROGRESS:
        messages.info(request, "You have already submitted this exam.")
        return redirect("student_dashboard")
    return redirect("take_exam", attempt_id=attempt.id)


@login_required
def take_exam(request, attempt_id):
    attempt = get_object_or_404(Attempt.objects.select_related("exam", "student"), id=attempt_id)
    if attempt.student_id != request.user.id:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    if attempt.status != Attempt.Status.IN_PROGRESS:
        return redirect("result", attempt_id=attempt.id)
    ensure_snapshots(attempt)
    remaining_seconds = max(0, int((attempt_deadline(attempt) - timezone.now()).total_seconds()))
    if remaining_seconds <= 0:
        with transaction.atomic():
            finalize_attempt(attempt)
        return redirect("result", attempt_id=attempt.id)
    answers = {str(answer.question_id_at_start): answer for answer in attempt.answers.all()}
    snapshots = list(attempt.snapshots.all())
    for snapshot in snapshots:
        snapshot.current_answer = answers.get(str(snapshot.question_id_at_start))
    return render(request, "student/exam.html", {
        "exam": attempt.exam, "attempt": attempt, "snapshots": snapshots,
        "answers": answers, "remaining_seconds": remaining_seconds,
        "lockdown_max_warnings": LOCKDOWN_MAX_WARNINGS,
    })


@require_POST
@login_required
def save_code(request, attempt_id):
    attempt = get_object_or_404(Attempt, id=attempt_id, student=request.user)
    if attempt.status != Attempt.Status.IN_PROGRESS or timezone.now() >= attempt_deadline(attempt):
        return JsonResponse({"ok": False, "error": "This attempt is closed."}, status=409)
    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)
    question_id = data.get("question_id")
    source = data.get("source_code")
    snapshot = attempt.snapshots.filter(question_id_at_start=question_id, kind=Question.Kind.PROGRAMMING).first()
    if not snapshot:
        return JsonResponse({"ok": False, "error": "This is not a programming question."}, status=400)
    if not isinstance(source, str) or len(source) > MAX_SOURCE_LENGTH:
        return JsonResponse({"ok": False, "error": "Source must be text under 200 KB."}, status=400)
    language = data.get("language", snapshot.programming_language)
    if language != snapshot.programming_language:
        return JsonResponse({"ok": False, "error": "Language does not match the question."}, status=400)
    answer = get_answer(attempt, snapshot)
    with transaction.atomic():
        changed = save_version(answer, source, language)
        if changed:
            log_event(attempt, "CODE_SAVED", f"Autosaved question {snapshot.question_id_at_start}.")
    return JsonResponse({"ok": True, "saved_at": timezone.now().isoformat(), "version_created": changed})


@require_POST
@login_required
def run_code(request, attempt_id):
    """Record a browser-side test run.

    The submitted source is executed in the student's own browser (Pyodide);
    it is never executed on this server, so an untrusted answer cannot put the
    platform at risk. This endpoint only validates the attempt and writes the
    CODE_RUN audit event that lecturers see in the monitoring log.
    """
    attempt = get_object_or_404(Attempt, id=attempt_id, student=request.user)
    if attempt.status != Attempt.Status.IN_PROGRESS or timezone.now() >= attempt_deadline(attempt):
        return JsonResponse({"ok": False, "error": "This attempt is closed."}, status=409)
    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)
    question_id = data.get("question_id")
    snapshot = attempt.snapshots.filter(question_id_at_start=question_id, kind=Question.Kind.PROGRAMMING).first()
    if not snapshot or not snapshot.code_execution_allowed:
        return JsonResponse({"ok": False, "error": "Execution is not enabled for this question."}, status=403)
    language = data.get("language")
    if language != snapshot.programming_language:
        return JsonResponse({"ok": False, "error": "The selected language does not match this question."}, status=400)
    if language != BROWSER_RUNNER_LANGUAGE:
        return JsonResponse(
            {"ok": False, "error": "The in-browser runner supports Python only. Your source is still autosaved and submitted."},
            status=400,
        )
    recent_run = attempt.logs.filter(
        event_type="CODE_RUN", timestamp__gte=timezone.now() - timedelta(seconds=3),
    ).exists()
    if recent_run:
        return JsonResponse({"ok": False, "error": "Please wait a few seconds before running again."}, status=429)
    log_event(attempt, "CODE_RUN", f"Student test-ran {language} code in the browser for question {question_id}.")
    return JsonResponse({"ok": True, "runner": "browser", "executed_on_server": False})


@require_POST
@login_required
def submit_exam(request, attempt_id):
    attempt = get_object_or_404(Attempt, id=attempt_id, student=request.user)
    if attempt.status != Attempt.Status.IN_PROGRESS:
        return JsonResponse({"ok": True, "redirect": f"/student/result/{attempt.id}/"})
    if timezone.now() >= attempt_deadline(attempt):
        payload = {}
    else:
        try:
            payload = json.loads(request.body or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)
    answers = payload.get("answers", {})
    code_answers = payload.get("code_answers", {})
    if not isinstance(answers, dict) or not isinstance(code_answers, dict):
        return JsonResponse({"ok": False, "error": "Invalid answer payload."}, status=400)
    ensure_snapshots(attempt)
    try:
        with transaction.atomic():
            finalize_attempt(attempt, answers, code_answers)
    except ValueError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
    return JsonResponse({"ok": True, "redirect": f"/student/result/{attempt.id}/"})


@require_POST
@login_required
def heartbeat(request, attempt_id):
    attempt = get_object_or_404(Attempt, id=attempt_id, student=request.user)
    if attempt.status != Attempt.Status.IN_PROGRESS:
        return JsonResponse({"ok": False}, status=409)
    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        data = {}
    token = data.get("session_token", "")
    if not isinstance(token, str) or len(token) < 20 or len(token) > 64:
        return JsonResponse({"ok": False, "error": "Invalid session token."}, status=400)
    now = timezone.now()
    ActiveAttemptSession.objects.filter(
        attempt__student=request.user, last_seen__lt=now - SESSION_STALE_AFTER,
    ).delete()
    session_row, _ = ActiveAttemptSession.objects.update_or_create(
        attempt=attempt, session_token=token, defaults={"last_seen": now},
    )
    other_sessions = ActiveAttemptSession.objects.filter(
        attempt__student=request.user,
        attempt__status=Attempt.Status.IN_PROGRESS,
        last_seen__gte=now - SESSION_STALE_AFTER,
    ).exclude(pk=session_row.pk)
    multiple_sessions = other_sessions.exists()
    recent_duplicate_flag = attempt.logs.filter(
        event_type="MULTIPLE_LOGIN", timestamp__gte=now - timedelta(seconds=30),
    ).exists()
    if multiple_sessions and not recent_duplicate_flag:
        log_event(attempt, "MULTIPLE_LOGIN", "Another active browser session is using this exam attempt.", "warning")
    return JsonResponse({"ok": True, "multiple_sessions": multiple_sessions})


@require_POST
@login_required
def proctor_log(request):
    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)
    attempt = get_object_or_404(Attempt, id=data.get("attempt_id"), student=request.user)
    if attempt.status != Attempt.Status.IN_PROGRESS:
        return JsonResponse({"ok": False}, status=409)
    raw_type = str(data.get("event_type", "LEGACY_EVENT"))[:40]
    event_type = EVENT_ALIASES.get(raw_type.lower(), raw_type.upper())
    if event_type not in ALLOWED_EVENTS:
        event_type = "LEGACY_EVENT"
    log_event(attempt, event_type, data.get("detail", ""), data.get("severity", "warning"))
    return JsonResponse({"ok": True, "violation_count": attempt.logs.count()})


@require_GET
@login_required
def result(request, attempt_id):
    attempt = get_object_or_404(Attempt.objects.select_related("exam", "student"), id=attempt_id)
    if attempt.student_id != request.user.id and (not request.user.is_admin or attempt.exam.created_by_id != request.user.id):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    return render(request, "student/result.html", {"attempt": attempt, "exam": attempt.exam})


@require_GET
@login_required
@admin_required
def admin_dashboard(request):
    exams = Exam.objects.filter(created_by=request.user).prefetch_related("questions", "attempts__student").order_by("-created_at")
    attempts = Attempt.objects.filter(exam__created_by=request.user).exclude(status=Attempt.Status.IN_PROGRESS).select_related(
        "exam", "student",
    ).annotate(
        alert_count=Count("logs", filter=Q(logs__severity__in=["warning", "critical"]), distinct=True),
    ).order_by("-submitted_at")[:6]
    pending_programming = Answer.objects.filter(
        attempt__exam__created_by=request.user,
        attempt__status=Attempt.Status.SUBMITTED,
        source_code__gt="",
        marks_awarded__isnull=True,
    ).select_related("attempt__exam", "attempt__student").order_by("code_submitted_at")
    rates = [exam.pass_rate for exam in exams if exam.submitted_attempts]
    return render(request, "admin/dashboard.html", {
        "exams": exams,
        "published_count": sum(exam.is_published for exam in exams),
        "total_attempts": sum(exam.attempts.count() for exam in exams),
        "flagged_count": Attempt.objects.filter(
            exam__created_by=request.user, logs__severity__in=["warning", "critical"],
        ).distinct().count(),
        "avg_pass_rate": round(sum(rates) / len(rates)) if rates else 0,
        "recent_attempts": attempts,
        "pending_programming": pending_programming[:6],
        "pending_programming_count": pending_programming.count(),
    })


@require_http_methods(["GET", "POST"])
@login_required
@admin_required
def create_exam(request):
    if request.method == "POST":
        try:
            duration = int(request.POST.get("duration_minutes", "30"))
        except ValueError:
            duration = 0
        title = request.POST.get("title", "").strip()
        exam_password = request.POST.get("exam_password", "")
        if not title or not 5 <= duration <= 240 or len(exam_password) < 8:
            messages.error(request, "Enter a title, a duration between 5 and 240 minutes, and an exam password of at least 8 characters.")
            return redirect("create_exam")
        exam = Exam.objects.create(
            title=title, description=request.POST.get("description", "").strip(),
            duration_minutes=duration, created_by=request.user,
        )
        exam.set_access_password(exam_password)
        exam.save(update_fields=["access_password_hash"])
        messages.success(request, "Exam created as a draft.")
        return redirect("exam_detail", exam_id=exam.id)
    return render(request, "admin/create_exam.html")


@require_GET
@login_required
@admin_required
def exam_detail(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, created_by=request.user)
    attempts = exam.attempts.select_related("student").annotate(
        log_count=Count("logs", distinct=True),
        critical_log_count=Count("logs", filter=Q(logs__severity="critical"), distinct=True),
        warning_log_count=Count("logs", filter=Q(logs__severity="warning"), distinct=True),
    ).order_by("-started_at")
    return render(request, "admin/exam_detail.html", {
        "exam": exam, "attempts": attempts, "language_choices": Question.LANGUAGES,
    })


@require_POST
@login_required
@admin_required
def update_exam_settings(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, created_by=request.user)
    title = request.POST.get("title", "").strip()
    description = request.POST.get("description", "").strip()
    exam_password = request.POST.get("exam_password", "")
    try:
        duration = int(request.POST.get("duration_minutes", ""))
    except (TypeError, ValueError):
        duration = 0
    if not title or not 5 <= duration <= 240:
        messages.error(request, "Enter a title and a duration between 5 and 240 minutes.")
        return redirect("exam_detail", exam_id=exam.id)
    if exam_password and len(exam_password) < 8:
        messages.error(request, "Exam passwords must be at least 8 characters.")
        return redirect("exam_detail", exam_id=exam.id)
    exam.title = title
    exam.description = description
    exam.duration_minutes = duration
    update_fields = ["title", "description", "duration_minutes"]
    if exam_password:
        exam.set_access_password(exam_password)
        update_fields.append("access_password_hash")
    exam.save(update_fields=update_fields)
    messages.success(request, "Exam settings updated. Existing attempts keep their original duration.")
    return redirect("exam_detail", exam_id=exam.id)


@require_POST
@login_required
@admin_required
def add_question(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, created_by=request.user)
    text = request.POST.get("text", "").strip()
    kind = request.POST.get("kind", Question.Kind.MCQ)
    try:
        marks = int(request.POST.get("marks", "1"))
    except ValueError:
        marks = 0
    if not text or not 1 <= marks <= 100:
        messages.error(request, "Question text and marks from 1 to 100 are required.")
        return redirect("exam_detail", exam_id=exam.id)
    question = Question(exam=exam, text=text, kind=kind, marks=marks, position=exam.questions.count())
    if kind == Question.Kind.MCQ:
        options = [request.POST.get(f"option_{letter.lower()}", "").strip() for letter in "ABCD"]
        correct = request.POST.get("correct_option", "A").upper()
        if not all(options) or correct not in {"A", "B", "C", "D"}:
            messages.error(request, "MCQ questions need four options and one correct option.")
            return redirect("exam_detail", exam_id=exam.id)
        question.option_a, question.option_b, question.option_c, question.option_d = options
        question.correct_option = correct
    elif kind == Question.Kind.PROGRAMMING:
        language = request.POST.get("programming_language", "python")
        if language not in dict(Question.LANGUAGES):
            messages.error(request, "Choose a supported programming language.")
            return redirect("exam_detail", exam_id=exam.id)
        question.programming_language = language
        question.code_editor_required = request.POST.get("code_editor_required") == "on"
        question.code_execution_allowed = request.POST.get("code_execution_allowed") == "on"
    else:
        messages.error(request, "Choose a valid question type.")
        return redirect("exam_detail", exam_id=exam.id)
    question.save()
    messages.success(request, "Question added.")
    return redirect("exam_detail", exam_id=exam.id)


@require_POST
@login_required
@admin_required
def toggle_publish(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, created_by=request.user)
    if not exam.is_published and not exam.has_access_password:
        messages.error(request, "Set an exam password before publishing.")
        return redirect("exam_detail", exam_id=exam.id)
    exam.is_published = not exam.is_published
    exam.save(update_fields=["is_published"])
    return redirect("exam_detail", exam_id=exam.id)


@require_GET
@login_required
@admin_required
def programming_submissions(request):
    answers = Answer.objects.filter(
        attempt__exam__created_by=request.user,
        attempt__status=Attempt.Status.SUBMITTED,
        source_code__gt="",
    ).select_related("attempt__exam", "attempt__student", "question").order_by("marks_awarded", "-code_submitted_at")
    return render(request, "admin/programming_submissions.html", {"answers": answers})


@require_http_methods(["GET", "POST"])
@login_required
@admin_required
def review_submission(request, answer_id):
    answer = get_object_or_404(
        Answer.objects.select_related("attempt__exam", "attempt__student", "question"),
        id=answer_id, attempt__exam__created_by=request.user,
        attempt__status=Attempt.Status.SUBMITTED, source_code__gt="",
    )
    snapshot = get_object_or_404(QuestionSnapshot, attempt=answer.attempt, question_id_at_start=answer.question_id_at_start)
    if request.method == "POST":
        raw_marks = request.POST.get("marks_awarded", "")
        try:
            marks = int(raw_marks)
        except (TypeError, ValueError):
            marks = -1
        if not 0 <= marks <= snapshot.marks:
            messages.error(request, f"Marks must be between 0 and {snapshot.marks}.")
            return redirect("review_submission", answer_id=answer.id)
        answer.marks_awarded = marks
        answer.lecturer_feedback = request.POST.get("lecturer_feedback", "")[:10000]
        answer.save(update_fields=["marks_awarded", "lecturer_feedback"])
        answer.attempt.score = sum(item.marks_awarded or 0 for item in answer.attempt.answers.all())
        answer.attempt.save(update_fields=["score"])
        messages.success(request, "Programming answer marked.")
        return redirect("review_submission", answer_id=answer.id)
    return render(request, "admin/review_submission.html", {"answer": answer, "snapshot": snapshot})


@require_GET
@login_required
@admin_required
def download_java_submission(request, answer_id):
    answer = get_object_or_404(
        Answer.objects.select_related("attempt__exam", "attempt__student"),
        id=answer_id,
        attempt__exam__created_by=request.user,
        attempt__status=Attempt.Status.SUBMITTED,
        source_code__gt="",
        programming_language="java",
    )
    version = answer.final_version
    source = version.source_code if version else answer.source_code
    public_type = re.search(
        r"\bpublic\s+(?:(?:abstract|final|sealed|non-sealed|strictfp)\s+)*"
        r"(?:class|interface|enum|record)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
        source,
    )
    filename = f"{public_type.group(1)}.java" if public_type else f"proctorhall_submission_{answer.id}.java"
    response = HttpResponse(source, content_type="text/x-java-source; charset=utf-8")
    response["Content-Disposition"] = content_disposition_header(True, filename)
    return response


@require_GET
@login_required
@admin_required
def attempt_audit(request, attempt_id):
    attempt = get_object_or_404(Attempt.objects.select_related("exam", "student"), id=attempt_id, exam__created_by=request.user)
    logs = attempt.logs.order_by("timestamp", "id")
    counts = {
        "total": logs.count(),
        "warning": logs.filter(severity="warning").count(),
        "critical": logs.filter(severity="critical").count(),
        "by_type": logs.values("event_type").annotate(total=Count("id")).order_by("-total"),
    }
    return render(request, "admin/attempt_audit.html", {
        "attempt": attempt, "logs": logs, "counts": counts,
    })


RESULTS_CSV_HEADERS = ["student_email", "question_number", "marks", "feedback"]


def exam_question_numbering(exam):
    """Map each question to its 1-based number as shown in the admin UI (Q1, Q2...)."""
    questions = list(exam.questions.order_by("position", "id"))
    return questions, {question.id: index + 1 for index, question in enumerate(questions)}


def row_cell(row, column, name):
    index = column.get(name)
    return row[index].strip() if index is not None and index < len(row) else ""


@require_GET
@login_required
@admin_required
def export_results(request, exam_id):
    """Download every submitted answer for an exam as a CSV the admin can edit and re-upload."""
    exam = get_object_or_404(Exam, id=exam_id, created_by=request.user)
    questions, numbers = exam_question_numbering(exam)
    max_marks = {question.id: question.marks for question in questions}
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="exam-{exam.id}-results.csv"'
    writer = csv.writer(response)
    writer.writerow(["student_email", "student_name", "question_number", "marks", "max_marks", "feedback", "status"])
    answers = Answer.objects.filter(attempt__exam=exam).select_related("attempt__student").order_by(
        "attempt__student__email", "question_id_at_start",
    )
    for answer in answers:
        writer.writerow([
            answer.attempt.student.email,
            answer.attempt.student.name,
            numbers.get(answer.question_id_at_start, ""),
            "" if answer.marks_awarded is None else answer.marks_awarded,
            max_marks.get(answer.question_id_at_start, ""),
            answer.lecturer_feedback,
            answer.attempt.get_status_display(),
        ])
    return response


@require_http_methods(["GET", "POST"])
@login_required
@admin_required
def upload_results(request, exam_id):
    """Bulk update marks/feedback for an exam from a CSV file.

    Expected columns: student_email, question_number, marks, feedback (feedback optional).
    """
    exam = get_object_or_404(Exam, id=exam_id, created_by=request.user)
    if request.method == "GET":
        return render(request, "admin/upload_results.html", {"exam": exam})

    upload = request.FILES.get("results_file")
    if upload is None:
        messages.error(request, "Choose a CSV results file to upload.")
        return redirect("upload_results", exam_id=exam.id)
    if not upload.name.lower().endswith(".csv"):
        messages.error(request, "The results file must be saved as a .csv file.")
        return redirect("upload_results", exam_id=exam.id)
    try:
        content = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        messages.error(request, "The CSV file could not be decoded. Save it as UTF-8.")
        return redirect("upload_results", exam_id=exam.id)

    reader = csv.reader(io.StringIO(content))
    try:
        header = [cell.strip().lower() for cell in next(reader)]
    except StopIteration:
        header = []
    if not all(name in header for name in RESULTS_CSV_HEADERS[:3]):
        messages.error(
            request,
            "The CSV must start with a header row containing: student_email, question_number, marks (feedback is optional).",
        )
        return redirect("upload_results", exam_id=exam.id)
    column = {name: header.index(name) for name in RESULTS_CSV_HEADERS if name in header}

    questions, numbers = exam_question_numbering(exam)
    by_number = {numbers[question.id]: question for question in questions}
    attempts_by_email = {
        attempt.student.email.lower(): attempt
        for attempt in exam.attempts.select_related("student")
    }

    updated = 0
    errors = []
    touched_attempts = set()
    for row_number, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        email = row_cell(row, column, "student_email").lower()
        raw_number = row_cell(row, column, "question_number")
        raw_marks = row_cell(row, column, "marks")
        feedback = row_cell(row, column, "feedback")
        try:
            number = int(raw_number)
        except ValueError:
            number = -1
        question = by_number.get(number)
        attempt = attempts_by_email.get(email)
        if question is None:
            errors.append(f"Row {row_number}: question number '{raw_number}' does not exist in this exam.")
            continue
        if attempt is None:
            errors.append(f"Row {row_number}: no attempt for '{email}' in this exam.")
            continue
        if attempt.status == Attempt.Status.IN_PROGRESS:
            errors.append(f"Row {row_number}: {attempt.student.name} has not submitted this exam yet.")
            continue
        snapshot = attempt.snapshots.filter(question_id_at_start=question.id).first()
        if snapshot is None:
            errors.append(f"Row {row_number}: question {number} was not part of {attempt.student.name}'s attempt.")
            continue
        try:
            marks = int(raw_marks)
        except ValueError:
            marks = -1
        if not 0 <= marks <= question.marks:
            errors.append(f"Row {row_number}: marks must be a whole number between 0 and {question.marks}.")
            continue
        answer = get_answer(attempt, snapshot)
        answer.marks_awarded = marks
        if feedback:
            answer.lecturer_feedback = feedback[:10000]
        answer.save(update_fields=["marks_awarded", "lecturer_feedback"])
        touched_attempts.add(attempt.id)
        updated += 1

    if touched_attempts:
        for attempt in Attempt.objects.filter(id__in=touched_attempts).prefetch_related("answers"):
            attempt.score = sum(answer.marks_awarded or 0 for answer in attempt.answers.all())
            attempt.save(update_fields=["score"])
    if updated:
        messages.success(request, f"Results updated: {updated} answer(s) marked from the CSV.")
    if errors:
        preview = errors[:8]
        if len(errors) > 8:
            preview.append(f"...and {len(errors) - 8} more row(s).")
        messages.error(request, "Some rows were skipped — " + " | ".join(preview))
    if not updated and not errors:
        messages.info(request, "The CSV contained no data rows, so nothing changed.")
    return redirect("upload_results", exam_id=exam.id)
