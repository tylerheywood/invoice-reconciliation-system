from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from load_po_master import load_po_master
from snapshot import write_snapshot

ROOT = Path(__file__).resolve().parent
EXPORTS_DIR = ROOT / "exports"
DATA_DIR = ROOT / "data"
SNAPSHOT_PATH = EXPORTS_DIR / "snapshot.json"
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    return send_file(ROOT / "dashboard.html")


@app.route("/snapshot.json")
def snapshot():
    if not SNAPSHOT_PATH.exists():
        return jsonify({"_error": "snapshot.json not found — run the pipeline first"}), 404
    resp = send_from_directory(str(EXPORTS_DIR), "snapshot.json", mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/upload-po", methods=["POST"])
def upload_po():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "No file selected"}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"ok": False, "error": f"Unsupported file type: {ext}. Use .csv or .xlsx"}), 400

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_path = DATA_DIR / f"po_upload{ext}"
    f.save(str(save_path))

    try:
        result = load_po_master(save_path)
        write_snapshot()
        return jsonify({"ok": True, "po_loaded": result["po_loaded"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
