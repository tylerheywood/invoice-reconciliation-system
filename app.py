from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from core.load_po_master import load_po_master
from core.snapshot import write_snapshot
from core.db import get_connection

ROOT = Path(__file__).resolve().parent
EXPORTS_DIR = ROOT / "exports"
DATA_DIR = ROOT / "data"
SNAPSHOT_PATH = EXPORTS_DIR / "snapshot.json"
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload cap


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

    ext = Path(f.filename).name  # strip directory components
    ext = Path(ext).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"ok": False, "error": f"Unsupported file type: {ext}. Use .csv or .xlsx"}), 400

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    final_path = DATA_DIR / f"po_upload{ext}"

    # Write to a temp file then atomically rename to avoid partial-file races
    fd, tmp_path = tempfile.mkstemp(dir=str(DATA_DIR), suffix=ext)
    try:
        os.close(fd)
        f.save(tmp_path)
        os.replace(tmp_path, str(final_path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    try:
        result = load_po_master(final_path)
        write_snapshot()
        return jsonify({"ok": True, "po_loaded": result["po_loaded"]})
    except Exception:
        app.logger.exception("PO master load failed")
        return jsonify({"ok": False, "error": "Failed to process PO file. Check the server logs for details."}), 500


STAGING_DIR = ROOT / "staging"


@app.route("/api/mark-posted", methods=["POST"])
def mark_posted():
    """Mark an invoice as posted. Sets posted_datetime and refreshes the snapshot."""
    data = request.get_json(silent=True) or {}
    doc_hash = data.get("document_hash", "")
    if not doc_hash or not re.fullmatch(r"[0-9a-f]{64}", doc_hash):
        return jsonify({"ok": False, "error": "document_hash must be a 64-character hex string"}), 400

    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE invoice_document SET posted_datetime = ?, processing_status = 'POSTED' WHERE document_hash = ?",
            (now, doc_hash),
        )
        if cur.rowcount == 0:
            return jsonify({"ok": False, "error": "Invoice not found"}), 404
        conn.commit()
    finally:
        conn.close()

    try:
        write_snapshot()
    except Exception:
        app.logger.exception("Snapshot refresh failed after mark-posted")

    return jsonify({"ok": True, "posted_datetime": now})


@app.route("/api/add-note", methods=["POST"])
def add_note():
    """Add a review note to an invoice."""
    data = request.get_json(silent=True) or {}
    doc_hash = data.get("document_hash", "")
    note = data.get("note", "").strip()
    if not doc_hash or not re.fullmatch(r"[0-9a-f]{64}", doc_hash):
        return jsonify({"ok": False, "error": "document_hash must be a 64-character hex string"}), 400
    if not note:
        return jsonify({"ok": False, "error": "note required"}), 400
    if len(note) > 2000:
        return jsonify({"ok": False, "error": "note must not exceed 2000 characters"}), 400

    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE invoice_document SET review_note = ?, reviewed_datetime = ?, review_outcome = 'REVIEWED' WHERE document_hash = ?",
            (note, now, doc_hash),
        )
        if cur.rowcount == 0:
            return jsonify({"ok": False, "error": "Invoice not found"}), 404
        conn.commit()
    finally:
        conn.close()

    try:
        write_snapshot()
    except Exception:
        app.logger.exception("Snapshot refresh failed after add-note")

    return jsonify({"ok": True})


@app.route("/api/pdf/<doc_hash>")
def serve_pdf(doc_hash):
    """Serve a staged PDF by its document hash, validated against the database."""
    if not doc_hash or not doc_hash.isalnum():
        return jsonify({"error": "Invalid hash"}), 400

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM invoice_document WHERE document_hash = ? LIMIT 1",
            (doc_hash,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "Invoice not found"}), 404

    matches = sorted(STAGING_DIR.glob(f"{doc_hash[:12]}_*"))
    if not matches:
        return jsonify({"error": "PDF file missing from staging despite database record existing"}), 404

    return send_file(matches[0], mimetype="application/pdf")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
