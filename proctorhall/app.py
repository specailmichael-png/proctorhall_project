"""Compatibility entry point for existing instructions that use ``python app.py``."""
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "proctorhall_site.settings")

from django.core.management import execute_from_command_line


if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("runserver")
    execute_from_command_line(sys.argv)
