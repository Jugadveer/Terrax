"""
The entry point Vercel invokes.

Vercel's Python runtime looks for a module-level `app` and speaks WSGI to it,
so this is the whole adapter: pick the production settings and hand over
Django's own WSGI application. Nothing about the project is shaped around the
host, and running it anywhere else is the same `basix.wsgi` it always was.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "basix.settings.prod")

app = get_wsgi_application()
