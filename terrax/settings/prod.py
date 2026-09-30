"""
Production settings.

Fails loudly rather than starting in an insecure state. `manage.py check
--deploy` passes clean against this module.

Two things a serverless host cannot give a Django application are a writable
disk and a process that stays alive, so both are configured away here rather
than assumed: the database comes from `DATABASE_URL`, and uploads go to object
storage when it is configured.
"""

from decouple import config
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False

SECRET_KEY = config("SECRET_KEY", default="")
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY must be set in the environment.")

# --- Database -------------------------------------------------------------
# SQLite is the development default and cannot be the production one: the file
# is not in the deployment, and the filesystem it would live on is read-only.
# Saying so here turns a stack trace on the first query into one clear line in
# the build log.
if not DATABASE_URL:  # noqa: F405
    raise ImproperlyConfigured(
        "DATABASE_URL must be set in production. SQLite needs a writable disk, "
        "which a serverless host does not have. Any Postgres URL works; Neon "
        "and Supabase both have a free tier."
    )

# --- Hosts ----------------------------------------------------------------
# Vercel supplies the deployment's own hostname, which changes with every
# preview build, so it is read rather than listed.
VERCEL_URL = config("VERCEL_URL", default="")
VERCEL_BRANCH_URL = config("VERCEL_PROJECT_PRODUCTION_URL", default="")

for host in (VERCEL_URL, VERCEL_BRANCH_URL):
    if host and host not in ALLOWED_HOSTS:  # noqa: F405
        ALLOWED_HOSTS.append(host)  # noqa: F405

if not ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set in the environment.")

# Django 4+ checks the Origin header on unsafe requests behind HTTPS, and a
# bare hostname is not a valid origin.
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS if host != "*"]  # noqa: F405

# --- Media ----------------------------------------------------------------
# The filesystem is read-only apart from /tmp, and /tmp does not survive the
# invocation, so an upload has nowhere to land. Cloudinary is used when it is
# configured; without it uploads are refused rather than silently lost.
CLOUDINARY_URL = config("CLOUDINARY_URL", default="")

if CLOUDINARY_URL:
    INSTALLED_APPS = [*INSTALLED_APPS, "cloudinary", "cloudinary_storage"]  # noqa: F405
    STORAGES = {
        **STORAGES,  # noqa: F405
        "default": {"BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"},
    }

# --- Transport ------------------------------------------------------------
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "Lax"

X_FRAME_OPTIONS = "DENY"

# --- Logging --------------------------------------------------------------
# One handler to stdout, which is what a serverless platform collects. Django's
# default mails the admins, and there is no mail server here.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "{levelname} {name} {message}", "style": "{"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}
