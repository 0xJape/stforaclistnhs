from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
LIMITATION_EN = "ORACLIS scenario projection only. This is not a confirmed outbreak or an official health advisory. Verify with local surveillance data."
LIMITATION_FIL = "Scenario projection lamang ng ORACLIS. Hindi ito kumpirmadong outbreak o opisyal na health advisory. Beripikahin gamit ang lokal na surveillance data."


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def resolve_run(run_dir: str | None) -> Path:
    if run_dir:
        path = Path(run_dir).resolve()
    else:
        pointer = ROOT / "outputs/latest_spatiotemporal_run.txt"
        if not pointer.exists():
            raise FileNotFoundError("No successful ORACLIS run is available.")
        value = pointer.read_text(encoding="utf-8").strip()
        path = Path(value) if Path(value).is_absolute() else ROOT / value
        path = path.resolve()
    if not (path / "SPATIOTEMPORAL_BAYESIAN_SUCCESS.txt").exists():
        raise ValueError("Alert delivery requires a verified successful run.")
    if not (path / "database/oraclis_spatiotemporal.sqlite").exists():
        raise FileNotFoundError("Run database is missing.")
    return path


def eligible_alerts(database: Path) -> list[dict[str, object]]:
    sql = """
        SELECT DATE, PSGC, BARANGAY, MUNICIPALITY, ALERT_LEVEL,
               OUTBREAK_PROBABILITY, POSTERIOR_MEAN_CASES,
               LOWER_CREDIBLE_CASES, UPPER_CREDIBLE_CASES,
               OUTBREAK_ALERT_REASON, DOMINANT_OUTBREAK_FACTOR
        FROM forecasts
        WHERE ALERT_LEVEL IN ('HIGH', 'CRITICAL') AND HIGH_RISK_ONSET = 1
        ORDER BY DATE, PSGC
    """
    uri = "file:" + database.as_posix() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql)]


def reported_cases(psgc: object) -> dict[str, object] | None:
    database = ROOT / "data" / "oraclis_reporting_demo.sqlite"
    if not database.exists():
        return None
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("""SELECT reporting_period, suspected_cases + probable_cases + confirmed_cases AS total_cases, updated_at
            FROM aggregate_reports WHERE psgc=? AND status='approved' ORDER BY reporting_period DESC, updated_at DESC LIMIT 1""", (str(psgc),)).fetchone()
    return dict(row) if row else None

def payload(row: dict[str, object], run_id: str, observed: dict[str, object] | None = None) -> dict[str, object]:
    probability = round(float(row["OUTBREAK_PROBABILITY"]) * 100)
    mean = float(row["POSTERIOR_MEAN_CASES"])
    low = float(row["LOWER_CREDIBLE_CASES"])
    high = float(row["UPPER_CREDIBLE_CASES"])
    place = f"{row['BARANGAY']}, {row['MUNICIPALITY']}"
    event_id = f"RISK_ONSET:{row['DATE']}:{row['PSGC']}:{row['ALERT_LEVEL']}"
    reported_line = f" Latest reported aggregate total: {int(observed['total_cases'])} cases for {observed['reporting_period']}." if observed else " No current reported aggregate total is available."
    english = f"Dengue risk scenario for {place}: {row['ALERT_LEVEL']} modeled alert for {row['DATE']}, with {probability} percent outbreak probability and {mean:.1f} projected cases. Credible range: {low:.1f} to {high:.1f}.{reported_line} Weather context and reported totals are separate from model output. {LIMITATION_EN}"
    filipino = f"Dengue risk scenario para sa {place}: {row['ALERT_LEVEL']} na modeled alert para sa {row['DATE']}, na may {probability} percent outbreak probability at {mean:.1f} projected cases. Credible range: {low:.1f} hanggang {high:.1f}.{reported_line} Magkahiwalay ang weather context, reported totals, at model output. {LIMITATION_FIL}"
    return {
        "event_id": event_id,
        "event_type": "dengue_risk_scenario_onset",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "source_data_status": "synthetic_or_interpolated_scenario",
        "scenario_date": row["DATE"],
        "psgc": row["PSGC"],
        "barangay": row["BARANGAY"],
        "municipality": row["MUNICIPALITY"],
        "alert_level": row["ALERT_LEVEL"],
        "outbreak_probability": row["OUTBREAK_PROBABILITY"],
        "projected_cases": row["POSTERIOR_MEAN_CASES"],
        "credible_interval": [row["LOWER_CREDIBLE_CASES"], row["UPPER_CREDIBLE_CASES"]],
        "reported_case_total": observed["total_cases"] if observed else None,
        "reported_case_period": observed["reporting_period"] if observed else None,
        "reported_case_updated_at": observed["updated_at"] if observed else None,
        "alert_reason": row["OUTBREAK_ALERT_REASON"],
        "dominant_factor": row["DOMINANT_OUTBREAK_FACTOR"],
        "message_en": english,
        "message_fil": filipino,
        "facebook_message": english + "\n\n" + filipino,
        "limitation": LIMITATION_EN,
    }


def open_ledger(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE IF NOT EXISTS deliveries (event_id TEXT PRIMARY KEY, sent_at TEXT NOT NULL, run_id TEXT NOT NULL)")
    return connection


def send(webhook_url: str, secret: str, body: bytes) -> None:
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    request = Request(webhook_url, data=body, headers={"Content-Type": "application/json", "X-ORACLIS-Signature": "sha256=" + signature, "User-Agent": "ORACLIS/1.0"}, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Make.com webhook returned HTTP {response.status}.")
    except HTTPError as exc:
        raise RuntimeError(f"Make.com webhook returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("Make.com webhook is unavailable.") from exc


def dispatch(run_dir: str | None, dry_run: bool) -> tuple[int, int]:
    load_env()
    run = resolve_run(run_dir)
    events = [payload(row, run.name, reported_cases(row["PSGC"])) for row in eligible_alerts(run / "database/oraclis_spatiotemporal.sqlite")]
    if dry_run:
        for event in events:
            print(json.dumps(event, ensure_ascii=False))
        return len(events), 0
    if os.getenv("MAKE_ALERTS_ENABLED", "false").casefold() != "true":
        print("Make.com alerts disabled; no external messages sent.")
        return len(events), 0
    webhook = os.getenv("MAKE_WEBHOOK_URL", "").strip()
    secret = os.getenv("MAKE_WEBHOOK_SECRET", "").strip()
    if not webhook.startswith("https://") or not secret:
        raise ValueError("MAKE_WEBHOOK_URL must use HTTPS and MAKE_WEBHOOK_SECRET is required.")
    sent = 0
    with open_ledger(ROOT / "data/make_alert_delivery.sqlite") as ledger:
        for event in events:
            if ledger.execute("SELECT 1 FROM deliveries WHERE event_id=?", (event["event_id"],)).fetchone():
                continue
            body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
            send(webhook, secret, body)
            ledger.execute("INSERT INTO deliveries VALUES (?,?,?)", (event["event_id"], datetime.now(timezone.utc).isoformat(), run.name))
            ledger.commit()
            sent += 1
    return len(events), sent


def self_test() -> None:
    row = {"DATE": "2026-08-01", "PSGC": "123", "BARANGAY": "Test", "MUNICIPALITY": "Sample", "ALERT_LEVEL": "HIGH", "OUTBREAK_PROBABILITY": 0.75, "POSTERIOR_MEAN_CASES": 4.2, "LOWER_CREDIBLE_CASES": 1.0, "UPPER_CREDIBLE_CASES": 8.0, "OUTBREAK_ALERT_REASON": "test", "DOMINANT_OUTBREAK_FACTOR": "baseline"}
    event = payload(row, "test-run", {"total_cases": 3, "reporting_period": "2026-08-01", "updated_at": "2026-08-01T00:00:00+00:00"})
    assert event["event_id"] == "RISK_ONSET:2026-08-01:123:HIGH"
    assert "not a confirmed outbreak" in str(event["facebook_message"])
    assert "Hindi ito kumpirmadong outbreak" in str(event["facebook_message"])
    assert event["reported_case_total"] == 3
    print("Make alert dispatcher self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send deduplicated ORACLIS risk-onset events to Make.com.")
    parser.add_argument("--run-dir")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    eligible, sent = dispatch(args.run_dir, args.dry_run)
    print(f"Eligible events: {eligible}; sent: {sent}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Alert dispatch failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
