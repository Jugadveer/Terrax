"""
Settings for the build step, which collects static files and nothing else.

`collectstatic` reads templates and writes a manifest. It never signs a cookie,
opens a database connection, or answers a request, so the three values
production refuses to start without are supplied here rather than being
required of the build environment.

Keeping this in its own module means the checks in `prod.py` still stop the
site from serving when something real is missing, which is the point of them.
"""

import os
import secrets

os.environ.setdefault("SECRET_KEY", secrets.token_urlsafe(50))
os.environ.setdefault("ALLOWED_HOSTS", "build.invalid")
os.environ.setdefault("DATABASE_URL", "sqlite:///build-only-never-opened.sqlite3")

from .prod import *  # noqa: E402,F401,F403
