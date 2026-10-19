import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY and os.environ.get("APP_ENV") == "production":
    raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")
SECRET_KEY = SECRET_KEY or "development-only-change-me"
DEBUG = os.environ.get("APP_ENV") != "production"
_configured_hosts = os.environ.get("ALLOWED_HOSTS")
if _configured_hosts:
    ALLOWED_HOSTS = [host.strip() for host in _configured_hosts.split(",") if host.strip()]
else:
    _render_hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    ALLOWED_HOSTS = [_render_hostname] if _render_hostname else ["127.0.0.1", "localhost"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "exams.apps.ExamsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "proctorhall_site.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "exams.context_processors.exam_runner_settings",
        "exams.context_processors.admin_navigation",
    ]},
}]
WSGI_APPLICATION = "proctorhall_site.wsgi.application"

_database_url = os.environ.get("DATABASE_URL", "")
if _database_url.startswith(("postgres://", "postgresql://")):
    _parsed = urlparse(_database_url)
    _database_options = {
        key: values[-1]
        for key, values in parse_qs(_parsed.query).items()
        if values
    }
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(_parsed.path.lstrip("/")),
        "USER": unquote(_parsed.username or ""),
        "PASSWORD": unquote(_parsed.password or ""),
        "HOST": _parsed.hostname or "localhost",
        "PORT": str(_parsed.port or 5432),
        "OPTIONS": _database_options,
        "CONN_MAX_AGE": 60,
    }}
elif _database_url.startswith(("mysql://", "mysql+pymysql://")):
    _parsed = urlparse(_database_url)
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": unquote(_parsed.path.lstrip("/")),
        "USER": unquote(_parsed.username or ""),
        "PASSWORD": unquote(_parsed.password or ""),
        "HOST": _parsed.hostname or "localhost",
        "PORT": str(_parsed.port or 3306),
        "OPTIONS": {"charset": "utf8mb4"},
        "CONN_MAX_AGE": 60,
    }}
elif not _database_url:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SQLITE_PATH", str(BASE_DIR / "instance" / "proctorhall-django.sqlite3")),
    }}
else:
    raise ValueError("DATABASE_URL must use postgres://, postgresql://, or mysql://")

AUTH_USER_MODEL = "exams.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 3600
# Programming code is executed in the student's own browser (Pyodide) — never on this server.
PYODIDE_VERSION = os.environ.get("PYODIDE_VERSION", "0.26.4")
