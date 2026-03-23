from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .dashboard_data import (
    load_overview_data,
    load_status_breakdown_data,
    load_ageing_buckets_data,
    load_trends_data,
    load_worklist_data,
    load_po_master_data,
    load_invoices_data,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "irs.db"
DEFAULT_OUT_PATH = Path(__file__).resolve().parent.parent / "exports" / "snapshot.json"


def build_snapshot(
    db_path: Path = DEFAULT_DB_PATH,
    include_trends: bool = False,
    include_worklist: bool = True,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()

    overview = load_overview_data(db_path)
    if "_error" in overview:
        return {"_error": overview["_error"], "generated_at": now}

    snapshot = {
        "generated_at": now,
        "overview": overview,
        "status_breakdown": load_status_breakdown_data(db_path),
        "ageing_buckets": load_ageing_buckets_data(db_path),
        "worklist": load_worklist_data(db_path) if include_worklist else [],
        "trends": load_trends_data(db_path) if include_trends else [],
        "po_master": load_po_master_data(db_path),
        "invoices": load_invoices_data(db_path),
    }
    return snapshot


def write_snapshot(
    db_path: Path = DEFAULT_DB_PATH,
    out_path: Path = DEFAULT_OUT_PATH,
    **kwargs,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = build_snapshot(db_path, **kwargs)
    content = json.dumps(data, ensure_ascii=False, indent=2)

    # Write to temp file then atomically replace to prevent partial snapshots
    fd, tmp_path = tempfile.mkstemp(
        dir=str(out_path.parent), suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(out_path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    return out_path
