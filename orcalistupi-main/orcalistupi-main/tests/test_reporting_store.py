import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from reporting_store import ReportingStore


def test_aggregate_report_publishes_immediately() -> None:
    path = Path(tempfile.gettempdir()) / "oraclis-reporting-store-test.sqlite"
    path.unlink(missing_ok=True)
    store = ReportingStore(path)
    report = store.create_report({
        "psgc": "1206302002", "reporting_period": "2026-07-14",
        "suspected_cases": 2, "probable_cases": 1, "confirmed_cases": 3,
        "active_cases": 4, "recovered_cases": 2, "deceased_cases": 0, "exposure": 1000,
    })
    assert report["status"] == "approved"
    assert store.situation("1206302002")["approved"]["total_cases"] == 6
    path.unlink(missing_ok=True)

def test_login_session_and_logout() -> None:
    path = Path(tempfile.gettempdir()) / "oraclis-reporting-auth-test.sqlite"
    path.unlink(missing_ok=True)
    store = ReportingStore(path)
    token, user = store.login("barangayname@oraclis.com", "denguewatch")
    assert user["psgc"] == "1206302002"
    assert store.session_user(token) == user
    store.logout(token)
    assert store.session_user(token) is None
    path.unlink(missing_ok=True)
