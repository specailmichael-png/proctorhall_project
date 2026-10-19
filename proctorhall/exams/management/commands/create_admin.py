from getpass import getpass
from django.core.management.base import BaseCommand, CommandError
from exams.models import User


class Command(BaseCommand):
    help = "Create a lecturer/admin account."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--password", help="Omit to enter the password securely at the prompt")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        password = options["password"]
        if password is None:
            password = getpass("Password (minimum 8 characters): ")
            confirmation = getpass("Confirm password: ")
            if password != confirmation:
                raise CommandError("Passwords do not match.")
        if len(password) < 8:
            raise CommandError("Password must be at least 8 characters.")
        if User.objects.filter(email=email).exists():
            raise CommandError("An account with that email already exists.")
        user = User.objects.create_user(email=email, name=options["name"].strip(), password=password, role="admin", is_staff=True)
        self.stdout.write(self.style.SUCCESS(f"Created administrator account for {user.email}."))
