from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


READY_STATUSES = ("VALID_PO",)


@dataclass(frozen=True)
class ReadinessRule:
    ready_predicate_sql: str
    ready_params: tuple
    manual_predicate_sql: str
    manual_params: tuple
    source: str


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    if not row:
        return None
    return list(row)[0]


def fetch_rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        scalar(
            conn,
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1;",
            (name,),
        )
        is not None
    )


def get_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def build_readiness_rule(invoice_cols: set[str]) -> ReadinessRule:
    if "ready_to_post" in invoice_cols:
        return ReadinessRule(
            ready_predicate_sql="ready_to_post = 1",
            ready_params=(),
            manual_predicate_sql="(ready_to_post IS NULL OR ready_to_post <> 1)",
            manual_params=(),
            source="ready_to_post",
        )

    if "po_match_status" in invoice_cols:
        placeholders = ", ".join(["?"] * len(READY_STATUSES))
        return ReadinessRule(
            ready_predicate_sql=f"po_match_status IN ({placeholders})",
            ready_params=tuple(READY_STATUSES),
            manual_predicate_sql=f"(po_match_status IS NULL OR po_match_status NOT IN ({placeholders}))",
            manual_params=tuple(READY_STATUSES),
            source="po_match_status",
        )

    return ReadinessRule(
        ready_predicate_sql="0 = 1",
        ready_params=(),
        manual_predicate_sql="1 = 1",
        manual_params=(),
        source="fallback_none_ready",
    )


def pence_or_zero(pence: Any) -> int:
    try:
        return int(pence) if pence is not None else 0
    except (TypeError, ValueError):
        return 0


def parse_iso_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def load_overview_data(db_path: Path) -> dict[str, Any]:
    with get_connection(db_path) as conn:
        if not table_exists(conn, "invoice_document"):
            return {"_error": "Missing table: invoice_document"}

        invoice_cols = get_table_columns(conn, "invoice_document")
        rule = build_readiness_rule(invoice_cols)

        total_present = int(
            scalar(conn, "SELECT COUNT(*) FROM invoice_document WHERE is_currently_present = 1") or 0
        )

        ready_count = int(
            scalar(
                conn,
                f"""
                SELECT COUNT(*)
                FROM invoice_document
                WHERE is_currently_present = 1
                  AND ({rule.ready_predicate_sql})
                """,
                rule.ready_params,
            )
            or 0
        )

        manual_count = int(
            scalar(
                conn,
                f"""
                SELECT COUNT(*)
                FROM invoice_document
                WHERE is_currently_present = 1
                  AND ({rule.manual_predicate_sql})
                """,
                rule.manual_params,
            )
            or 0
        )

        po_confidence = round((ready_count / total_present) * 100, 1) if total_present else None
        last_scan = scalar(conn, "SELECT MAX(last_scan_datetime) FROM invoice_document")

        oldest_first_seen = scalar(
            conn,
            """
            SELECT MIN(first_seen_datetime)
            FROM invoice_document
            WHERE is_currently_present = 1
            """,
        )
        oldest_days = None
        dt_oldest = parse_iso_dt(oldest_first_seen)
        if dt_oldest:
            oldest_days = (datetime.now(dt_oldest.tzinfo) - dt_oldest).days

        known_exposure_pence = scalar(
            conn,
            """
            SELECT SUM(gross_total)
            FROM invoice_document
            WHERE is_currently_present = 1
              AND gross_total IS NOT NULL
              AND gross_total > 0
            """,
        )

        biggest_invoice_pence = scalar(
            conn,
            """
            SELECT MAX(gross_total)
            FROM invoice_document
            WHERE is_currently_present = 1
              AND gross_total IS NOT NULL
              AND gross_total > 0
            """,
        )

        value_covered = int(
            scalar(
                conn,
                """
                SELECT SUM(CASE WHEN gross_total IS NOT NULL AND gross_total > 0 THEN 1 ELSE 0 END)
                FROM invoice_document
                WHERE is_currently_present = 1
                """,
            )
            or 0
        )
        missing_value_count = max(total_present - value_covered, 0)
        value_coverage_pct = round((value_covered / total_present) * 100, 1) if total_present else None

        median_gross_pence = scalar(
            conn,
            """
            SELECT AVG(gross_total) FROM (
                SELECT gross_total
                FROM invoice_document
                WHERE is_currently_present = 1
                  AND gross_total IS NOT NULL
                  AND gross_total > 0
                ORDER BY gross_total
                LIMIT 2 - (SELECT COUNT(*) FROM invoice_document
                           WHERE is_currently_present = 1
                             AND gross_total IS NOT NULL
                             AND gross_total > 0) % 2
                OFFSET (SELECT (COUNT(*) - 1) / 2 FROM invoice_document
                        WHERE is_currently_present = 1
                          AND gross_total IS NOT NULL
                          AND gross_total > 0)
            )
            """,
        )

        estimated_missing_exposure_pence = None
        if missing_value_count > 0 and median_gross_pence is not None:
            estimated_missing_exposure_pence = int(float(median_gross_pence)) * missing_value_count

        total_estimated_exposure_pence = pence_or_zero(known_exposure_pence) + pence_or_zero(
            estimated_missing_exposure_pence
        )

        ready_exposure_pence = scalar(
            conn,
            f"""
            SELECT SUM(gross_total)
            FROM invoice_document
            WHERE is_currently_present = 1
              AND ({rule.ready_predicate_sql})
              AND gross_total IS NOT NULL
              AND gross_total > 0
            """,
            rule.ready_params,
        )
        ready_known_exposure_pence = ready_exposure_pence

        manual_known_exposure_pence = scalar(
            conn,
            f"""
            SELECT SUM(gross_total)
            FROM invoice_document
            WHERE is_currently_present = 1
              AND ({rule.manual_predicate_sql})
              AND gross_total IS NOT NULL
              AND gross_total > 0
            """,
            rule.manual_params,
        )

        manual_value_covered = int(
            scalar(
                conn,
                f"""
                SELECT SUM(CASE WHEN gross_total IS NOT NULL AND gross_total > 0 THEN 1 ELSE 0 END)
                FROM invoice_document
                WHERE is_currently_present = 1
                  AND ({rule.manual_predicate_sql})
                """,
                rule.manual_params,
            )
            or 0
        )
        manual_missing_value_count = max(manual_count - manual_value_covered, 0)

        manual_estimated_missing_exposure_pence = None
        if manual_missing_value_count > 0 and median_gross_pence is not None:
            manual_estimated_missing_exposure_pence = int(float(median_gross_pence)) * manual_missing_value_count

        manual_total_estimated_exposure_pence = pence_or_zero(manual_known_exposure_pence) + pence_or_zero(
            manual_estimated_missing_exposure_pence
        )

        ocr_needed_count = None
        if "po_match_status" in invoice_cols:
            ocr_needed_count = int(
                scalar(
                    conn,
                    """
                    SELECT COUNT(*)
                    FROM invoice_document
                    WHERE is_currently_present = 1
                      AND po_match_status = 'NO_TEXT_LAYER'
                    """,
                )
                or 0
            )

        return {
            "readiness_source": rule.source,
            "total_present": total_present,
            "ready_count": ready_count,
            "manual_count": manual_count,
            "po_confidence": po_confidence,
            "value_coverage_pct": value_coverage_pct,
            "last_scan": last_scan,
            "oldest_days": oldest_days,
            "known_exposure_pence": known_exposure_pence,
            "estimated_missing_exposure_pence": estimated_missing_exposure_pence,
            "total_estimated_exposure_pence": total_estimated_exposure_pence,
            "median_gross_pence": median_gross_pence,
            "biggest_invoice_pence": biggest_invoice_pence,
            "value_covered": value_covered,
            "missing_value_count": missing_value_count,
            "ready_exposure_pence": ready_exposure_pence,
            "ready_known_exposure_pence": ready_known_exposure_pence,
            "manual_known_exposure_pence": manual_known_exposure_pence,
            "manual_estimated_missing_exposure_pence": manual_estimated_missing_exposure_pence,
            "manual_total_estimated_exposure_pence": manual_total_estimated_exposure_pence,
            "manual_missing_value_count": manual_missing_value_count,
            "ocr_needed_count": ocr_needed_count,
        }


def load_status_breakdown_data(db_path: Path) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        if not table_exists(conn, "invoice_document"):
            return []
        cols = get_table_columns(conn, "invoice_document")
        if "po_match_status" not in cols:
            return []

        rows = fetch_rows(
            conn,
            """
            SELECT
                po_match_status AS status,
                COUNT(*) AS cnt,
                SUM(CASE WHEN gross_total IS NOT NULL AND gross_total > 0 THEN gross_total ELSE 0 END) AS gross_pence
            FROM invoice_document
            WHERE is_currently_present = 1
            GROUP BY po_match_status
            ORDER BY cnt DESC, status ASC
            """,
        )
        return [dict(r) for r in rows]


def load_ageing_buckets_data(db_path: Path) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        if not table_exists(conn, "invoice_document"):
            return []
        cols = get_table_columns(conn, "invoice_document")
        rule = build_readiness_rule(cols)

        rows = fetch_rows(
            conn,
            f"""
            WITH base AS (
              SELECT
                first_seen_datetime,
                gross_total,
                CASE WHEN ({rule.ready_predicate_sql}) THEN 1 ELSE 0 END AS is_ready,
                CAST((julianday('now') - julianday(first_seen_datetime)) AS INTEGER) AS age_days
              FROM invoice_document
              WHERE is_currently_present = 1
            ),
            bucketed AS (
              SELECT
                CASE
                  WHEN age_days <= 1 THEN '0-1 days'
                  WHEN age_days BETWEEN 2 AND 3 THEN '2-3 days'
                  WHEN age_days BETWEEN 4 AND 7 THEN '4-7 days'
                  WHEN age_days BETWEEN 8 AND 14 THEN '8-14 days'
                  ELSE '15+ days'
                END AS age_bucket,
                CASE WHEN is_ready = 1 THEN 'Ready' ELSE 'Manual' END AS lane,
                COUNT(*) AS cnt,
                SUM(CASE WHEN gross_total IS NOT NULL AND gross_total > 0 THEN gross_total ELSE 0 END) AS gross_pence
              FROM base
              GROUP BY age_bucket, lane
            )
            SELECT age_bucket, lane, cnt, gross_pence
            FROM bucketed
            ORDER BY
              CASE age_bucket
                WHEN '0-1 days' THEN 1
                WHEN '2-3 days' THEN 2
                WHEN '4-7 days' THEN 3
                WHEN '8-14 days' THEN 4
                ELSE 5
              END,
              lane DESC
            """,
            rule.ready_params,
        )
        return [dict(r) for r in rows]


def load_worklist_data(db_path: Path) -> list[dict[str, Any]]:
    """Read worklist rows from SQLite for inclusion in snapshot.json."""
    with get_connection(db_path) as conn:
        if not table_exists(conn, "invoice_worklist"):
            return []

        rows = fetch_rows(
            conn,
            """
            SELECT
              document_hash,
              file_name AS attachment_name,
              scanned_datetime AS received_datetime,
              next_action,
              action_reason,
              priority,
              generated_at_utc,
              is_currently_present
            FROM invoice_worklist
            ORDER BY priority ASC, document_hash ASC
            """,
        )

        return [dict(r) for r in rows]


def load_po_master_data(db_path: Path) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        if not table_exists(conn, "po_master"):
            return []
        rows = fetch_rows(
            conn,
            """
            SELECT
                pm.po_number,
                pm.supplier_account,
                pm.po_status,
                pm.approval_status,
                pm.last_import_datetime,
                COUNT(id.document_hash) AS invoice_count,
                SUM(CASE WHEN id.gross_total IS NOT NULL AND id.gross_total > 0
                         THEN id.gross_total ELSE 0 END) AS gross_total_pence
            FROM po_master pm
            LEFT JOIN invoice_po ip ON pm.po_number = ip.po_number
            LEFT JOIN invoice_document id ON ip.document_hash = id.document_hash
            GROUP BY pm.po_number
            ORDER BY pm.po_number ASC
            """,
        )
        return [dict(r) for r in rows]


def load_invoices_data(db_path: Path) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        if not table_exists(conn, "invoice_document"):
            return []
        rows = fetch_rows(
            conn,
            """
            SELECT
                id.document_hash,
                id.file_name AS attachment_file_name,
                id.po_match_status,
                GROUP_CONCAT(ip.po_number, ', ') AS po_number,
                id.gross_total,
                id.first_seen_datetime,
                id.processing_status,
                id.is_currently_present
            FROM invoice_document id
            LEFT JOIN invoice_po ip ON id.document_hash = ip.document_hash
            GROUP BY id.document_hash
            ORDER BY id.first_seen_datetime DESC
            """,
        )
        return [dict(r) for r in rows]


def load_trends_data(db_path: Path) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        if not table_exists(conn, "inbox_snapshot_daily"):
            return []
        rows = fetch_rows(
            conn,
            """
            SELECT *
            FROM inbox_snapshot_daily
            ORDER BY snapshot_date DESC
            LIMIT 60
            """,
        )
        return [dict(r) for r in rows]
