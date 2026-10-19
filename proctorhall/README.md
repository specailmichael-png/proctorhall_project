# ProctorHall

ProctorHall is a Django-based online examination platform with student/admin roles, MCQ and programming questions, browser-based proctoring, source-code autosave/version history, final code submission, and lecturer review/marking. Django stores student source code and does not execute it.

## Stack

- Django 6.1
- PostgreSQL or MySQL in a deployed setup; SQLite is the local/test default
- Django authentication, sessions, CSRF middleware, ORM, and migrations
- HTML templates, existing CSS, and browser JavaScript
- face-api.js and TensorFlow.js models for client-side camera analysis

## Local Setup (Windows)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000/`, select **Sign in**, and use an administrator/lecturer account. Create one with `python manage.py create_admin --email lecturer@example.com --name "Lecturer Name"`; Django prompts privately for the password. To change the password for an imported administrator, run `python manage.py changepassword their-email@example.com`. The `python app.py` compatibility entry point also starts Django's development server. Public registration accepts University of Lusaka student IDs in the format `BIT` followed by 8 digits (for example, `BIT23240508`). The ID is the student's account name and can be used instead of email to sign in; older student accounts without a valid ID must be updated by an administrator in `/django-admin/` before they can sign in. Public registration always creates a student.

To seed a ready-to-publish smoke-test exam with mixed MCQ and programming questions, run:

```powershell
python manage.py create_test_exam --email lecturer@example.com --name "Lecturer Name"
```

Omit `--password` to enter the administrator password securely. The command also prompts for the exam access password. This creates or refreshes a draft exam titled `Sample Practice Exam` with both multiple-choice and programming questions, leaving it unpublished so the admin can review and publish it from the admin interface.

The student ID format check validates the shape of an ID only; it does not verify enrollment with the University of Lusaka. To confirm actual student status, use an institutional identity provider or a verified student roster before enabling self-registration.

## Create and Manage Exams

After signing in as an administrator, open **New exam**, set the title, initial duration, and an exam access password of at least 8 characters, then add questions from the exam management page. Exam passwords are stored as hashes; share them with eligible students separately. Students must enter the correct exam password before their attempt is created. The question type selector switches between MCQ and programming settings: programming questions do not require MCQ options. Set the language, marks, editor requirement, and optional execution permission there. The **Exam settings** section remains available after creation; later duration changes affect new attempts only, while active students retain their original duration. An exam cannot be published without an access password.

Submitted programming answers appear in **Programming submissions** and in the dashboard's **Programming answers awaiting marks** queue until marked. Open an answer to review the exact submitted source, inspect saved versions, award marks, and add feedback.

## MySQL Setup

Create a MySQL database using `utf8mb4`, then set `DATABASE_URL` before migrations. For PowerShell:

```powershell
$env:DATABASE_URL = "mysql://proctorhall_user:your-url-encoded-password@127.0.0.1:3306/proctorhall"
$env:SECRET_KEY = "generate-a-long-random-secret"
$env:ALLOWED_HOSTS = "localhost,127.0.0.1"
python manage.py migrate
python manage.py runserver
```

Install a compatible MySQL Server (8.0.11 or later) and the `mysqlclient` build prerequisites for your platform. In production set `APP_ENV=production`, use HTTPS, and provide a strong `SECRET_KEY`; production cookies are marked secure and Django rejects an unset secret.

## Free Demo Deployment (Render + Neon)

The repository includes a Render Blueprint at `../render.yaml`. It configures a free Render web service, installs the Python dependencies, collects static files, runs database migrations at startup, and starts Django with Gunicorn. The app supports PostgreSQL connection URLs (including Neon URLs) through `DATABASE_URL`; static assets are served by WhiteNoise.

1. Push this repository to GitHub and make sure the GitHub repository is private if it contains non-public exam material.
2. Create a free PostgreSQL project on [Neon](https://neon.com/) and copy its **pooled** connection string. Keep the string private.
3. In [Render](https://render.com/), create a **Blueprint** and connect the GitHub repository containing this project. Render reads `render.yaml` from the repository root.
4. When prompted, set `DATABASE_URL` to the Neon connection string. Set `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_NAME`, and `BOOTSTRAP_ADMIN_PASSWORD` to the lecturer's details; choose a unique password of at least 8 characters. Render generates `SECRET_KEY` and sets `APP_ENV=production`; the app uses Render's hostname automatically.
5. Apply the Blueprint and wait for the first deployment to finish. The startup command applies migrations and creates the initial admin account. Once the deployment succeeds, remove `BOOTSTRAP_ADMIN_PASSWORD` from the Render environment variables and redeploy. The bootstrap command will leave the existing account unchanged on subsequent restarts.
6. Sign in with the lecturer account, create an unpublished test exam, and verify the exam flow before inviting students.

This free setup is for demos and light testing, **not live or high-stakes exams**. Free web services sleep after inactivity, so first page loads can be delayed, and local files are temporary. PostgreSQL keeps application records outside the web service, but the free tier is not a substitute for a production backup and availability plan. Do not upload real student records or rely on free hosting for exam sessions. Use a paid, monitored service with managed backups and an availability plan for real examinations.

## Import Existing Flask Data

The old SQLite database is retained at `instance/proctorhall.db`. Back it up before importing. First migrate Django's schema into your destination database, then run:

```powershell
python manage.py import_legacy_data --source instance\proctorhall.db
```

The importer copies existing users, exams, questions, attempts, snapshots, MCQ answers, and proctor logs while preserving record IDs. Legacy Werkzeug password hashes are retained and upgraded to Django hashes after the user's first successful login. The importer is safe to rerun for the same source database.

## Programming Answers and Execution

Programming questions configure Python, JavaScript, Java, C++, or C#, marks, editor requirement, and whether optional execution is available. Student edits are stored locally first, autosaved to Django, versioned when changed, and submitted as a clearly marked final version. Lecturers can open the source, inspect autosaves, add feedback, and award marks from **Programming submissions**. Source is HTML-escaped in read-only review fields.

### Browser Code Runner (Pyodide)

Django never executes submitted code — not locally, not in Docker. Python test runs use [Pyodide](https://pyodide.org) (CPython compiled to WebAssembly) in a dedicated browser Web Worker. Runtime loading has up to 60 seconds; code execution is stopped after 8 seconds. During an exam, the student's browser runs the code and the server only records a `CODE_RUN` monitoring event; the code is not sent to the server for execution. Lecturers can also test submitted Python source in their own browser from the programming-answer review page.

- Python runs are free and need no Docker, no API keys and no extra server. The runtime is downloaded from a public CDN the first time a student clicks Run.
- Test runs are optional and never graded: the student still submits source for lecturer marking.
- For Java, the lecturer downloads the submitted `.java` file from the review page and compiles/runs it on their own computer with a locally installed JDK (for example, free OpenJDK/Temurin). Check that `javac -version` works, open a terminal in the download folder, then run `javac YourClass.java` and `java YourClass` (replace `YourClass` with the public class name). Java source is not executed by the ProctorHall server.
- JavaScript, C++ and C# submissions can be marked like any other; the in-browser Python runner is not enabled for those languages.
- A run that cannot load (no internet, unsupported browser) still leaves the submission flow intact.

The student's browser screen lock-down warns three times before an exam is submitted automatically, copying and pasting are blocked and recorded, and the recorded activity is hidden from the student during the exam.

## Proctoring and Audit

Camera presence, multiple faces, gaze estimate, object detection, tab changes, window blur, copy/paste/cut attempts, fullscreen exit, camera interruption, lockdown warnings, auto-submissions and multiple active exam sessions create timestamped review events. Events are evidence for lecturer/admin review only; they do not automatically fail an attempt or determine cheating.

Monitoring is deliberately **one-way for students**: during the exam a student only sees that monitoring is live — the recorded events themselves are never shown, so they cannot distract from the paper. Lecturers open **Monitoring log** on an attempt to see the full chronological activity, a summary by severity, and a breakdown by event type.

### Exam lock-down

When a student starts an exam the page enters full screen. Leaving the exam screen — minimizing, switching tab or window, exiting full screen — shows a blocking warning and is recorded. After three warnings the exam is **submitted automatically, answered or not**, and the auto-submission is recorded as a critical event. Copy, paste and cut (including keyboard shortcuts and the right-click menu) are blocked and recorded everywhere on the exam screen; students can still type and edit their own code.

## Admin Workflow

1. **Sign in** with a lecturer/admin account (`python manage.py create_admin --email lecturer@example.com --name "Lecturer Name"`).
2. **Create an exam** — *New exam*, add MCQ and programming questions, then publish.
3. **Review attempts** — the dashboard lists exams, recent attempts and *Programming answers awaiting marks*. Open an attempt's **Monitoring log** for that student's recorded activity.
4. **Mark submissions** — open each programming submission to view the exact source, inspect autosave versions, award marks and write feedback.
5. **Bulk update results** — on the exam page, *Download current results (CSV)*, edit `marks`/`feedback` columns, then *Upload results CSV*. Rows are validated per student and question, scores are recalculated automatically, and skipped rows are reported.


## Tests

```powershell
python manage.py check
python manage.py test
```

The tests use Django's temporary SQLite test database, regardless of the configured production database.
