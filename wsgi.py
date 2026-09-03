# wsgi.py
# Public "hello world" homepage at "/" + the real tool mounted at a hidden path.
#
# The mount path defaults to "sbb". Override by setting the SECRET_PATH env var
# or by writing a value into `.secret_path`. Nothing about the real tool is
# exposed at "/" — the public only ever sees a bare hello world.

import os

from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.exceptions import NotFound
from flask import Flask

from app import app as _tool_app  # the real application (login-gated)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_PATH = "sbb"


def _resolve_secret_path() -> str:
    """Return the URL segment the real tool is mounted under (no slashes)."""
    env = (os.getenv("SECRET_PATH") or "").strip().strip("/")
    if env:
        return env
    path_file = os.path.join(_BASE_DIR, ".secret_path")
    if os.path.exists(path_file):
        with open(path_file, "r") as f:
            val = f.read().strip().strip("/")
            if val:
                return val
    return DEFAULT_PATH


SECRET_PATH = _resolve_secret_path()
MOUNT = "/" + SECRET_PATH

# --- Public app: bare hello world, zero branding, no auth ---
# static_folder=None disables Flask's built-in /static/<file> route, otherwise
# the public app would serve the tool's JS/CSS at the root and leak branding.
public_app = Flask(__name__, static_folder=None)


@public_app.route("/")
def public_home():
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Hello World</title>"
        "<style>html,body{height:100%;margin:0}"
        "body{display:flex;align-items:center;justify-content:center;"
        "font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;"
        "background:#0b0f14;color:#e6edf3}"
        "h1{font-weight:600;font-size:clamp(28px,6vw,56px);letter-spacing:-.02em}"
        "</style></head><body><h1>Hello, World!</h1></body></html>"
    )


@public_app.route("/health")
def public_health():
    return {"ok": True}


# Everything not under the secret mount is served by the public app.
# The real tool lives only under MOUNT and is invisible from "/".
application = DispatcherMiddleware(public_app, {MOUNT: _tool_app})

if __name__ == "__main__":
    # Dev only. Production runs via gunicorn: `gunicorn wsgi:application`
    from werkzeug.serving import run_simple

    print(f"Public homepage:  http://127.0.0.1:5000/")
    print(f"Hidden tool at:   http://127.0.0.1:5000{MOUNT}/")
    run_simple("0.0.0.0", 5000, application, use_reloader=False, threaded=True)
