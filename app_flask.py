"""
app_flask.py -- minimal Flask entry point.

index.html already contains all the calculator logic client-side (JS mirrors
solar_core.py's formulas), so this file's only job is to serve that page.
solar_core.py is imported so it stays available for any future
server-side use, but no routes depend on it yet.

Start Command on Render: gunicorn app_flask:app
"""

from flask import Flask, send_from_directory
import os

# Keep solar_core importable / importing cleanly (also catches syntax errors
# early during deploy) even though index.html doesn't call it yet.
import solar_core  # noqa: F401

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)


@app.route("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


# Serves any other static files (css/js/images) placed alongside index.html,
# in case you add them later.
@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    # Local dev only -- Render uses gunicorn, not this.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
