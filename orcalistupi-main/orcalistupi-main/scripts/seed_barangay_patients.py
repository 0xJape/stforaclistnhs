"""Ensure every South Cotabato barangay has three fictional patient records."""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reporting_store import ReportingStore  # noqa: E402

FIRST_NAMES = ("Maria", "Jose", "Angelica", "Mark", "Rosalinda", "Carlo", "Jessa", "Ramon", "Lorna", "Paolo", "Marites", "Jun")
LAST_NAMES = ("Santos", "Dela Cruz", "Reyes", "Mendoza", "Garcia", "Flores", "Villanueva", "Bautista", "Navarro", "Castillo", "Aquino", "Ramos")
SYMPTOMS = (
    "High fever | Severe headache",
    "High fever | Pain behind the eyes | Muscle, joint, or bone pain",
    "High fever | Skin rash | Nausea or vomiting",
)
STATUSES = ("active", "recovered", "active")
CLASSIFICATIONS = ("suspected", "confirmed", "probable")


def main() -> None:
    map_path = ROOT.parents[1] / "frontend" / "public" / "maps" / "south_cotabato_barangays_2023.geojson"
    features = json.loads(map_path.read_text(encoding="utf-8"))["features"]
    store = ReportingStore(ROOT / "data" / "oraclis_reporting_demo.sqlite")
    inserted = 0
    covered = 0
    for barangay_index, feature in enumerate(features):
        props = feature.get("properties", {})
        psgc = str(props.get("ORACLIS_PSGC", props.get("adm4_psgc", "")))
        barangay = str(props.get("ORACLIS_BARANGAY", props.get("adm4_en", ""))).strip()
        municipality = str(props.get("ORACLIS_LOCALITY", props.get("municipality", ""))).strip()
        if len(psgc) != 10 or not psgc.isdigit() or not barangay or not municipality:
            continue
        existing = store.list_demo_patients(psgc)
        for person_index, item in enumerate(existing):
            if item["municipality"] and item["barangay"] and item["contact_number"] and item["symptoms"]:
                continue
            seed = barangay_index * 3 + person_index
            store.update_demo_patient(item["id"], psgc, {
                **item,
                "municipality": item["municipality"] or municipality,
                "barangay": item["barangay"] or barangay,
                "purok": item["purok"] or f"Purok {1 + seed % 7}",
                "contact_number": item["contact_number"] or f"09{100000000 + seed:09d}"[-11:],
                "symptoms": item["symptoms"] or SYMPTOMS[person_index % len(SYMPTOMS)],
            })
        for person_index in range(len(existing), 3):
            seed = barangay_index * 3 + person_index
            reported = date(2026, 9, 1) + timedelta(days=seed % 35)
            onset = reported - timedelta(days=2 + seed % 5)
            store.create_demo_patient({
                "display_name": f"{FIRST_NAMES[seed % len(FIRST_NAMES)]} {LAST_NAMES[(seed * 5 + 3) % len(LAST_NAMES)]}",
                "age_band": str(8 + seed * 7 % 68),
                "sex": "female" if seed % 2 == 0 else "male",
                "psgc": psgc,
                "municipality": municipality,
                "barangay": barangay,
                "purok": f"Purok {1 + seed % 7}",
                "contact_number": f"09{100000000 + seed:09d}"[-11:],
                "symptoms": SYMPTOMS[person_index],
                "onset_date": onset.isoformat(),
                "reported_date": reported.isoformat(),
                "classification": CLASSIFICATIONS[person_index],
                "case_status": STATUSES[person_index],
            })
            inserted += 1
        covered += 1
    print(f"Barangays covered: {covered}; patient records inserted: {inserted}")
    assert covered > 0
    assert all(len(store.list_demo_patients(str((feature.get('properties') or {}).get('ORACLIS_PSGC', (feature.get('properties') or {}).get('adm4_psgc', ''))))) >= 3 for feature in features if len(str((feature.get('properties') or {}).get('ORACLIS_PSGC', (feature.get('properties') or {}).get('adm4_psgc', '')))) == 10)


if __name__ == "__main__":
    main()
