from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sqlite3
import statistics
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, unquote, urlparse
from urllib.request import urlopen

from groq_audio import synthesize, transcribe
from oraclis_agent import ask as ask_agent
from reporting_store import ReportingStore
from weather_facebook import publish_weather_warning

ROOT = Path(__file__).resolve().parents[1]
API_VERSION = "1.2.0-clean-transfer"


def parse_args() -> argparse.Namespace:
    def load_dotenv(path: Path = ROOT / ".env") -> None:
        """Load local backend configuration without overriding process environment."""
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)

    load_dotenv()
    parser = argparse.ArgumentParser(description="Serve the latest ORACLIS spatio-temporal result.")
    parser.add_argument("--host", default=os.getenv("ORACLIS_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ORACLIS_API_PORT", "8765")))
    parser.add_argument("--run-dir", default=os.getenv("ORACLIS_RUN_DIR") or None)
    parser.add_argument("--cors-origin", default=os.getenv("ORACLIS_CORS_ORIGIN", "*"))
    return parser.parse_args()


def resolve_run(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    pointer = ROOT / "outputs" / "latest_spatiotemporal_run.txt"
    if pointer.exists():
        raw = pointer.read_text(encoding="utf-8", errors="ignore").strip().strip('"')
        if raw:
            candidates.append(Path(raw))
            candidates.append(ROOT / "outputs" / Path(raw.replace("\\", "/")).name)
    candidates.extend(sorted((ROOT / "outputs").glob("spatiotemporal_run_*"), reverse=True))
    for candidate in candidates:
        if (
            (candidate / "SPATIOTEMPORAL_BAYESIAN_SUCCESS.txt").exists()
            and (candidate / "database" / "oraclis_spatiotemporal.sqlite").exists()
        ):
            return candidate.resolve()
    raise FileNotFoundError(
        "No completed spatio-temporal run was found. Run "
        "RUN_SYSTEM.bat first, or pass --run-dir."
    )


def rows_to_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    columns = [description[0] for description in cursor.description or []]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def first(query: dict[str, list[str]], key: str, default: str = "") -> str:
    return query.get(key, [default])[0]


def parse_limit(raw: str, default: int = 200, maximum: int = 5000) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))

def municipality_weather(run_dir: Path) -> list[dict[str, Any]]:
    """Read live Open-Meteo current conditions for municipality map points."""
    path = run_dir / "maps" / "south_cotabato_municipality_boundaries.geojson"
    features = json.loads(path.read_text(encoding="utf-8")).get("features", [])
    locations: list[tuple[str, str, float, float]] = []
    for feature in features:
        properties = feature.get("properties", {})
        points: list[list[float]] = []
        def collect(value: Any) -> None:
            if isinstance(value, list) and len(value) >= 2 and isinstance(value[0], (int, float)):
                points.append(value)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
        collect(feature.get("geometry", {}).get("coordinates", []))
        if points:
            lons, lats = zip(*((float(point[0]), float(point[1])) for point in points))
            locations.append((str(properties["municipality_code"]), str(properties["municipality"]), (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2))
    if not locations:
        raise ValueError("No municipality geometry available for weather feed")
    query = urlencode({"latitude": ",".join(f"{lat:.6f}" for _, _, lat, _ in locations), "longitude": ",".join(f"{lon:.6f}" for _, _, _, lon in locations), "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m", "timezone": "Asia/Manila"})
    with urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    payloads = payload if isinstance(payload, list) else [payload]
    if len(payloads) != len(locations):
        raise ValueError("Open-Meteo returned incomplete municipality conditions")
    return [{"MUNICIPALITY_CODE": code, "MUNICIPALITY": name, "LATITUDE": lat, "LONGITUDE": lon, "OBSERVED_AT": item.get("current", {}).get("time"), "TEMPERATURE_C": item.get("current", {}).get("temperature_2m"), "HUMIDITY_PCT": item.get("current", {}).get("relative_humidity_2m"), "PRECIPITATION_MM": item.get("current", {}).get("precipitation"), "WIND_SPEED_KMH": item.get("current", {}).get("wind_speed_10m"), "SOURCE": "Open-Meteo"} for (code, name, lat, lon), item in zip(locations, payloads)]

def municipality_weather_forecast(run_dir: Path, days: int = 16) -> list[dict[str, Any]]:
    live = municipality_weather(run_dir)
    result: list[dict[str, Any]] = []
    for municipality in live:
        query = urlencode({"latitude": f"{municipality['LATITUDE']:.6f}", "longitude": f"{municipality['LONGITUDE']:.6f}", "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max", "forecast_days": days, "timezone": "Asia/Manila"})
        with urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=20) as response:
            daily = json.loads(response.read().decode("utf-8")).get("daily", {})
        for date_value, low, high, rain, probability in zip(daily.get("time", []), daily.get("temperature_2m_min", []), daily.get("temperature_2m_max", []), daily.get("precipitation_sum", []), daily.get("precipitation_probability_max", [])):
            result.append({"DATE": date_value, "MUNICIPALITY_CODE": municipality["MUNICIPALITY_CODE"], "MUNICIPALITY": municipality["MUNICIPALITY"], "TEMPERATURE_MIN_C": low, "TEMPERATURE_MAX_C": high, "PRECIPITATION_MM": rain, "RAIN_PROBABILITY_PCT": probability, "SOURCE": "Open-Meteo"})
    return result

def short_term_weather_scenario(run_dir: Path, database_path: Path, forecast_date: str, days: int = 16) -> list[dict[str, Any]]:
    """Experimental municipal weather-context modifier; never a dengue probability."""
    historical_path = ROOT / "data" / "ORACLIS_Monthly_Barangay_Data_Corrected.csv"
    if not historical_path.exists():
        raise FileNotFoundError("Historical weather dataset is unavailable")
    import csv

    grouped: dict[tuple[str, str], list[float]] = {}
    with historical_path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            key = (row["MUNICIPALITY"].strip(), row["DATE"])
            grouped.setdefault(key, []).append(float(row["DENGUE_CASES_BARANGAY_EST"] or 0))
    history: dict[str, list[tuple[float, float, float]]] = {}
    weather_by_month: dict[tuple[str, str], tuple[float, float]] = {}
    with historical_path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            key = (row["MUNICIPALITY"].strip(), row["DATE"])
            if key not in weather_by_month:
                weather_by_month[key] = (float(row["TEMPERATURE_C_EST"]), float(row["RAINFALL_MM_EST"]))
    for key, cases in grouped.items():
        temperature, rainfall = weather_by_month[key]
        history.setdefault(key[0], []).append((temperature, rainfall, sum(cases)))

    weather = municipality_weather_forecast(run_dir, days)
    weather_window: dict[str, list[dict[str, Any]]] = {}
    for row in weather:
        weather_window.setdefault(row["MUNICIPALITY"], []).append(row)
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        baseline_rows = connection.execute(
            "SELECT MUNICIPALITY,MUNICIPALITY_CODE,POSTERIOR_MEAN_CASES,MEAN_OUTBREAK_PROBABILITY "
            "FROM monthly_municipality_summary WHERE DATE=?", (forecast_date,)
        ).fetchall()
    result: list[dict[str, Any]] = []
    for base in baseline_rows:
        name = str(base["MUNICIPALITY"])
        samples = history.get(name, [])
        days_rows = weather_window.get(name, [])
        if len(samples) < 3 or not days_rows:
            continue
        temperatures, rainfalls, cases = zip(*samples)
        current_temp = statistics.fmean(float(row["TEMPERATURE_MAX_C"] + row["TEMPERATURE_MIN_C"]) / 2 for row in days_rows)
        current_rainfall = statistics.fmean(float(row["PRECIPITATION_MM"] or 0) for row in days_rows) * 30
        def z(value: float, values: tuple[float, ...]) -> float:
            spread = statistics.pstdev(values)
            return 0.0 if spread == 0 else (value - statistics.fmean(values)) / spread
        def correlation(values: tuple[float, ...], targets: tuple[float, ...]) -> float:
            sx, sy = statistics.pstdev(values), statistics.pstdev(targets)
            if sx == 0 or sy == 0:
                return 0.0
            mx, my = statistics.fmean(values), statistics.fmean(targets)
            return sum((x - mx) * (y - my) for x, y in zip(values, targets)) / (len(values) * sx * sy)
        temp_correlation = correlation(temperatures, cases)
        rain_correlation = correlation(rainfalls, cases)
        raw_score = temp_correlation * z(current_temp, temperatures) + rain_correlation * z(current_rainfall, rainfalls)
        modifier = max(0.85, min(1.15, 1 + 0.10 * raw_score))
        result.append({
            "MUNICIPALITY": name,
            "MUNICIPALITY_CODE": base["MUNICIPALITY_CODE"],
            "WINDOW_DAYS": len(days_rows),
            "FORECAST_TEMPERATURE_C": round(current_temp, 1),
            "FORECAST_RAINFALL_30D_EQUIVALENT_MM": round(current_rainfall, 1),
            "TEMPERATURE_CASE_CORRELATION": round(temp_correlation, 3),
            "RAINFALL_CASE_CORRELATION": round(rain_correlation, 3),
            "WEATHER_CONTEXT_MODIFIER": round(modifier, 3),
            "BASELINE_PROJECTED_CASES": base["POSTERIOR_MEAN_CASES"],
            "WEATHER_CONTEXT_CASES": round(float(base["POSTERIOR_MEAN_CASES"]) * modifier, 1),
            "BASELINE_OUTBREAK_PROBABILITY": base["MEAN_OUTBREAK_PROBABILITY"],
        })
    return result


class Handler(BaseHTTPRequestHandler):
    run_dir: Path
    database_path: Path
    reporting_store: ReportingStore
    cors_origin: str = "*"

    def common_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", self.cors_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Cache-Control", "no-store")

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.common_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.common_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.common_headers()
        self.end_headers()
        self.wfile.write(body)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def table_columns(self, table: str) -> list[dict[str, Any]]:
        allowed = {
            "barangays", "municipalities", "adjacency_edges", "forecasts", "alerts",
            "monthly_province_summary", "monthly_municipality_summary",
            "annual_municipality_summary", "red_cluster_events", "metadata",
        }
        if table not in allowed:
            raise ValueError("Unknown or disallowed table")
        return self.query(f"PRAGMA table_info({table})")

    def session_user(self) -> dict[str, Any] | None:
        cookie = self.headers.get("Cookie", "")
        token = next((part.split("=", 1)[1] for part in cookie.split(";") if part.strip().startswith("oraclis_session=")), "")
        return self.reporting_store.session_user(token) if token else None

    def require_barangay_user(self) -> dict[str, Any]:
        user = self.session_user()
        if user is None:
            raise PermissionError("Login required")
        return user

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.common_headers()
        self.end_headers()

    def do_POST(self) -> None:
        try:
            path = unquote(urlparse(self.path).path)
            reporting_paths = {"/api/reports", "/api/demo-patients", "/api/demo-patients/reset", "/api/auth/login", "/api/auth/logout"}
            if path not in {"/api/agent/ask", "/api/agent/transcribe", "/api/agent/speech", "/api/weather/facebook-post", *reporting_paths} and not path.startswith("/api/reports/"):
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
                return
            length = int(self.headers.get("Content-Length", "0"))
            if path == "/api/agent/transcribe":
                if length <= 0 or length > 10 * 1024 * 1024:
                    self.send_json({"error": "Recording must be between 1 byte and 10 MB."}, 413)
                    return
                content_type = self.headers.get_content_type()
                if content_type not in {"audio/webm", "audio/wav", "audio/x-wav", "audio/ogg", "audio/mp4"}:
                    self.send_json({"error": "Unsupported audio format."}, 415)
                    return
                self.send_json({"text": transcribe(self.rfile.read(length), content_type)})
                return
            if self.headers.get_content_type() != "application/json":
                self.send_json({"error": "Content-Type must be application/json"}, 415)
                return
            if length <= 0 or length > 5 * 1024 * 1024:
                self.send_json({"error": "Request body must be between 1 byte and 5 MB"}, 413)
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            if path == "/api/auth/login":
                token, user = self.reporting_store.login(str(payload.get("username", "")), str(payload.get("password", "")))
                body = json_bytes({"data": user})
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Set-Cookie", f"oraclis_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800")
                self.common_headers()
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/auth/logout":
                cookie = self.headers.get("Cookie", "")
                token = next((part.split("=", 1)[1] for part in cookie.split(";") if part.strip().startswith("oraclis_session=")), "")
                if token:
                    self.reporting_store.logout(token)
                self.send_response(204)
                self.send_header("Set-Cookie", "oraclis_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
                self.common_headers()
                self.end_headers()
                return
            if path == "/api/reports":
                user = self.require_barangay_user()
                payload["psgc"] = user["psgc"]
                self.send_json({"data": self.reporting_store.create_report(payload), "requestId": self.headers.get("X-Request-ID", "")}, 201)
                return
            if path.startswith("/api/reports/") and path.endswith("/decision"):
                user = self.require_barangay_user()
                if user["role"] not in {"reviewer", "administrator"}:
                    raise PermissionError("Reviewer access required")
                report_id = int(path.split("/")[3])
                self.send_json({"data": self.reporting_store.decide_report(report_id, str(payload.get("status", "")), str(payload.get("reviewer_note", "")))})
                return
            if path == "/api/demo-patients":
                user = self.require_barangay_user()
                payload["psgc"] = user["psgc"]
                self.send_json({"data": self.reporting_store.create_demo_patient(payload), "demo": True}, 201)
                return
            if path == "/api/demo-patients/reset":
                user = self.require_barangay_user()
                self.send_json({"data": {"deleted": self.reporting_store.reset_demo_patients(user["psgc"])}, "demo": True})
                return
            if path == "/api/weather/facebook-post":
                municipality = payload.get("municipality", "")
                if not isinstance(municipality, str) or not municipality.strip():
                    raise ValueError("municipality is required")
                map_image = payload.get("map_image", "")
                if not isinstance(map_image, str):
                    raise ValueError("map_image must be text")
                with sqlite3.connect(self.database_path) as connection:
                    municipality_codes = connection.execute(
                        "SELECT DISTINCT MUNICIPALITY,MUNICIPALITY_CODE FROM barangays"
                    ).fetchall()
                selected = municipality.strip().casefold()
                code = next((str(code) for name, code in municipality_codes if str(name).strip().casefold() == selected), "")
                if len(code) != 10 or not code.endswith("000"):
                    raise ValueError("Municipality reporting boundary is unavailable.")
                prefix = code[:-3]
                reported_cases = sum(int(row.get("total_cases") or 0) for row in self.reporting_store.observed_snapshot() if str(row.get("psgc", "")).startswith(prefix))
                self.send_json(publish_weather_warning(municipality.strip(), lambda days: municipality_weather_forecast(self.run_dir, days), map_image, reported_cases))
                return
            if path == "/api/agent/speech":
                text = payload.get("text", "")
                if not isinstance(text, str):
                    raise ValueError("text must be text")
                self.send_bytes(synthesize(text), "audio/wav")
                return
            message = payload.get("message", "")
            context = payload.get("context", {})
            if not isinstance(message, str) or not isinstance(context, dict):
                raise ValueError("message must be text and context must be an object")
            self.send_json(ask_agent(str(self.database_path), message, context, lambda forecast: municipality_weather_forecast(self.run_dir) if forecast else municipality_weather(self.run_dir), self.reporting_store.observed_snapshot))
        except PermissionError as exc:
            self.send_json({"error": "unauthorized", "message": str(exc)}, 401)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": "invalid_request", "message": str(exc)}, 400)
        except RuntimeError as exc:
            self.send_json({"error": "external_provider_error", "message": str(exc)}, 502)
        except Exception:
            self.send_json({"error": "agent_unavailable", "message": "Assistant could not process this request."}, 500)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path in {"/", "/map"}:
                self.send_file(self.run_dir / "maps" / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html")
                return

            if path == "/api/health":
                self.send_json({
                    "status": "ok",
                    "api_version": API_VERSION,
                    "run_dir": str(self.run_dir),
                    "database": str(self.database_path),
                })
                return

            if path == "/api/version":
                self.send_json({"api_version": API_VERSION, "project": "ORACLIS South Cotabato"})
                return

            if path == "/api/auth/session":
                user = self.session_user()
                self.send_json({"data": user}, 200 if user else 401)
                return

            if path == "/api/reports":
                user = self.require_barangay_user()
                items = self.reporting_store.list_reports(user["psgc"])
                self.send_json({"data": items, "count": len(items)})
                return

            if path == "/api/situation":
                user = self.require_barangay_user()
                self.send_json({"data": self.reporting_store.situation(user["psgc"])})
                return

            if path == "/api/observed-snapshot":
                items = self.reporting_store.observed_snapshot()
                self.send_json({"data": items, "count": len(items), "note": "Latest published aggregate reports."})
                return

            if path == "/api/demo-patients":
                user = self.require_barangay_user()
                items = self.reporting_store.list_demo_patients(user["psgc"])
                self.send_json({"data": items, "count": len(items), "demo": True, "notice": "DEMO DATA — NOT REAL PATIENTS. Excluded from map, forecasts, and alerts."})
                return

            if path == "/api/metadata":
                metadata = self.query("SELECT KEY, VALUE FROM metadata ORDER BY KEY")
                decoded: dict[str, Any] = {}
                for row in metadata:
                    try:
                        decoded[row["KEY"]] = json.loads(row["VALUE"])
                    except Exception:
                        decoded[row["KEY"]] = row["VALUE"]
                self.send_json(decoded)
                return

            if path == "/api/schema":
                table = first(query, "table", "forecasts")
                self.send_json({"table": table, "columns": self.table_columns(table)})
                return

            if path == "/api/dates":
                rows = self.query("SELECT DISTINCT DATE FROM forecasts ORDER BY DATE")
                dates = [row["DATE"] for row in rows]
                self.send_json({"count": len(dates), "items": dates})
                return

            if path == "/api/barangays":
                municipality_code = first(query, "municipality_code")
                sql = (
                    "SELECT PSGC,PROVINCE,PROVINCE_CODE,MUNICIPALITY,MUNICIPALITY_CODE,"
                    "BARANGAY,CALIBRATION_STATUS,AREA_KM2,CENTROID_LON,CENTROID_LAT "
                    "FROM barangays WHERE 1=1"
                )
                params: list[Any] = []
                if municipality_code:
                    sql += " AND MUNICIPALITY_CODE=?"
                    params.append(municipality_code)
                sql += " ORDER BY MUNICIPALITY,BARANGAY"
                rows = self.query(sql, tuple(params))
                self.send_json({"count": len(rows), "items": rows})
                return

            if path == "/api/municipalities":
                rows = self.query("SELECT * FROM municipalities ORDER BY MUNICIPALITY")
                self.send_json({"count": len(rows), "items": rows})
                return

            if path == "/api/weather/live":
                rows = municipality_weather(self.run_dir)
                self.send_json({"count": len(rows), "items": rows, "model_note": "Live weather context. It does not modify dengue scenario probabilities until weather-effect backtesting is validated."})
                return

            if path == "/api/weather/forecast":
                days = parse_limit(first(query, "days", "16"), default=16, maximum=16)
                rows = municipality_weather_forecast(self.run_dir, days)
                self.send_json({"days": days, "count": len(rows), "items": rows, "model_note": "Weather forecast context. Dengue scenario probabilities remain model outputs."})
                return

            if path == "/api/weather/short-term-scenario":
                date = first(query, "date")
                if not date:
                    self.send_json({"error": "date is required"}, 400)
                    return
                days = parse_limit(first(query, "days", "16"), default=16, maximum=16)
                rows = short_term_weather_scenario(self.run_dir, self.database_path, date, days)
                self.send_json({"date": date, "days": days, "count": len(rows), "items": rows, "model_note": "Experimental municipality weather-context scenario. Historical cases and weather are synthetic/interpolated. This is not an outbreak probability, alert, or surveillance forecast."})
                return

            if path == "/api/forecast":
                date = first(query, "date")
                psgc = first(query, "psgc")
                if not date or not psgc:
                    self.send_json({"error": "date and psgc are required"}, 400)
                    return
                rows = self.query("SELECT * FROM forecasts WHERE DATE=? AND PSGC=? LIMIT 1", (date, psgc))
                self.send_json(rows[0] if rows else {"error": "forecast not found"}, 200 if rows else 404)
                return

            if path in {"/api/parameters", "/api/barangay-details"}:
                date = first(query, "date")
                psgc = first(query, "psgc")
                if not date or not psgc:
                    self.send_json({"error": "date and psgc are required"}, 400)
                    return
                rows = self.query("SELECT * FROM forecasts WHERE DATE=? AND PSGC=? LIMIT 1", (date, psgc))
                self.send_json(rows[0] if rows else {"error": "record not found"}, 200 if rows else 404)
                return

            if path == "/api/snapshot":
                date = first(query, "date")
                if not date:
                    self.send_json({"error": "date is required"}, 400)
                    return
                rows = self.query("SELECT * FROM forecasts WHERE DATE=? ORDER BY PSGC", (date,))
                self.send_json({"date": date, "count": len(rows), "items": rows})
                return

            if path == "/api/ranking":
                date = first(query, "date")
                metric = first(query, "metric", "POSTERIOR_MEAN_CASES").upper()
                limit = parse_limit(first(query, "limit", "199"), default=199, maximum=199)
                allowed_metrics = {
                    "POSTERIOR_MEAN_CASES", "OUTBREAK_PROBABILITY", "HOTSPOT_Z_SCORE",
                    "UPPER_CREDIBLE_CASES", "RED_CLUSTER_SIZE",
                }
                if not date:
                    self.send_json({"error": "date is required"}, 400)
                    return
                if metric not in allowed_metrics:
                    self.send_json({"error": "unsupported metric", "allowed": sorted(allowed_metrics)}, 400)
                    return
                rows = self.query(
                    f"SELECT DATE,PSGC,MUNICIPALITY,MUNICIPALITY_CODE,BARANGAY,{metric} AS VALUE,"
                    "ALERT_LEVEL,AUTOMATIC_OUTBREAK_ALERT,OUTBREAK_ALERT_REASON,RED_CLUSTER_ID,RED_CLUSTER_SIZE "
                    f"FROM forecasts WHERE DATE=? ORDER BY {metric} DESC LIMIT ?",
                    (date, limit),
                )
                self.send_json({"date": date, "metric": metric, "count": len(rows), "items": rows})
                return

            if path == "/api/province-summary":
                date_from = first(query, "date_from")
                date_to = first(query, "date_to")
                sql = "SELECT * FROM monthly_province_summary WHERE 1=1"
                params: list[Any] = []
                if date_from:
                    sql += " AND DATE>=?"
                    params.append(date_from)
                if date_to:
                    sql += " AND DATE<=?"
                    params.append(date_to)
                sql += " ORDER BY DATE"
                rows = self.query(sql, tuple(params))
                self.send_json({"count": len(rows), "items": rows})
                return

            if path == "/api/municipality-summary":
                date = first(query, "date")
                municipality_code = first(query, "municipality_code")
                sql = "SELECT * FROM monthly_municipality_summary WHERE 1=1"
                params: list[Any] = []
                if date:
                    sql += " AND DATE=?"
                    params.append(date)
                if municipality_code:
                    sql += " AND MUNICIPALITY_CODE=?"
                    params.append(municipality_code)
                sql += " ORDER BY DATE,MUNICIPALITY"
                rows = self.query(sql, tuple(params))
                self.send_json({"count": len(rows), "items": rows})
                return

            if path == "/api/alerts":
                date = first(query, "date")
                level = first(query, "level").upper()
                limit = parse_limit(first(query, "limit", "5000"), default=5000, maximum=5000)
                sql = "SELECT * FROM alerts WHERE 1=1"
                params: list[Any] = []
                if date:
                    sql += " AND DATE=?"
                    params.append(date)
                if level:
                    sql += " AND ALERT_LEVEL=?"
                    params.append(level)
                sql += " ORDER BY OUTBREAK_PROBABILITY DESC LIMIT ?"
                params.append(limit)
                rows = self.query(sql, tuple(params))
                self.send_json({"count": len(rows), "items": rows})
                return

            if path == "/api/clusters":
                date = first(query, "date")
                if not date:
                    self.send_json({"error": "date is required"}, 400)
                    return
                rows = self.query(
                    "SELECT DATE,RED_CLUSTER_ID,MAX(RED_CLUSTER_SIZE) AS CLUSTER_SIZE,"
                    "GROUP_CONCAT(BARANGAY || ' (' || MUNICIPALITY || ')', '; ') AS MEMBERS,"
                    "MAX(CASE WHEN RED_CLUSTER_LIKELY_ORIGIN=1 THEN BARANGAY || ' (' || MUNICIPALITY || ')' ELSE NULL END) AS LIKELY_ORIGIN,"
                    "SUM(POSTERIOR_MEAN_CASES) AS PROJECTED_CASES,MAX(OUTBREAK_PROBABILITY) AS MAX_OUTBREAK_PROBABILITY,"
                    "MAX(RED_CLUSTER_OUTBREAK_ONSET) AS NEW_CLUSTER_ONSET "
                    "FROM forecasts WHERE DATE=? AND RED_CLUSTER_ID>0 "
                    "GROUP BY DATE,RED_CLUSTER_ID ORDER BY RED_CLUSTER_ID",
                    (date,),
                )
                self.send_json({"date": date, "count": len(rows), "items": rows})
                return

            if path == "/api/timeline":
                psgc = first(query, "psgc")
                if not psgc:
                    self.send_json({"error": "psgc is required"}, 400)
                    return
                rows = self.query("SELECT * FROM forecasts WHERE PSGC=? ORDER BY DATE", (psgc,))
                self.send_json({"psgc": psgc, "count": len(rows), "items": rows})
                return

            if path == "/api/geometry/barangays":
                self.send_file(ROOT / "data" / "cache" / "south_cotabato_barangays_2023.geojson")
                return
            if path == "/api/geometry/municipalities":
                self.send_file(self.run_dir / "maps" / "south_cotabato_municipality_boundaries.geojson")
                return
            if path == "/api/geometry/province":
                candidates = [
                    self.run_dir / "maps" / "south_cotabato_province_boundary.geojson",
                    self.run_dir / "maps" / "south_cotabato_province.geojson",
                ]
                match = next((candidate for candidate in candidates if candidate.exists()), None)
                if match is None:
                    self.send_json({"error": "province geometry export not found"}, 404)
                else:
                    self.send_file(match)
                return

            if path.startswith("/files/"):
                relative = Path(path[len("/files/"):])
                candidate = (self.run_dir / relative).resolve()
                if candidate != self.run_dir and self.run_dir not in candidate.parents:
                    self.send_error(HTTPStatus.FORBIDDEN, "Invalid path")
                    return
                self.send_file(candidate)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        except PermissionError as exc:
            self.send_json({"error": "unauthorized", "message": str(exc)}, 401)
        except ValueError as exc:
            self.send_json({"error": "ValueError", "message": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": type(exc).__name__, "message": str(exc)}, 500)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[ORACLIS API] {self.address_string()} - {format % args}")


def main() -> int:
    args = parse_args()
    run_dir = resolve_run(args.run_dir)
    Handler.run_dir = run_dir
    Handler.database_path = run_dir / "database" / "oraclis_spatiotemporal.sqlite"
    Handler.reporting_store = ReportingStore(ROOT / "data" / "oraclis_reporting_demo.sqlite")
    Handler.cors_origin = args.cors_origin
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ORACLIS development API v{API_VERSION}: http://{args.host}:{args.port}")
    print(f"Interactive map: http://{args.host}:{args.port}/map")
    print(f"CORS origin: {args.cors_origin}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
