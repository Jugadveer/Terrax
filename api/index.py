"""
The entry point Vercel invokes.

Vercel's Python runtime looks for a module-level `app` and speaks WSGI to it,
so this is the whole adapter: pick the production settings and hand over
Django's own WSGI application. Nothing about the project is shaped around the
host, and running it anywhere else is the same `terrax.wsgi` it always was.

The one addition is what happens when the application refuses to start. A
missing environment variable or an app that will not import raises during
`django.setup()`, which on a serverless host surfaces as an opaque
`FUNCTION_INVOCATION_FAILED` with the reason buried in a log nobody has open.
Catching it answers 503 with the reason instead, and writes the traceback to
stderr where the platform collects it.
"""

import os
import traceback

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "terrax.settings.prod")

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Not configured</title>
<style>
  body {{ font: 16px/1.6 ui-sans-serif, system-ui, sans-serif; color: #1a1d1b;
         background: #fbfbf9; margin: 0; display: grid; place-items: center;
         min-height: 100vh; padding: 24px; }}
  main {{ max-width: 34rem; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 .75rem; }}
  code {{ background: #eceee9; padding: .1em .35em; border-radius: 4px;
          font: 0.9em ui-monospace, monospace; }}
  p {{ margin: 0 0 1rem; }}
  .reason {{ border-left: 3px solid #c8825a; padding-left: 1rem; color: #4a4f4b; }}
</style></head>
<body><main>
  <h1>Terrax is deployed but did not start</h1>
  <p class="reason">{reason}</p>
  <p>Set the missing value in the host's environment variables and redeploy.
     The full list is in <code>.env.example</code>, and the whole traceback is
     in the platform's runtime log.</p>
</main></body></html>
"""


def _refuse(reason: str):
    """A WSGI app that explains itself instead of crashing the invocation."""
    body = PAGE.format(reason=reason).encode()

    def application(environ, start_response):
        start_response(
            "503 Service Unavailable",
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ],
        )
        return [body]

    return application


try:
    app = get_wsgi_application()
except Exception as exc:  # noqa: BLE001 - a dead function explains nothing
    traceback.print_exc()
    app = _refuse(f"{type(exc).__name__}: {exc}")
