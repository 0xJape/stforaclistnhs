"""Aggregate barangay reporting store."""
from __future__ import annotations

import sqlite3
import hashlib
import hmac
import json
import secrets
import re
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def month_start(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("reporting_period must be YYYY-MM-DD") from exc
    return parsed.replace(day=1).isoformat()


class ReportingStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS aggregate_reports (
                    id INTEGER PRIMARY KEY,
                    reporting_period TEXT NOT NULL,
                    psgc TEXT NOT NULL CHECK(length(psgc)=10 AND psgc GLOB '[0-9]*'),
                    suspected_cases INTEGER NOT NULL DEFAULT 0 CHECK(suspected_cases>=0),
                    probable_cases INTEGER NOT NULL DEFAULT 0 CHECK(probable_cases>=0),
                    confirmed_cases INTEGER NOT NULL DEFAULT 0 CHECK(confirmed_cases>=0),
                    active_cases INTEGER NOT NULL DEFAULT 0 CHECK(active_cases>=0),
                    recovered_cases INTEGER NOT NULL DEFAULT 0 CHECK(recovered_cases>=0),
                    deceased_cases INTEGER NOT NULL DEFAULT 0 CHECK(deceased_cases>=0),
                    exposure REAL NOT NULL DEFAULT 1 CHECK(exposure>0),
                    status TEXT NOT NULL DEFAULT 'approved' CHECK(status IN ('pending','approved','rejected','withdrawn')),
                    reviewer_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(reporting_period, psgc)
                );
                CREATE TABLE IF NOT EXISTS demo_patients (
                    id INTEGER PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    age_band TEXT NOT NULL,
                    sex TEXT NOT NULL CHECK(sex IN ('female','male','other','unspecified')),
                    psgc TEXT NOT NULL CHECK(length(psgc)=10 AND psgc GLOB '[0-9]*'),
                    onset_date TEXT NOT NULL,
                    reported_date TEXT NOT NULL,
                    classification TEXT NOT NULL CHECK(classification IN ('suspected','probable','confirmed','discarded')),
                    case_status TEXT NOT NULL CHECK(case_status IN ('active','recovered','deceased')),
                    archived_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS reporting_audit_log (
                    id INTEGER PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    entity_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS reporting_users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('barangay_encoder','reviewer','administrator')),
                    psgc TEXT NOT NULL,
                    barangay_name TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS reporting_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES reporting_users(id),
                    expires_at TEXT NOT NULL
                );
            """)
            row = db.execute("SELECT id FROM reporting_users WHERE username IN ('barangay-demo','barangayname@oraclis.com')").fetchone()
            if row:
                db.execute("DELETE FROM reporting_sessions WHERE user_id=?", (row["id"],))
                db.execute("DELETE FROM reporting_users WHERE id=?", (row["id"],))
            db.execute("UPDATE aggregate_reports SET status='approved', updated_at=? WHERE status='pending'", (now(),))
            self._provision_barangay_users(db)

    @staticmethod
    def _username(barangay: str, municipality: str) -> str:
        slug = lambda value: re.sub(r"[^a-z0-9]+", ".", value.lower()).strip(".")
        return f"{slug(barangay)}.{slug(municipality)}@oraclis.com"

    def _provision_barangay_users(self, db: sqlite3.Connection) -> None:
        map_path = Path(__file__).resolve().parents[3] / "frontend" / "public" / "maps" / "south_cotabato_barangays_2023.geojson"
        features = json.loads(map_path.read_text(encoding="utf-8")).get("features", [])
        salt = secrets.token_hex(16)
        password_hash = self._password_hash("denguewatch", salt)
        for feature in features:
            props = feature.get("properties", {})
            psgc = str(props.get("ORACLIS_PSGC", props.get("adm4_psgc", "")))
            barangay = str(props.get("ORACLIS_BARANGAY", props.get("adm4_en", ""))).strip()
            municipality = str(props.get("ORACLIS_LOCALITY", props.get("municipality", ""))).strip()
            if len(psgc) != 10 or not psgc.isdigit() or not barangay or not municipality:
                continue
            db.execute("""INSERT INTO reporting_users(username,password_salt,password_hash,role,psgc,barangay_name)
                VALUES (?,?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET psgc=excluded.psgc, barangay_name=excluded.barangay_name, active=1""",
                (self._username(barangay, municipality), salt, password_hash, "barangay_encoder", psgc, barangay))

    @staticmethod
    def _password_hash(password: str, salt: str) -> str:
        return hashlib.scrypt(password.encode(), salt=salt.encode(), n=2**14, r=8, p=1).hex()

    @staticmethod
    def _public_user(row: sqlite3.Row) -> dict[str, Any]:
        return {"username": row["username"], "role": row["role"], "psgc": row["psgc"], "barangay_name": row["barangay_name"]}

    def login(self, username: str, password: str) -> tuple[str, dict[str, Any]]:
        with self.connection() as db:
            row = db.execute("SELECT * FROM reporting_users WHERE username=? AND active=1", (username.strip(),)).fetchone()
            if row is None or not hmac.compare_digest(self._password_hash(password, row["password_salt"]), row["password_hash"]):
                raise PermissionError("Invalid username or password")
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO reporting_sessions(token_hash,user_id,expires_at) VALUES (?,?,datetime('now','+8 hours'))", (hashlib.sha256(token.encode()).hexdigest(), row["id"]))
            return token, self._public_user(row)

    def session_user(self, token: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute("""SELECT u.* FROM reporting_sessions s JOIN reporting_users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>datetime('now') AND u.active=1""", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return self._public_user(row) if row else None

    def logout(self, token: str) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM reporting_sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    @staticmethod
    def _report_values(payload: dict[str, Any]) -> tuple[Any, ...]:
        psgc = str(payload.get("psgc", "")).strip()
        if len(psgc) != 10 or not psgc.isdigit():
            raise ValueError("psgc must be a 10-digit code")
        period = month_start(str(payload.get("reporting_period", "")))
        counts: list[int] = []
        for key in ("suspected_cases", "probable_cases", "confirmed_cases", "active_cases", "recovered_cases", "deceased_cases"):
            value = payload.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{key} must be a non-negative whole number")
            counts.append(value)
        exposure = payload.get("exposure", 1)
        if isinstance(exposure, bool) or not isinstance(exposure, (int, float)) or exposure <= 0:
            raise ValueError("exposure must be a positive number")
        return (period, psgc, *counts, float(exposure))

    def list_reports(self, psgc: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT *, suspected_cases + probable_cases + confirmed_cases AS total_reported_cases FROM aggregate_reports"
        params: tuple[Any, ...] = ()
        if psgc:
            sql += " WHERE psgc=?"
            params = (psgc,)
        sql += " ORDER BY reporting_period DESC, psgc"
        with self.connection() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def create_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = self._report_values(payload)
        stamp = now()
        with self.connection() as db:
            try:
                cursor = db.execute("""INSERT INTO aggregate_reports
                    (reporting_period,psgc,suspected_cases,probable_cases,confirmed_cases,active_cases,recovered_cases,deceased_cases,exposure,status,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (*values, 'approved', stamp, stamp))
            except sqlite3.IntegrityError as exc:
                raise ValueError("A report already exists for this barangay and month") from exc
            report_id = int(cursor.lastrowid)
            db.execute("INSERT INTO reporting_audit_log(entity_type,entity_id,action,occurred_at) VALUES ('aggregate_report',?,'created',?)", (report_id, stamp))
            return dict(db.execute("SELECT *, suspected_cases + probable_cases + confirmed_cases AS total_reported_cases FROM aggregate_reports WHERE id=?", (report_id,)).fetchone())

    def decide_report(self, report_id: int, status: str, note: str = "") -> dict[str, Any]:
        if status not in {"approved", "rejected", "withdrawn"}:
            raise ValueError("invalid report status")
        stamp = now()
        with self.connection() as db:
            cursor = db.execute("UPDATE aggregate_reports SET status=?, reviewer_note=?, updated_at=?, version=version+1 WHERE id=? AND status='pending'", (status, note[:500], stamp, report_id))
            if cursor.rowcount != 1:
                raise ValueError("Only pending reports can be reviewed")
            db.execute("INSERT INTO reporting_audit_log(entity_type,entity_id,action,occurred_at,detail) VALUES ('aggregate_report',?,?,?,?)", (report_id, status, stamp, note[:500]))
            return dict(db.execute("SELECT *, suspected_cases + probable_cases + confirmed_cases AS total_reported_cases FROM aggregate_reports WHERE id=?", (report_id,)).fetchone())

    def situation(self, psgc: str) -> dict[str, Any]:
        with self.connection() as db:
            current = db.execute("""SELECT reporting_period, suspected_cases, probable_cases, confirmed_cases, active_cases, recovered_cases, deceased_cases, exposure, updated_at,
                suspected_cases + probable_cases + confirmed_cases AS total_cases
                FROM aggregate_reports WHERE psgc=? AND status='approved' ORDER BY reporting_period DESC LIMIT 1""", (psgc,)).fetchone()
            pending = db.execute("SELECT count(*) FROM aggregate_reports WHERE psgc=? AND status='pending'", (psgc,)).fetchone()[0]
        return {"psgc": psgc, "approved": dict(current) if current else None, "pending_reports": pending, "data_note": "Approved aggregate reports only. Demo patient records are excluded."}

    def observed_snapshot(self) -> list[dict[str, Any]]:
        """Latest approved monthly total per barangay for map-only aggregate display."""
        with self.connection() as db:
            rows = db.execute("""SELECT r.psgc, r.reporting_period,
                r.suspected_cases + r.probable_cases + r.confirmed_cases AS total_cases, r.updated_at
                FROM aggregate_reports r
                INNER JOIN (SELECT psgc, max(reporting_period) AS reporting_period
                    FROM aggregate_reports WHERE status='approved' GROUP BY psgc) latest
                ON latest.psgc=r.psgc AND latest.reporting_period=r.reporting_period
                WHERE r.status='approved' ORDER BY r.psgc""").fetchall()
        return [dict(row) for row in rows]

    def list_demo_patients(self, psgc: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM demo_patients WHERE archived_at IS NULL", []
        if psgc:
            sql += " AND psgc=?"
            params.append(psgc)
        sql += " ORDER BY reported_date DESC, id DESC"
        with self.connection() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def create_demo_patient(self, payload: dict[str, Any]) -> dict[str, Any]:
        display_name = str(payload.get("display_name", "")).strip()[:80]
        age_band = str(payload.get("age_band", "")).strip()[:30]
        psgc = str(payload.get("psgc", "")).strip()
        sex = str(payload.get("sex", "unspecified"))
        classification = str(payload.get("classification", "suspected"))
        case_status = str(payload.get("case_status", "active"))
        onset_date, reported_date = str(payload.get("onset_date", "")), str(payload.get("reported_date", ""))
        if not display_name or not age_band or len(psgc) != 10 or not psgc.isdigit():
            raise ValueError("display_name, age_band, and a 10-digit psgc are required")
        for value in (onset_date, reported_date):
            date.fromisoformat(value)
        if onset_date > reported_date:
            raise ValueError("onset_date cannot be after reported_date")
        stamp = now()
        with self.connection() as db:
            cursor = db.execute("""INSERT INTO demo_patients
                (display_name,age_band,sex,psgc,onset_date,reported_date,classification,case_status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (display_name, age_band, sex, psgc, onset_date, reported_date, classification, case_status, stamp, stamp))
            item_id = int(cursor.lastrowid)
            db.execute("INSERT INTO reporting_audit_log(entity_type,entity_id,action,occurred_at) VALUES ('demo_patient',?,'created',?)", (item_id, stamp))
            return dict(db.execute("SELECT * FROM demo_patients WHERE id=?", (item_id,)).fetchone())

    def reset_demo_patients(self, psgc: str) -> int:
        with self.connection() as db:
            count = db.execute("SELECT count(*) FROM demo_patients WHERE psgc=?", (psgc,)).fetchone()[0]
            db.execute("DELETE FROM demo_patients WHERE psgc=?", (psgc,))
            db.execute("INSERT INTO reporting_audit_log(entity_type,entity_id,action,occurred_at,detail) VALUES ('demo_registry',0,'reset',?,?)", (now(), f"{psgc}:{count}"))
            return int(count)
