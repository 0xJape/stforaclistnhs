from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from south_cotabato_boundaries import (
    EXPECTED_BARANGAY_FEATURES,
    EXPECTED_LOCALITY_COUNT,
    PROVINCE_CODE,
    candidate_urls,
    load_south_cotabato_localities_2023,
    validate_cached_geojson,
    validate_cache_metadata,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default="data/cache/south_cotabato_barangays_2023.geojson")
    parser.add_argument("--require-cache", action="store_true")
    args = parser.parse_args()

    localities = load_south_cotabato_localities_2023()
    codes = {item.code for item in localities}
    calibrated = [item for item in localities if item.source_data_status == "CALIBRATED"]
    checks = {
        "locality_count": len(localities) == EXPECTED_LOCALITY_COUNT,
        "unique_codes": len(codes) == len(localities),
        "all_codes_south_cotabato": all(code.startswith("12063") and len(code) == 10 for code in codes),
        "province_code": all(item.province_code == PROVINCE_CODE for item in localities),
        "candidate_urls_available": all(len(candidate_urls(item.code)) >= 3 for item in localities),
        "calibrated_locality_count": len(calibrated) == EXPECTED_LOCALITY_COUNT,
    }
    if not all(checks.values()):
        raise RuntimeError("South Cotabato catalogue validation failed: " + json.dumps(checks, indent=2))

    print("SOUTH COTABATO BOUNDARY PREFLIGHT")
    print(f"Province PSGC: {PROVINCE_CODE}")
    print(f"Expected cities/municipalities: {EXPECTED_LOCALITY_COUNT}")
    print(f"Expected barangay polygons: {EXPECTED_BARANGAY_FEATURES}")
    print(f"Calibrated localities: {len(calibrated)}")
    print()
    for item in localities:
        print(f"- {item.name} ({item.code}) | {item.source_data_status}")

    cache = (ROOT / args.cache).resolve()
    if cache.exists():
        try:
            payload = validate_cached_geojson(cache)
            validate_cache_metadata(cache.with_name("south_cotabato_barangays_2023_metadata.json"))
            print(f"\nValidated real boundary cache: {cache}")
            print(f"Barangays: {len(payload['features'])}")
        except Exception as exc:
            if args.require_cache:
                raise
            metadata = cache.with_name("south_cotabato_barangays_2023_metadata.json")
            cache.unlink(missing_ok=True)
            metadata.unlink(missing_ok=True)
            print(f"\nRemoved invalid/stale boundary cache: {exc}")
            print("The simulation will download verified real barangay polygons.")
    elif args.require_cache:
        raise FileNotFoundError(f"Required South Cotabato boundary cache not found: {cache}")
    else:
        print("\nBoundary cache not present yet. The simulation launcher will download it on first run.")
    print("\nPREFLIGHT PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"SOUTH COTABATO BOUNDARY PREFLIGHT FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
