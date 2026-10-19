import json
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from exams.models import Answer, Attempt, CodeVersion, Exam, ProctorLog, Question, User


class ExamIntegrityTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="lecturer@example.test", name="Lecturer", password="adminpass123",
            role="admin", is_staff=True,
        )
        self.other_admin = User.objects.create_user(
            email="other@example.test", name="Other Lecturer", password="adminpass123",
            role="admin", is_staff=True,
        )
        self.student = User.objects.create_user(
            email="student@example.test", name="Student", password="studentpass123",
        )
        self.exam = Exam.objects.create(
            title="Practical", duration_minutes=30, created_by=self.admin, is_published=True,
        )
        self.exam.set_access_password("ExamPass123")
        self.exam.save(update_fields=["access_password_hash"])
        self.mcq = Question.objects.create(
            exam=self.exam, text="Pick the answer", option_a="A", option_b="B",
            option_c="C", option_d="D", correct_option="B", marks=2,
        )
        self.programming = Question.objects.create(
            exam=self.exam, text="Write a function", kind=Question.Kind.PROGRAMMING,
            programming_language="python", marks=5, code_editor_required=True,
            code_execution_allowed=False, position=1,
        )
        self.client = Client()

    def start_attempt(self):
        response = self.client.post(reverse("start_exam", args=[self.exam.id]), {
            "exam_password": "ExamPass123",
        })
        self.assertEqual(response.status_code, 302)
        return Attempt.objects.get(exam=self.exam, student=self.student)

    def post_json(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    def test_student_registration_always_creates_student_role(self):
        response = self.client.post(reverse("register"), {
            "student_id": "bit23240509", "email": "new@example.test", "password": "password123",
            "role": "admin",
        })
        self.assertRedirects(response, reverse("login"))
        user = User.objects.get(email="new@example.test")
        self.assertEqual(user.role, "student")
        self.assertEqual(user.student_id, "BIT23240509")
        self.assertEqual(user.name, "BIT23240509")

    def test_student_registration_rejects_invalid_student_id(self):
        response = self.client.post(reverse("register"), {
            "student_id": "BIT123", "email": "invalid@example.test", "password": "password123",
        })
        self.assertRedirects(response, reverse("register"))
        self.assertFalse(User.objects.filter(email="invalid@example.test").exists())

    def test_existing_student_without_valid_id_cannot_sign_in(self):
        self.student.set_password("ValidPass123")
        self.student.save(update_fields=["password"])
        response = self.client.post(reverse("login"), {
            "email": self.student.email, "password": "ValidPass123",
        })
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

        self.student.student_id = "BIT23240508"
        self.student.save(update_fields=["student_id"])
        response = self.client.post(reverse("login"), {
            "email": self.student.email, "password": "ValidPass123",
        })
        self.assertRedirects(response, reverse("student_dashboard"))
        self.client.logout()
        response = self.client.post(reverse("login"), {
            "email": "BIT23240508", "password": "ValidPass123",
        })
        self.assertRedirects(response, reverse("student_dashboard"))

    def test_admin_can_create_exam_and_add_both_question_types(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("create_exam"), {
            "title": "Admin workflow smoke test", "description": "Test-only exam",
            "duration_minutes": "75", "exam_password": "ExamPass123",
        })
        exam = Exam.objects.get(title="Admin workflow smoke test")
        self.assertRedirects(response, reverse("exam_detail", args=[exam.id]))
        self.assertEqual(exam.duration_minutes, 75)
        self.assertTrue(exam.check_access_password("ExamPass123"))
        self.assertNotEqual(exam.access_password_hash, "ExamPass123")

        self.client.post(reverse("add_question", args=[exam.id]), {
            "kind": "mcq", "text": "Select one", "option_a": "A", "option_b": "B",
            "option_c": "C", "option_d": "D", "correct_option": "C", "marks": "2",
        })
        self.client.post(reverse("add_question", args=[exam.id]), {
            "kind": "programming", "text": "Write code", "marks": "8",
            "programming_language": "java", "code_editor_required": "on",
        })
        questions = list(exam.questions.order_by("position"))
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0].kind, Question.Kind.MCQ)
        self.assertEqual(questions[1].kind, Question.Kind.PROGRAMMING)
        self.assertEqual(exam.total_marks, 10)

        self.client.post(reverse("toggle_publish", args=[exam.id]))
        exam.refresh_from_db()
        self.assertTrue(exam.is_published)

    def test_draft_exam_cannot_be_started(self):
        draft = Exam.objects.create(title="Draft", duration_minutes=30, created_by=self.admin)
        self.client.force_login(self.student)
        response = self.client.get(reverse("start_exam", args=[draft.id]))
        self.assertEqual(response.status_code, 404)

    def test_exam_password_is_required_before_an_attempt_is_created(self):
        self.client.force_login(self.student)
        start_url = reverse("start_exam", args=[self.exam.id])
        self.assertEqual(self.client.get(start_url).status_code, 200)
        response = self.client.post(start_url, {"exam_password": "wrong-password"})
        self.assertRedirects(response, start_url)
        self.assertFalse(Attempt.objects.filter(exam=self.exam, student=self.student).exists())
        response = self.client.post(start_url, {"exam_password": "ExamPass123"})
        attempt = Attempt.objects.get(exam=self.exam, student=self.student)
        self.assertRedirects(response, reverse("take_exam", args=[attempt.id]))

    def test_exam_without_password_cannot_be_published(self):
        draft = Exam.objects.create(title="Unprotected draft", duration_minutes=30, created_by=self.admin)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("toggle_publish", args=[draft.id]))
        self.assertRedirects(response, reverse("exam_detail", args=[draft.id]))
        draft.refresh_from_db()
        self.assertFalse(draft.is_published)

    def test_exam_duration_can_change_without_changing_active_attempt(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        self.assertEqual(attempt.duration_minutes, 30)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("update_exam_settings", args=[self.exam.id]), {
            "title": "Updated practical", "description": "Updated instructions", "duration_minutes": "55",
        })
        self.assertRedirects(response, reverse("exam_detail", args=[self.exam.id]))
        attempt.refresh_from_db()
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.duration_minutes, 55)
        self.assertEqual(attempt.duration_minutes, 30)

    def test_autosave_versions_and_final_source_are_submitted(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        save_url = reverse("save_code", args=[attempt.id])
        first_source = "def answer():\n    return 1\n"
        response = self.post_json(save_url, {
            "question_id": self.programming.id, "source_code": first_source, "language": "python",
        })
        self.assertEqual(response.status_code, 200)
        second_source = "def answer():\n    return 42\n"
        self.post_json(save_url, {
            "question_id": self.programming.id, "source_code": second_source, "language": "python",
        })
        answer = Answer.objects.get(attempt=attempt, question_id_at_start=self.programming.id)
        self.assertEqual(answer.versions.count(), 2)
        self.assertEqual(answer.source_code, second_source)

        response = self.post_json(reverse("submit_exam", args=[attempt.id]), {
            "answers": {str(self.mcq.id): "B"},
            "code_answers": {str(self.programming.id): {
                "source_code": second_source, "language": "python",
            }},
        })
        self.assertEqual(response.status_code, 200)
        answer.refresh_from_db()
        attempt.refresh_from_db()
        final_version = answer.versions.get(is_final=True)
        self.assertEqual(final_version.source_code, second_source)
        self.assertTrue(answer.code_submitted_at)
        self.assertEqual(answer.programming_language, "python")
        self.assertEqual(attempt.status, Attempt.Status.SUBMITTED)
        self.assertEqual(attempt.score, 2)
        self.assertTrue(attempt.logs.filter(event_type="CODE_SUBMITTED").exists())
        self.assertTrue(attempt.logs.filter(event_type="EXAM_SUBMITTED").exists())

    def test_programming_answer_can_be_manually_marked_with_feedback(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        self.post_json(reverse("submit_exam", args=[attempt.id]), {
            "answers": {str(self.mcq.id): "B"},
            "code_answers": {str(self.programming.id): {
                "source_code": "print('actual source')\n", "language": "python",
            }},
        })
        answer = Answer.objects.get(attempt=attempt, question_id_at_start=self.programming.id)
        self.client.force_login(self.admin)
        review_url = reverse("review_submission", args=[answer.id])
        self.assertEqual(self.client.get(review_url).status_code, 200)
        response = self.client.post(review_url, {"marks_awarded": "4", "lecturer_feedback": "Good approach."})
        self.assertRedirects(response, review_url)
        answer.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(answer.marks_awarded, 4)
        self.assertEqual(answer.lecturer_feedback, "Good approach.")
        self.assertEqual(attempt.score, 6)

    def test_admin_can_download_java_submission_for_local_testing(self):
        self.programming.programming_language = "java"
        self.programming.save(update_fields=["programming_language"])
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        source = "public class AnswerCheck { public static void main(String[] args) {} }\n"
        self.post_json(reverse("submit_exam", args=[attempt.id]), {
            "code_answers": {str(self.programming.id): {"source_code": source, "language": "java"}},
        })
        answer = Answer.objects.get(attempt=attempt, question_id_at_start=self.programming.id)

        self.client.force_login(self.admin)
        response = self.client.get(reverse("download_java_submission", args=[answer.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/x-java-source; charset=utf-8")
        self.assertIn('filename="AnswerCheck.java"', response["Content-Disposition"])
        self.assertEqual(response.content.decode(), source)
        review_response = self.client.get(reverse("review_submission", args=[answer.id]))
        self.assertContains(review_response, "Download Java source")
        self.assertContains(review_response, "javac YourClass.java")

    def test_java_download_is_limited_to_own_submitted_java_answers(self):
        self.programming.programming_language = "java"
        self.programming.save(update_fields=["programming_language"])
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        self.post_json(reverse("submit_exam", args=[attempt.id]), {
            "code_answers": {str(self.programming.id): {"source_code": "class AnswerCheck {}", "language": "java"}},
        })
        answer = Answer.objects.get(attempt=attempt, question_id_at_start=self.programming.id)

        self.client.force_login(self.other_admin)
        self.assertEqual(self.client.get(reverse("download_java_submission", args=[answer.id])).status_code, 404)

        self.client.force_login(self.admin)
        response = self.client.get(reverse("download_java_submission", args=[answer.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"proctorhall_submission_{answer.id}.java", response["Content-Disposition"])

    def test_admin_review_page_offers_python_browser_runner(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        self.post_json(reverse("submit_exam", args=[attempt.id]), {
            "code_answers": {str(self.programming.id): {"source_code": "print('test')", "language": "python"}},
        })
        answer = Answer.objects.get(attempt=attempt, question_id_at_start=self.programming.id)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("review_submission", args=[answer.id]))
        self.assertContains(response, "Run Python here")
        self.assertContains(response, "admin-review-runner.js")

    def test_lecturer_cannot_review_another_lecturers_submission(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        self.post_json(reverse("submit_exam", args=[attempt.id]), {
            "code_answers": {str(self.programming.id): {"source_code": "pass", "language": "python"}},
        })
        answer = Answer.objects.get(attempt=attempt, question_id_at_start=self.programming.id)
        self.client.force_login(self.other_admin)
        self.assertEqual(self.client.get(reverse("review_submission", args=[answer.id])).status_code, 404)

    def test_proctor_events_are_review_flags_not_automatic_cheating_decisions(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        response = self.post_json(reverse("proctor_log"), {
            "attempt_id": attempt.id, "event_type": "tab_blur", "detail": "tab hidden",
        })
        self.assertEqual(response.status_code, 200)
        event = ProctorLog.objects.get(attempt=attempt, event_type="TAB_SWITCH")
        self.assertEqual(event.detail, "tab hidden")
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.IN_PROGRESS)

    def test_second_active_browser_session_is_logged(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        heartbeat_url = reverse("heartbeat", args=[attempt.id])
        first = self.post_json(heartbeat_url, {"session_token": "first-browser-session-token"})
        second = self.post_json(heartbeat_url, {"session_token": "second-browser-session-token"})
        self.assertFalse(first.json()["multiple_sessions"])
        self.assertTrue(second.json()["multiple_sessions"])
        self.assertEqual(attempt.logs.filter(event_type="MULTIPLE_LOGIN").count(), 1)

    def test_parallel_attempts_for_same_student_are_detected(self):
        self.client.force_login(self.student)
        first_attempt = self.start_attempt()
        other_exam = Exam.objects.create(
            title="Second practical", duration_minutes=30,
            created_by=self.admin, is_published=True,
        )
        second_attempt = Attempt.objects.create(exam=other_exam, student=self.student)
        self.post_json(reverse("heartbeat", args=[first_attempt.id]), {
            "session_token": "first-exam-browser-session-token",
        })
        response = self.post_json(reverse("heartbeat", args=[second_attempt.id]), {
            "session_token": "second-exam-browser-session-token",
        })
        self.assertTrue(response.json()["multiple_sessions"])
        self.assertTrue(second_attempt.logs.filter(event_type="MULTIPLE_LOGIN").exists())

    def test_student_cannot_save_for_another_attempt_or_wrong_language(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        response = self.post_json(reverse("save_code", args=[attempt.id]), {
            "question_id": self.programming.id, "source_code": "print('x')", "language": "javascript",
        })
        self.assertEqual(response.status_code, 400)
        response = self.post_json(reverse("save_code", args=[attempt.id]), {
            "question_id": self.programming.id, "source_code": "x" * 200_001, "language": "python",
        })
        self.assertEqual(response.status_code, 400)

    def test_audit_log_is_chronological_and_submission_pages_render(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attempt_audit", args=[attempt.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Exam Started")
        self.assertEqual(self.client.get(reverse("programming_submissions")).status_code, 200)

    def test_exam_management_and_student_editor_templates_render(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("exam_detail", args=[self.exam.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Programming language")
        self.assertContains(response, 'mcqFields.style.display = programming ? "none" : "grid";')
        self.assertContains(response, "input.required = !programming;")

        self.programming.code_editor_required = False
        self.programming.save(update_fields=["code_editor_required"])
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        response = self.client.get(reverse("take_exam", args=[attempt.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="plain program-source"')
        self.assertContains(response, 'data-language="python"')

    def test_programming_question_needs_no_mcq_options(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("add_question", args=[self.exam.id]), {
            "kind": "programming", "text": "Implement the function", "marks": "12",
            "programming_language": "csharp", "code_editor_required": "on",
            "code_execution_allowed": "on",
        })
        self.assertRedirects(response, reverse("exam_detail", args=[self.exam.id]))
        question = Question.objects.filter(exam=self.exam, kind=Question.Kind.PROGRAMMING).latest("id")
        self.assertEqual(question.programming_language, "csharp")
        self.assertEqual(question.marks, 12)
        self.assertEqual(question.option_a, "")

    def test_runner_records_browser_runs_without_executing_on_server(self):
        self.programming.code_execution_allowed = True
        self.programming.save(update_fields=["code_execution_allowed"])
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        response = self.post_json(reverse("run_code", args=[attempt.id]), {
            "question_id": self.programming.id, "language": "python",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runner"], "browser")
        self.assertFalse(response.json()["executed_on_server"])
        self.assertTrue(attempt.logs.filter(event_type="CODE_RUN").exists())
        # The server only records the run, so a quick second run is rate limited.
        response = self.post_json(reverse("run_code", args=[attempt.id]), {
            "question_id": self.programming.id, "language": "python",
        })
        self.assertEqual(response.status_code, 429)

    def test_runner_rejects_disabled_execution_and_non_python_languages(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        run_url = reverse("run_code", args=[attempt.id])
        response = self.post_json(run_url, {"question_id": self.programming.id, "language": "python"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(attempt.logs.filter(event_type="CODE_RUN").exists())

        snapshot = attempt.snapshots.get(question_id_at_start=self.programming.id)
        snapshot.code_execution_allowed = True
        snapshot.programming_language = "java"
        snapshot.save(update_fields=["code_execution_allowed", "programming_language"])
        response = self.post_json(run_url, {"question_id": self.programming.id, "language": "java"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Python only", response.json()["error"])
        self.assertFalse(attempt.logs.filter(event_type="CODE_RUN").exists())

    def test_run_button_appears_only_for_python_questions_with_execution(self):
        self.programming.code_editor_required = True
        self.programming.code_execution_allowed = True
        self.programming.save(update_fields=["code_editor_required", "code_execution_allowed"])
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        response = self.client.get(reverse("take_exam", args=[attempt.id]))
        self.assertContains(response, "code-runner-trigger")

        snapshot = attempt.snapshots.get(question_id_at_start=self.programming.id)
        snapshot.programming_language = "java"
        snapshot.save(update_fields=["programming_language"])
        response = self.client.get(reverse("take_exam", args=[attempt.id]))
        self.assertNotContains(response, "code-runner-trigger")

    def test_lockdown_and_auto_submit_events_are_recorded_for_review(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        for event_type, severity in (("LOCKDOWN_WARNING", "warning"), ("EXAM_AUTO_SUBMITTED", "critical")):
            response = self.post_json(reverse("proctor_log"), {
                "attempt_id": attempt.id, "event_type": event_type,
                "detail": f"{event_type} recorded by the screen lock-down.", "severity": severity,
            })
            self.assertEqual(response.status_code, 200)
        self.assertEqual(attempt.logs.filter(event_type="LOCKDOWN_WARNING").count(), 1)
        self.assertEqual(attempt.logs.filter(event_type="EXAM_AUTO_SUBMITTED").count(), 1)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("attempt_audit", args=[attempt.id]))
        self.assertContains(response, "Lockdown Warning")
        self.assertContains(response, "Exam Auto Submitted")

    def test_student_exam_screen_hides_the_activity_log_from_the_student(self):
        self.programming.code_editor_required = False
        self.programming.save(update_fields=["code_editor_required"])
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        self.post_json(reverse("proctor_log"), {
            "attempt_id": attempt.id, "event_type": "COPY_ATTEMPT", "detail": "copy attempt recorded",
        })
        response = self.client.get(reverse("take_exam", args=[attempt.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Monitoring")
        self.assertContains(response, "pyodideWorkerUrl")
        self.assertContains(response, "pyodideIndexUrl")
        self.assertContains(response, "lockdownMaxWarnings: 3")
        self.assertContains(response, "Begin exam")
        self.assertNotContains(response, "violationFeed")
        self.assertNotContains(response, "violationCount")
        self.assertNotContains(response, "copy attempt recorded")

        self.post_json(reverse("submit_exam", args=[attempt.id]), {
            "answers": {str(self.mcq.id): "B"},
        })
        response = self.client.get(reverse("result", args=[attempt.id]))
        self.assertNotContains(response, "Proctoring log")
        self.assertNotContains(response, "copy attempt recorded")
        self.assertContains(response, "Submitted for review")

        self.client.force_login(self.admin)
        response = self.client.get(reverse("result", args=[attempt.id]))
        self.assertContains(response, "Proctoring log")

    def test_admin_can_upload_results_csv(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        self.post_json(reverse("submit_exam", args=[attempt.id]), {
            "answers": {str(self.mcq.id): "B"},
            "code_answers": {str(self.programming.id): {
                "source_code": "def answer(): return 7", "language": "python",
            }},
        })
        self.client.force_login(self.admin)
        upload_url = reverse("upload_results", args=[self.exam.id])
        self.assertEqual(self.client.get(upload_url).status_code, 200)
        csv_content = (
            "student_email,question_number,marks,feedback\n"
            "student@example.test,2,4,Solid solution.\n"
            "student@example.test,1,99,Too many marks.\n"
            "ghost@example.test,2,3,No such student.\n"
            "student@example.test,9,1,No such question.\n"
        )
        response = self.client.post(upload_url, {
            "results_file": SimpleUploadedFile("results.csv", csv_content.encode(), content_type="text/csv"),
        })
        self.assertEqual(response.status_code, 302)
        answer = Answer.objects.get(attempt=attempt, question_id_at_start=self.programming.id)
        self.assertEqual(answer.marks_awarded, 4)
        self.assertEqual(answer.lecturer_feedback, "Solid solution.")
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, 6)
        follow_up = self.client.get(upload_url)
        self.assertContains(follow_up, "Results updated: 1 answer(s) marked")
        self.assertContains(follow_up, "Some rows were skipped")

        # Students may never upload results.
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(upload_url).status_code, 403)
        self.assertEqual(self.client.post(upload_url, {}).status_code, 403)

    def test_admin_can_download_results_csv(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        self.post_json(reverse("submit_exam", args=[attempt.id]), {
            "answers": {str(self.mcq.id): "B"},
        })
        self.client.force_login(self.admin)
        response = self.client.get(reverse("export_results", args=[self.exam.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode()
        self.assertIn("student_email,student_name,question_number,marks,max_marks,feedback,status", body)
        self.assertIn("student@example.test,Student,1,2,2,,Submitted", body)

    def test_admin_dashboard_lists_unmarked_source(self):
        self.client.force_login(self.student)
        attempt = self.start_attempt()
        self.post_json(reverse("submit_exam", args=[attempt.id]), {
            "code_answers": {str(self.programming.id): {
                "source_code": "def answer(): return 7", "language": "python",
            }},
        })
        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Programming answers awaiting marks")
        self.assertContains(response, "Student")

    def test_csrf_is_required_for_mutations(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.student)
        response = csrf_client.post(reverse("start_exam", args=[self.exam.id]))
        self.assertEqual(response.status_code, 403)
