from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dashboard_data import (
    load_overview_data,
    load_status_breakdown_data,
    load_ageing_buckets_data,
    load_trends_data,
    load_worklist_data,
)


@dataclass(frozen=True)
class PublishConfig:
    enabled: bool
    db_path: Path
    out_path: Path
    vps_host: str
    remote_dir: str
    remote_name: str = "snapshot.json"
    include_trends: bool = False
    include_worklist: bool = True


def build_snapshot(cfg: PublishConfig) -> dict:
    now = datetime.now(timezone.utc).isoformat()

    overview = load_overview_data(cfg.db_path)
    if "_error" in overview:
        return {"_error": overview["_error"], "generated_at": now}

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overview": overview,
        "status_breakdown": load_status_breakdown_data(cfg.db_path),
        "ageing_buckets": load_ageing_buckets_data(cfg.db_path),
        "worklist": load_worklist_data(cfg.db_path),
        "trends": load_trends_data(cfg.db_path) if cfg.include_trends else [],
    }

    return snapshot


def write_snapshot(cfg: PublishConfig) -> None:
    cfg.out_path.parent.mkdir(parents=True, exist_ok=True)
    data = build_snapshot(cfg)
    cfg.out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def publish_to_vps(cfg: PublishConfig) -> None:
    remote_tmp = f"{cfg.remote_dir}/{cfg.remote_name}.new"
    remote_live = f"{cfg.remote_dir}/{cfg.remote_name}"

    subprocess.run(["scp", str(cfg.out_path), f"{cfg.vps_host}:{remote_tmp}"], check=True)
    subprocess.run(["ssh", cfg.vps_host, f"mv {remote_tmp} {remote_live}"], check=True)


def run_publish(cfg: PublishConfig, log=print) -> bool:
    if not cfg.enabled:
        return False

    try:
        write_snapshot(cfg)
        publish_to_vps(cfg)
        log(f"[PUBLISH] OK → {cfg.vps_host}:{cfg.remote_dir}/{cfg.remote_name}")
        return True
    except Exception as e:
        log(f"[PUBLISH] FAILED (non-fatal): {e}")
        return False
