from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from exams.models import Exam, Question, User


class Command(BaseCommand):
    help = "Create a sample exam with mixed MCQ and programming questions that is ready for an admin to publish."

    def add_arguments(self, parser):
        parser.add_argument("--email", default="lecturer@example.com", help="Administrator email for the test exam owner.")
        parser.add_argument("--name", default="Lecturer", help="Administrator display name.")
        parser.add_argument("--password", help="Password for a newly created admin account. Omit to be prompted securely.")
        parser.add_argument("--exam-password", help="Exam access password. Omit to be prompted securely.")
        parser.add_argument("--title", default="Sample Practice Exam", help="Title for the seeded exam.")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        name = options["name"].strip()
        title = options["title"].strip() or "Sample Practice Exam"

        if not email:
            raise CommandError("An email address is required.")
        if not name:
            raise CommandError("A name is required.")

        password = options["password"]
        from getpass import getpass
        if password is None:
            password = getpass("Password for admin account (minimum 8 characters): ")
            confirmation = getpass("Confirm password: ")
            if password != confirmation:
                raise CommandError("Passwords do not match.")
        exam_password = options["exam_password"]
        if exam_password is None:
            exam_password = getpass("Exam access password (minimum 8 characters): ")
            confirmation = getpass("Confirm exam access password: ")
            if exam_password != confirmation:
                raise CommandError("Exam passwords do not match.")
        if len(exam_password) < 8:
            raise CommandError("Exam access password must be at least 8 characters.")

        admin, created = User.objects.get_or_create(
            email=email,
            defaults={
                "name": name,
                "role": "admin",
                "is_staff": True,
                "is_superuser": False,
            },
        )
        if created:
            if len(password) < 8:
                raise CommandError("Password must be at least 8 characters.")
            admin.set_password(password)
            admin.save(update_fields=["password", "name", "role", "is_staff", "is_superuser"])
            self.stdout.write(self.style.SUCCESS(f"Created administrator account for {admin.email}."))
        else:
            admin.name = name
            admin.role = "admin"
            admin.is_staff = True
            admin.save(update_fields=["name", "role", "is_staff"])

        with transaction.atomic():
            exam, exam_created = Exam.objects.get_or_create(
                title=title,
                defaults={
                    "description": "Sample exam for practice and QA testing. Contains both multiple-choice and programming questions.",
                    "duration_minutes": 45,
                    "created_by": admin,
                    "is_published": False,
                },
            )

            if not exam_created:
                exam.created_by = admin
                exam.description = exam.description or "Sample exam for practice and QA testing. Contains both multiple-choice and programming questions."
                exam.duration_minutes = exam.duration_minutes or 45
                exam.is_published = False
            exam.set_access_password(exam_password)
            exam.save(update_fields=["created_by", "description", "duration_minutes", "is_published", "access_password_hash"])

            questions = list(exam.questions.order_by("position", "id"))
            if not questions:
                Question.objects.create(
                    exam=exam,
                    text="Which HTML tag is used to define a paragraph?",
                    kind=Question.Kind.MCQ,
                    option_a="<title>",
                    option_b="<p>",
                    option_c="<div>",
                    option_d="<span>",
                    correct_option="B",
                    marks=2,
                    position=1,
                )
                Question.objects.create(
                    exam=exam,
                    text="Which of the following is a valid Python data type for storing key-value pairs?",
                    kind=Question.Kind.MCQ,
                    option_a="list",
                    option_b="tuple",
                    option_c="dict",
                    option_d="set",
                    correct_option="C",
                    marks=3,
                    position=2,
                )
                Question.objects.create(
                    exam=exam,
                    text="Write a Python function that returns the sum of two integers.",
                    kind=Question.Kind.PROGRAMMING,
                    programming_language="python",
                    marks=10,
                    code_editor_required=True,
                    code_execution_allowed=True,
                    position=3,
                )
                Question.objects.create(
                    exam=exam,
                    text="Write a JavaScript function that doubles a number and returns it.",
                    kind=Question.Kind.PROGRAMMING,
                    programming_language="javascript",
                    marks=10,
                    code_editor_required=True,
                    code_execution_allowed=False,
                    position=4,
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Ready: exam '{exam.title}' (ID {exam.id}) has mixed MCQ and programming questions and is available for the admin to publish."
            )
        )
