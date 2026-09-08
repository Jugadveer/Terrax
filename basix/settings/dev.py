"""Development settings. Never import these from a deployed process."""

import secrets

from decouple import config

from .base import *  # noqa: F401,F403

DEBUG = config("DEBUG", default=True, cast=bool)

# A throwaway key is fine here and means a fresh clone runs with no setup.
# `prod.py` refuses to start without a real one.
SECRET_KEY = config("SECRET_KEY", default="") or secrets.token_urlsafe(50)

ALLOWED_HOSTS = ["*"]

# WhiteNoise caches aggressively by default, which is right in production and
# maddening while editing CSS. Autorefresh restats the file on every request.
WHITENOISE_AUTOREFRESH = True
WHITENOISE_MAX_AGE = 0

# The hashed-filename storage needs a collectstatic run, so development uses
# the plain finder and serves straight from /static.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Keep uploads and thumbnails fast to iterate on locally.
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
