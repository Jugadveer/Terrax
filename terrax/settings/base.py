"""
Settings shared by every environment.

Split into base/dev/prod so that a missing production secret can never be
silently papered over by a development default. `manage.py` and `wsgi.py`
point at `terrax.settings.dev` and `terrax.settings.prod` respectively.
"""

from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core",
    "accounts",
    "properties",
    "market",
    "intelligence",
    "chain",
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

ROOT_URLCONF = "terrax.urls"
WSGI_APPLICATION = "terrax.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site",
            ],
        },
    },
]

# SQLite locally, whatever `DATABASE_URL` names when it is set. A deployment on
# a serverless platform has no writable disk, so the URL is how it gets a real
# database without any other setting changing.
DATABASE_URL = config("DATABASE_URL", default="")

if DATABASE_URL:
    import dj_database_url

    # SSL is required of Postgres, which is what a managed database will be,
    # and never of the others: SQLite's `connect()` has no `sslmode` argument
    # and raises a TypeError if one arrives.
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
            ssl_require=DATABASE_URL.startswith(("postgres://", "postgresql://")),
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Fingerprints filenames and pre-compresses, so static assets can be served
    # with a one-year immutable cache header and never re-fetched.
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "core:home"

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "terrax",
        "TIMEOUT": 300,
    }
}

# --- Uploads --------------------------------------------------------------
# Enforced by the forms in properties/forms.py, not just by the widget.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
MAX_IMAGES_PER_LISTING = 12
ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
ALLOWED_DOCUMENT_TYPES = ("application/pdf", "image/jpeg", "image/png")

# --- IPFS -----------------------------------------------------------------
PINATA_API_KEY = config("PINATA_API_KEY", default="")
PINATA_SECRET_API_KEY = config("PINATA_SECRET_API_KEY", default="")
PINATA_BASE_URL = config("PINATA_BASE_URL", default="https://api.pinata.cloud/pinning")

# --- Chain ----------------------------------------------------------------
# Blank by default. With no RPC URL and no key the site is fully functional and
# every property is recorded in a local registry that says it is local. Set
# these to put the same records on a testnet.
WEB3_RPC_URL = config("WEB3_RPC_URL", default="")
WEB3_PRIVATE_KEY = config("WEB3_PRIVATE_KEY", default="")
WEB3_CHAIN_ID = config("WEB3_CHAIN_ID", default=80002, cast=int)  # Polygon Amoy
DEED_CONTRACT_ADDRESS = config("DEED_CONTRACT_ADDRESS", default="")
SHARES_CONTRACT_ADDRESS = config("SHARES_CONTRACT_ADDRESS", default="")

# --- Language model -------------------------------------------------------
LLM_PROVIDER = config("LLM_PROVIDER", default="none").strip().lower()
LLM_TIMEOUT_SECONDS = config("LLM_TIMEOUT_SECONDS", default=20, cast=int)
LLM_MAX_OUTPUT_TOKENS = config("LLM_MAX_OUTPUT_TOKENS", default=900, cast=int)

GROQ_API_KEY = config("GROQ_API_KEY", default="")
GROQ_MODEL = config("GROQ_MODEL", default="llama-3.3-70b-versatile")
GEMINI_API_KEY = config("GEMINI_API_KEY", default="")
GEMINI_MODEL = config("GEMINI_MODEL", default="gemini-2.5-flash")
OPENROUTER_API_KEY = config("OPENROUTER_API_KEY", default="")
OPENROUTER_MODEL = config(
    "OPENROUTER_MODEL", default="meta-llama/llama-3.3-70b-instruct:free"
)
OLLAMA_BASE_URL = config("OLLAMA_BASE_URL", default="http://localhost:11434")
OLLAMA_MODEL = config("OLLAMA_MODEL", default="llama3.2")

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "{levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}
