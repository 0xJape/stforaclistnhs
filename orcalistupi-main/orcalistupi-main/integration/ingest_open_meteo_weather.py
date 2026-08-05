from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GEOMETRY = ROOT / "outputs" / "spatiotemporal_run_20260727_143226" / "maps" / "south_cotabato_municipality_boundaries.geojson"
DEFAULT_DAILY = ROOT / "data" / "weather" / "open_meteo_daily.csv"
DEFAULT_MONTHLY = ROOT / "data" / "weather" / "open_meteo_monthly.csv"
DAILY_COLUMNS = ("DATE", "MUNICIPALITY_CODE", "MUNICIPALITY", "LATITUDE", "LONGITUDE", "TEMPERATURE_C", "HUMIDITY_PCT", "PRECIPITATION_MM", "WIND_SPEED_MS", "SOURCE")
MONTHLY_COLUMNS = ("DATE", "MUNICIPALITY_CODE", "MUNICIPALITY", "TEMPERATURE_C_MEAN", "HUMIDITY_PCT_MEAN", "PRECIPITATION_MM_TOTAL", "WIND_SPEED_MS_MEAN", "SOURCE")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and cache municipality weather from Open-Meteo. Does not ingest dengue cases.")
    parser.add_argument("--start-date", required=True, type=parse_date, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, type=parse_date, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--geometry", default=str(DEFAULT_GEOMETRY), help="Municipality GeoJSON from a successful ORACLIS run")
    parser.add_argument("--daily-output", default=str(DEFAULT_DAILY))
    parser.add_argument("--monthly-output", default=str(DEFAULT_MONTHLY))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected YYYY-MM-DD, got {value}") from exc


def coordinates(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    kind = geometry.get("type")
    raw = geometry.get("coordinates")
    if kind == "Polygon":
        rings = raw
    elif kind == "MultiPolygon":
        rings = [ring for polygon in raw for ring in polygon]
    else:
        raise ValueError(f"Unsupported geometry type: {kind}")
    return [(float(point[0]), float(point[1])) for ring in rings for point in ring]


def load_municipalities(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        properties = feature.get("properties", {})
        points = coordinates(feature["geometry"])
        if not points:
            continue
        # ponytail: bounding-box centre; use polygon representative points if microclimate resolution is needed.
        longitude = (min(point[0] for point in points) + max(point[0] for point in points)) / 2
        latitude = (min(point[1] for point in points) + max(point[1] for point in points)) / 2
        rows.append({"code": str(properties["municipality_code"]), "name": str(properties["municipality"]), "latitude": latitude, "longitude": longitude})
    if not rows:
        raise ValueError("No municipality features found in GeoJSON.")
    return sorted(rows, key=lambda row: row["code"])


def api_url(end: date) -> str:
    return "https://archive-api.open-meteo.com/v1/archive" if end < date.today() else "https://api.open-meteo.com/v1/forecast"


def fetch_daily(municipality: dict[str, Any], start: date, end: date) -> list[dict[str, str]]:
    response = requests.get(api_url(end), params={
        "latitude": municipality["latitude"], "longitude": municipality["longitude"],
        "start_date": start.isoformat(), "end_date": end.isoformat(), "timezone": "Asia/Manila",
        "daily": "temperature_2m_mean,relative_humidity_2m_mean,precipitation_sum,wind_speed_10m_max",
    }, timeout=30)
    response.raise_for_status()
    daily = response.json().get("daily", {})
    fields = ("time", "temperature_2m_mean", "relative_humidity_2m_mean", "precipitation_sum", "wind_speed_10m_max")
    values = [daily.get(field) for field in fields]
    if not all(isinstance(value, list) for value in values) or len({len(value) for value in values}) != 1:
        raise ValueError(f"Open-Meteo returned incomplete daily data for {municipality['name']}")
    return [{
        "DATE": day, "MUNICIPALITY_CODE": municipality["code"], "MUNICIPALITY": municipality["name"],
        "LATITUDE": f"{municipality['latitude']:.6f}", "LONGITUDE": f"{municipality['longitude']:.6f}",
        "TEMPERATURE_C": format_number(temp), "HUMIDITY_PCT": format_number(humidity),
        "PRECIPITATION_MM": format_number(rain), "WIND_SPEED_MS": format_number(wind / 3.6 if wind is not None else None),
        "SOURCE": "open-meteo",
    } for day, temp, humidity, rain, wind in zip(*values)]


def format_number(value: Any) -> str:
    return "" if value is None else f"{float(value):.6g}"


def upsert(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]], key: tuple[str, ...]) -> list[dict[str, str]]:
    existing: list[dict[str, str]] = []
    if path.exists() and path.stat().st_size:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            existing = list(csv.DictReader(handle))
    records = {tuple(row[field] for field in key): row for row in existing}
    records.update({tuple(row[field] for field in key): row for row in rows})
    return sorted(records.values(), key=lambda row: tuple(row[field] for field in key))


def monthly_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault((row["DATE"][:7], row["MUNICIPALITY_CODE"]), []).append(row)
    result = []
    for (month, code), items in groups.items():
        mean = lambda field: sum(float(item[field]) for item in items if item[field]) / sum(bool(item[field]) for item in items)
        result.append({"DATE": f"{month}-01", "MUNICIPALITY_CODE": code, "MUNICIPALITY": items[0]["MUNICIPALITY"],
                       "TEMPERATURE_C_MEAN": f"{mean('TEMPERATURE_C'):.6g}", "HUMIDITY_PCT_MEAN": f"{mean('HUMIDITY_PCT'):.6g}",
                       "PRECIPITATION_MM_TOTAL": f"{sum(float(item['PRECIPITATION_MM']) for item in items if item['PRECIPITATION_MM']):.6g}",
                       "WIND_SPEED_MS_MEAN": f"{mean('WIND_SPEED_MS'):.6g}", "SOURCE": "open-meteo"})
    return result


def write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    if args.end_date < args.start_date:
        print("ERROR: --end-date must not be before --start-date", file=sys.stderr)
        return 2
    geometry = Path(args.geometry).expanduser().resolve()
    if not geometry.exists():
        print(f"ERROR: Municipality geometry not found: {geometry}", file=sys.stderr)
        return 2
    try:
        rows = [row for municipality in load_municipalities(geometry) for row in fetch_daily(municipality, args.start_date, args.end_date)]
        daily = upsert(Path(args.daily_output).expanduser().resolve(), DAILY_COLUMNS, rows, ("DATE", "MUNICIPALITY_CODE"))
        monthly = upsert(Path(args.monthly_output).expanduser().resolve(), MONTHLY_COLUMNS, monthly_rows(rows), ("DATE", "MUNICIPALITY_CODE"))
        print(f"Fetched daily rows: {len(rows)}")
        print(f"Daily cache rows: {len(daily)}")
        print(f"Monthly cache rows: {len(monthly)}")
        if args.dry_run:
            print("Dry run only; no files changed.")
        else:
            write_csv(Path(args.daily_output).expanduser().resolve(), DAILY_COLUMNS, daily)
            write_csv(Path(args.monthly_output).expanduser().resolve(), MONTHLY_COLUMNS, monthly)
            print("Weather cache written. No dengue observations or forecasts changed.")
        return 0
    except (OSError, ValueError, requests.RequestException) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())