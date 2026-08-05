from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "observed_cases_updates.csv"
REQUIRED = ("DATE", "PSGC", "OBSERVED_CASES")
OUTPUT_COLUMNS = ("DATE", "PSGC", "OBSERVED_CASES", "EXPOSURE")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and merge ORACLIS live observations.")
    parser.add_argument("--input", required=True, help="CSV or JSON file containing observations")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--mode", choices=("upsert", "append", "replace"), default="upsert")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("items", payload.get("data", payload.get("observations", payload)))
        if not isinstance(payload, list):
            raise ValueError("JSON input must be an array or contain an items/data/observations array.")
        return [dict(row) for row in payload]
    raise ValueError("Input must be .csv or .json")


def normalize_month(value: Any) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("DATE is blank")
    candidates = ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d", "%m/%d/%Y")
    parsed = None
    for fmt in candidates:
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Unsupported DATE value: {text}") from exc
    return parsed.strftime("%Y-%m-01")


def normalize_psgc(value: Any) -> str:
    text = str(value).strip().removesuffix(".0")
    if not (text.isdigit() and len(text) == 10):
        raise ValueError(f"PSGC must be exactly 10 digits: {value}")
    if not text.startswith("12063"):
        raise ValueError(f"PSGC is outside South Cotabato: {text}")
    return text


def number(value: Any, field: str, *, default: float | None = None) -> float:
    text = "" if value is None else str(value).strip()
    if text == "" and default is not None:
        result = default
    else:
        try:
            result = float(text)
        except ValueError as exc:
            raise ValueError(f"{field} is not numeric: {value}") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and non-negative: {value}")
    return result


def validate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        raise ValueError("Input contains no observations.")
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw in enumerate(rows, start=2):
        upper = {str(key).strip().upper(): value for key, value in raw.items()}
        missing = [field for field in REQUIRED if str(upper.get(field, "")).strip() == ""]
        if missing:
            errors.append(f"row {index}: missing {', '.join(missing)}")
            continue
        try:
            normalized.append({
                "DATE": normalize_month(upper["DATE"]),
                "PSGC": normalize_psgc(upper["PSGC"]),
                "OBSERVED_CASES": number(upper["OBSERVED_CASES"], "OBSERVED_CASES"),
                "EXPOSURE": number(upper.get("EXPOSURE"), "EXPOSURE", default=1.0),
            })
        except ValueError as exc:
            errors.append(f"row {index}: {exc}")
    if errors:
        raise ValueError("Live observation validation failed:\n- " + "\n- ".join(errors[:50]))

    deduplicated = {(row["DATE"], row["PSGC"]): row for row in normalized}
    return sorted(deduplicated.values(), key=lambda row: (row["DATE"], row["PSGC"]))


def read_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    return validate_rows(rows) if rows else []


def merge(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == "replace":
        return incoming
    if mode == "append":
        combined = existing + incoming
        keys = [(row["DATE"], row["PSGC"]) for row in combined]
        if len(keys) != len(set(keys)):
            raise ValueError("Append would create duplicate DATE+PSGC records. Use --mode upsert.")
        return sorted(combined, key=lambda row: (row["DATE"], row["PSGC"]))
    records = {(row["DATE"], row["PSGC"]): row for row in existing}
    records.update({(row["DATE"], row["PSGC"]): row for row in incoming})
    return sorted(records.values(), key=lambda row: (row["DATE"], row["PSGC"]))


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "DATE": row["DATE"],
                "PSGC": row["PSGC"],
                "OBSERVED_CASES": f"{float(row['OBSERVED_CASES']):.10g}",
                "EXPOSURE": f"{float(row['EXPOSURE']):.10g}",
            })
    temp.replace(path)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if not input_path.exists():
        print(f"ERROR: Input file does not exist: {input_path}", file=sys.stderr)
        return 2
    try:
        incoming = validate_rows(load_rows(input_path))
        existing = [] if args.mode == "replace" else read_existing(output_path)
        merged = merge(existing, incoming, args.mode)
        print(f"Validated incoming rows: {len(incoming)}")
        print(f"Existing rows: {len(existing)}")
        print(f"Result rows: {len(merged)}")
        print(f"Output: {output_path}")
        if args.dry_run:
            print("Dry run only; no file was changed.")
        else:
            write_rows(output_path, merged)
            print("Live observations were written successfully.")
            print("Run RUN_SYSTEM.bat to regenerate forecasts.")
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
