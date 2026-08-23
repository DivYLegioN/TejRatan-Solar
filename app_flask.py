"""
app_flask.py -- Flask entry point.

index.html contains all the calculator logic client-side (JS mirrors
solar_core.py's formulas). This file serves that page AND exposes
/api/generate-proposal, which turns the calculator's already-computed
numbers into a PPTX + PDF + JSON client proposal via proposal_engine.py.

IMPORTANT: this route does NOT recalculate anything. It only formats
numbers the frontend already computed and posted. See proposal_engine.py.

Start Command on Render: gunicorn app_flask:app
"""

from flask import Flask, send_from_directory, request, jsonify
import os

# Keep solar_core importable / importing cleanly (also catches syntax errors
# early during deploy) even though index.html doesn't call it yet.
import solar_core  # noqa: F401

import proposal_engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATED_DIR = proposal_engine.OUTPUT_ROOT

app = Flask(__name__)


@app.route("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


# Serves any other static files (css/js/images) placed alongside index.html,
# in case you add them later.
@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR, filename)


# ---------------------------------------------------------------------------
# Proposal generation
# ---------------------------------------------------------------------------
@app.route("/api/generate-proposal", methods=["POST"])
def generate_proposal():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"success": False, "error": "Request body must be JSON."}), 400

    try:
        result = proposal_engine.build_proposal(payload)
    except proposal_engine.ValidationError as e:
        return jsonify({"success": False, "error": "Validation failed.", "details": e.errors}), 422
    except Exception as e:  # noqa: BLE001 -- surface a clean error to the frontend
        app.logger.exception("Proposal generation failed")
        return jsonify({"success": False, "error": f"Proposal generation failed: {e}"}), 500

    proposal_id = result["proposal_id"]
    return jsonify({
        "success": True,
        "proposal_id": proposal_id,
        "pptx_url": f"/generated_proposals/{proposal_id}/proposal.pptx",
        "pdf_url": f"/generated_proposals/{proposal_id}/proposal.pdf",
        "json_url": f"/generated_proposals/{proposal_id}/proposal_data.json",
        "pdf_method": result["pdf_method"],  # "libreoffice" or "reportlab_fallback"
    })


@app.route("/generated_proposals/<path:subpath>")
def serve_generated_proposal(subpath):
    return send_from_directory(GENERATED_DIR, subpath)


if __name__ == "__main__":
    # Local dev only -- Render uses gunicorn, not this.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
