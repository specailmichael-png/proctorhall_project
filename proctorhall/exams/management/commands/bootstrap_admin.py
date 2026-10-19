import os

from django.core.management.base import BaseCommand, CommandError

from exams.models import User


class Command(BaseCommand):
    help = "Create the initial admin account from environment variables if it does not exist."

    def handle(self, *args, **options):
        email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        if not email:
            self.stdout.write("Initial admin not configured; skipping.")
            return

        existing_user = User.objects.filter(email=email).first()
        if existing_user:
            if not existing_user.is_admin:
                raise CommandError(f"Account {email} exists but is not an administrator.")
            self.stdout.write(f"Initial admin {email} already exists; skipping.")
            return

        name = os.environ.get("BOOTSTRAP_ADMIN_NAME", "").strip()
        password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
        if not name or not password:
            raise CommandError(
                "Set BOOTSTRAP_ADMIN_NAME and BOOTSTRAP_ADMIN_PASSWORD to create the initial admin."
            )
        if len(password) < 8:
            raise CommandError("BOOTSTRAP_ADMIN_PASSWORD must be at least 8 characters.")

        User.objects.create_user(
            email=email,
            name=name,
            password=password,
            role="admin",
            is_staff=True,
        )
        self.stdout.write(self.style.SUCCESS(f"Created initial admin account for {email}."))
