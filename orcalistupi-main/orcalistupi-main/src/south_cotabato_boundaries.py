from __future__ import annotations

import csv
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests

ROOT = Path(__file__).resolve().parents[1]
LOCALITY_CATALOGUE = ROOT / "data" / "south_cotabato_localities_2023.csv"
REGION_CODE = "1200000000"
PROVINCE_CODE = "1206300000"
EXPECTED_LOCALITY_COUNT = 11
EXPECTED_BARANGAY_FEATURES = 199

BOUNDARY_REPOSITORY = "faeldon/philippines-json-maps"
BOUNDARY_COMMIT = "8eeead560246863c8c820c31ca6fbca81a279477"
BOUNDARY_REFS = (BOUNDARY_COMMIT, "master")
GEOJSON_TEMPLATE = (
    "https://raw.githubusercontent.com/faeldon/philippines-json-maps/{ref}/"
    "2023/geojson/municities/{resolution}/"
    "bgysubmuns-municity-{code}.{suffix}.json"
)


@dataclass(frozen=True)
class Locality:
    code: str
    name: str
    province_code: str
    province_name: str
    locality_type: str
    source_data_status: str


def _digits_code(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if float(value).is_integer():
                return str(int(value))
        except Exception:
            pass
    text = re.sub(r"\.0+$", "", str(value or "").strip())
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 11 and digits.endswith("0"):
        digits = digits[:-1]
    return digits


def load_south_cotabato_localities_2023() -> list[Locality]:
    if not LOCALITY_CATALOGUE.exists():
        raise FileNotFoundError(f"Bundled South Cotabato locality catalogue is missing: {LOCALITY_CATALOGUE}")
    with LOCALITY_CATALOGUE.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    localities = [
        Locality(
            code=_digits_code(row.get("code")),
            name=str(row.get("name") or "").strip(),
            province_code=_digits_code(row.get("province_code")),
            province_name=str(row.get("province_name") or "").strip(),
            locality_type=str(row.get("locality_type") or "").strip().upper(),
            source_data_status=str(row.get("source_data_status") or "").strip().upper(),
        )
        for row in rows
    ]
    validate_locality_catalogue(localities)
    return sorted(localities, key=lambda item: (item.name, item.code))


def validate_locality_catalogue(localities: list[Locality]) -> None:
    if len(localities) != EXPECTED_LOCALITY_COUNT:
        raise ValueError(f"South Cotabato catalogue has {len(localities)} localities; expected {EXPECTED_LOCALITY_COUNT}.")
    codes = [item.code for item in localities]
    if len(set(codes)) != len(codes):
        raise ValueError("South Cotabato catalogue contains duplicate locality codes.")
    invalid = [item.code for item in localities if len(item.code) != 10 or not item.code.startswith("12063")]
    if invalid:
        raise ValueError("Catalogue contains non-South-Cotabato locality codes: " + ", ".join(invalid))
    if any(item.province_code != PROVINCE_CODE for item in localities):
        raise ValueError("Catalogue contains a locality assigned to the wrong province code.")
    if any(item.locality_type not in {"CITY", "MUNICIPALITY"} for item in localities):
        raise ValueError("Catalogue contains an invalid locality type.")
    if any(item.source_data_status != "CALIBRATED" for item in localities):
        raise ValueError("Catalogue contains an invalid source-data status.")
    required = {
        "1206302000", "1206306000", "1206311000", "1206312000", "1206313000",
        "1206314000", "1206315000", "1206316000", "1206317000", "1206318000", "1206319000",
    }
    if set(codes) != required:
        raise ValueError(f"South Cotabato locality code set is incomplete. Missing={sorted(required-set(codes))}; extra={sorted(set(codes)-required)}")


def fetch_south_cotabato_localities(session: requests.Session | None, timeout: int, logger: logging.Logger) -> list[Locality]:
    del session, timeout
    localities = load_south_cotabato_localities_2023()
    logger.info("Loaded bundled South Cotabato 2023 catalogue: %s localities.", len(localities))
    return localities


def _request_json(session: requests.Session, url: str, timeout: int, logger: logging.Logger) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = session.get(url, timeout=timeout)
            if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
                raise FileNotFoundError(f"HTTP {response.status_code} for {url}")
            response.raise_for_status()
            return response.json()
        except FileNotFoundError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                logger.warning("Boundary download attempt %s/3 failed for %s: %s", attempt, url, exc)
                time.sleep(attempt)
    raise RuntimeError(f"Could not download {url}: {last_error}")


def _geometry_is_valid_geometry_object(geometry: Any) -> bool:
    return (
        isinstance(geometry, dict)
        and geometry.get("type") in {"Polygon", "MultiPolygon"}
        and isinstance(geometry.get("coordinates"), list)
        and bool(geometry.get("coordinates"))
    )


def _iter_geometry_rings(geometry: dict[str, Any]) -> Iterable[list[Any]]:

    coordinates = geometry.get("coordinates") or []
    polygons = [coordinates] if geometry.get("type") == "Polygon" else coordinates
    for polygon in polygons:
        if not isinstance(polygon, list):
            continue
        for ring in polygon:
            if isinstance(ring, list) and ring:
                yield ring


def _axis_aligned_rectangle_ring(ring: list[Any], tolerance: float = 1e-10) -> bool:

    points: list[tuple[float, float]] = []
    for coordinate in ring:
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
            return False
        try:
            point = (float(coordinate[0]), float(coordinate[1]))
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(point[0]) and math.isfinite(point[1])):
            return False
        points.append(point)
    if len(points) < 5:
        return False
    if abs(points[0][0] - points[-1][0]) > tolerance or abs(points[0][1] - points[-1][1]) > tolerance:
        return False
    unique = {(round(x, 10), round(y, 10)) for x, y in points[:-1]}
    if len(unique) != 4:
        return False
    if len({point[0] for point in unique}) != 2 or len({point[1] for point in unique}) != 2:
        return False
    return all(
        abs(x1 - x2) <= tolerance or abs(y1 - y2) <= tolerance
        for (x1, y1), (x2, y2) in zip(points, points[1:])
    )


def _boundary_geometry_quality(features: list[dict[str, Any]]) -> dict[str, float]:
    rectangle_like = 0
    total_vertices = 0
    coordinates: list[tuple[float, float]] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        rings = list(_iter_geometry_rings(geometry))
        if len(rings) == 1 and _axis_aligned_rectangle_ring(rings[0]):
            rectangle_like += 1
        for ring in rings:
            total_vertices += max(0, len(ring) - 1)
            for coordinate in ring:
                if isinstance(coordinate, (list, tuple)) and len(coordinate) >= 2:
                    try:
                        x, y = float(coordinate[0]), float(coordinate[1])
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(x) and math.isfinite(y):
                        coordinates.append((x, y))
    if not coordinates:
        raise ValueError("Boundary cache contains no finite coordinates.")
    xs = [point[0] for point in coordinates]
    ys = [point[1] for point in coordinates]
    count = max(len(features), 1)
    return {
        "rectangle_ratio": rectangle_like / count,
        "mean_vertices_per_feature": total_vertices / count,
        "min_lon": min(xs),
        "max_lon": max(xs),
        "min_lat": min(ys),
        "max_lat": max(ys),
        "lon_span": max(xs) - min(xs),
        "lat_span": max(ys) - min(ys),
    }


def validate_real_boundary_geometry(features: list[dict[str, Any]]) -> dict[str, float]:

    quality = _boundary_geometry_quality(features)
    if quality["rectangle_ratio"] >= 0.50:
        raise ValueError(
            "Boundary cache is a synthetic rectangular grid rather than real barangay polygons "
            f"({quality['rectangle_ratio']:.1%} rectangle-like features)."
        )
    if quality["mean_vertices_per_feature"] < 4.5:
        raise ValueError(
            "Boundary cache is over-simplified or synthetic: mean polygon vertex count is "
            f"{quality['mean_vertices_per_feature']:.2f}."
        )
    if not (123.8 <= quality["min_lon"] <= 125.3 and 124.5 <= quality["max_lon"] <= 126.0):
        raise ValueError(f"Boundary longitudes are outside South Cotabato: {quality}")
    if not (5.0 <= quality["min_lat"] <= 6.7 and 6.0 <= quality["max_lat"] <= 7.5):
        raise ValueError(f"Boundary latitudes are outside South Cotabato: {quality}")
    if quality["lon_span"] < 0.45 or quality["lat_span"] < 0.45:
        raise ValueError(f"Boundary coverage is too small for the complete province: {quality}")
    return quality


def _feature_code(properties: dict[str, Any]) -> str:
    for key in ("ORACLIS_PSGC", "adm4_psgc", "ADM4_PCODE", "psgc", "psgc_code", "code"):
        digits = _digits_code(properties.get(key))
        if digits:
            return digits
    return ""


def _feature_name(properties: dict[str, Any]) -> str:
    for key in ("ORACLIS_BARANGAY", "adm4_en", "ADM4_EN", "name", "barangay", "barangay_name"):
        value = properties.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _feature_locality_code(properties: dict[str, Any]) -> str:
    for key in ("ORACLIS_LOCALITY_CODE", "adm3_psgc", "ADM3_PCODE", "municipality_code"):
        digits = _digits_code(properties.get(key))
        if digits:
            return digits
    return ""


def _feature_province_code(properties: dict[str, Any]) -> str:
    for key in ("ORACLIS_PROVINCE_CODE", "adm2_psgc", "ADM2_PCODE", "province_code"):
        digits = _digits_code(properties.get(key))
        if digits:
            return digits
    locality = _feature_locality_code(properties)
    return PROVINCE_CODE if locality.startswith("12063") else ""


def _feature_region_code(properties: dict[str, Any]) -> str:
    for key in ("adm1_psgc", "ADM1_PCODE", "region_code"):
        digits = _digits_code(properties.get(key))
        if digits:
            return digits
    locality = _feature_locality_code(properties)
    return REGION_CODE if locality.startswith("12063") else ""


def candidate_urls(locality_code: str) -> list[tuple[str, str]]:
    resolutions = [("medres", "0.01"), ("hires", "0.1"), ("lowres", "0.001")]
    return [
        (resolution, GEOJSON_TEMPLATE.format(ref=ref, resolution=resolution, code=locality_code, suffix=suffix))
        for resolution, suffix in resolutions
        for ref in BOUNDARY_REFS
    ]


def download_locality_geojson(session: requests.Session, locality: Locality, timeout: int, logger: logging.Logger) -> tuple[dict[str, Any], str]:
    errors: list[str] = []
    for resolution, url in candidate_urls(locality.code):
        try:
            payload = _request_json(session, url, timeout, logger)
            features = payload.get("features") if isinstance(payload, dict) else None
            if payload.get("type") != "FeatureCollection" or not isinstance(features, list) or not features:
                raise ValueError("Response is not a nonempty GeoJSON FeatureCollection")
            valid: list[dict[str, Any]] = []
            for feature in features:
                if not isinstance(feature, dict) or not _geometry_is_valid_geometry_object(feature.get("geometry")):
                    raise ValueError("GeoJSON contains a non-polygon or empty geometry")
                properties = feature.get("properties")
                if not isinstance(properties, dict):
                    raise ValueError("GeoJSON feature has no properties object")
                if _feature_region_code(properties) != REGION_CODE:
                    raise ValueError("Feature has the wrong region code")
                if _feature_province_code(properties) != PROVINCE_CODE:
                    raise ValueError("Feature has the wrong province code")
                if _feature_locality_code(properties) != locality.code:
                    raise ValueError("Feature locality code does not match the requested locality")
                barangay_code = _feature_code(properties)
                if len(barangay_code) != 10 or not barangay_code.startswith("12063"):
                    raise ValueError(f"Invalid South Cotabato barangay PSGC code {barangay_code or '<missing>'}")
                if not _feature_name(properties):
                    raise ValueError("Feature has no barangay name")
                valid.append(feature)
            payload["features"] = valid
            logger.info("Accepted %s boundary file for %s (%s barangays).", resolution, locality.name, len(valid))
            return payload, url
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError(f"No valid barangay GeoJSON was found for {locality.name} ({locality.code}). " + " | ".join(errors))


def validate_cached_geojson(path: Path, expected_features: int = EXPECTED_BARANGAY_FEATURES) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("type") != "FeatureCollection":
        raise ValueError("Boundary cache is not a GeoJSON FeatureCollection.")
    features = payload.get("features")
    if not isinstance(features, list) or len(features) != expected_features:
        raise ValueError(f"Boundary cache has {0 if not isinstance(features, list) else len(features)} barangays; exactly {expected_features} are required for South Cotabato.")
    expected_localities = {item.code for item in load_south_cotabato_localities_2023()}
    found_localities: set[str] = set()
    barangay_codes: set[str] = set()
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or not _geometry_is_valid_geometry_object(feature.get("geometry")):
            raise ValueError(f"Invalid geometry at feature index {index}.")
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise ValueError(f"Missing properties at feature index {index}.")
        code = _feature_code(properties)
        locality_code = _feature_locality_code(properties)
        if len(code) != 10 or not code.startswith("12063"):
            raise ValueError(f"Non-South-Cotabato barangay code at feature index {index}: {code}")
        if _feature_region_code(properties) != REGION_CODE or _feature_province_code(properties) != PROVINCE_CODE:
            raise ValueError(f"Wrong parent code at feature index {index}.")
        if locality_code not in expected_localities:
            raise ValueError(f"Unexpected locality code at feature index {index}: {locality_code}")
        if code in barangay_codes:
            raise ValueError(f"Duplicate barangay PSGC code: {code}")
        if not _feature_name(properties):
            raise ValueError(f"Missing barangay name at feature index {index}.")
        barangay_codes.add(code)
        found_localities.add(locality_code)
    if found_localities != expected_localities:
        raise ValueError(f"Boundary cache locality coverage mismatch. Missing={sorted(expected_localities-found_localities)}; extra={sorted(found_localities-expected_localities)}")
    validate_real_boundary_geometry(features)
    return payload


def validate_cache_metadata(metadata_path: Path) -> dict[str, Any]:
    if not metadata_path.exists():
        raise ValueError("Boundary cache metadata file is missing.")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("Boundary cache metadata is not a JSON object.")
    files = metadata.get("files")
    if metadata.get("source_repository") != BOUNDARY_REPOSITORY:
        raise ValueError("Boundary cache metadata does not identify the approved source repository.")
    if metadata.get("source_commit") != BOUNDARY_COMMIT:
        raise ValueError("Boundary cache metadata is not pinned to the approved 2023 source commit.")
    if metadata.get("cache_format_version") != 2:
        raise ValueError("Boundary cache metadata predates the real-geometry validation format.")
    if not isinstance(files, list) or len(files) != EXPECTED_LOCALITY_COUNT:
        raise ValueError("Boundary cache metadata does not list all 11 downloaded locality files.")
    listed_codes = {_digits_code(item.get("locality_code")) for item in files if isinstance(item, dict)}
    expected_codes = {item.code for item in load_south_cotabato_localities_2023()}
    if listed_codes != expected_codes:
        raise ValueError("Boundary cache metadata locality list is incomplete or inconsistent.")
    return metadata


def ensure_south_cotabato_boundaries(cache_path: Path, metadata_path: Path, timeout: int, force_download: bool, logger: logging.Logger) -> tuple[dict[str, Any], dict[str, Any]]:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and not force_download:
        try:
            payload = validate_cached_geojson(cache_path)
            metadata = validate_cache_metadata(metadata_path)
            logger.info("Using validated South Cotabato boundary cache: %s (%s barangays).", cache_path, len(payload["features"]))
            return payload, metadata
        except Exception as exc:
            logger.warning("Existing South Cotabato boundary cache is invalid and will be replaced: %s", exc)
            cache_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "ORACLIS-SouthCotabato-Spatiotemporal/2.0",
        "Accept": "application/geo+json, application/json;q=0.9, */*;q=0.1",
    })
    localities = fetch_south_cotabato_localities(session, timeout, logger)
    logger.info("Downloading real 2023 barangay boundaries for all %s South Cotabato localities...", len(localities))
    all_features: list[dict[str, Any]] = []
    sources: list[dict[str, str]] = []
    failed: list[str] = []
    seen_codes: set[str] = set()
    for position, locality in enumerate(localities, start=1):
        logger.info("Boundary download %s/%s: %s (%s)", position, len(localities), locality.name, locality.code)
        try:
            payload, url = download_locality_geojson(session, locality, timeout, logger)
            accepted = 0
            for feature in payload["features"]:
                properties = feature.setdefault("properties", {})
                code = _feature_code(properties)
                if code in seen_codes:
                    raise ValueError(f"Duplicate barangay PSGC across locality files: {code}")
                seen_codes.add(code)
                properties["ORACLIS_PSGC"] = code
                properties["ORACLIS_BARANGAY"] = _feature_name(properties)
                properties["ORACLIS_LOCALITY_CODE"] = locality.code
                properties["ORACLIS_LOCALITY"] = locality.name
                properties["ORACLIS_PROVINCE_CODE"] = locality.province_code
                properties["ORACLIS_PROVINCE"] = locality.province_name
                properties["ORACLIS_LOCALITY_TYPE"] = locality.locality_type
                properties["ORACLIS_SOURCE_DATA_STATUS"] = locality.source_data_status
                all_features.append(feature)
                accepted += 1
            sources.append({"locality_code": locality.code, "locality": locality.name, "url": url, "features": str(accepted)})
        except Exception as exc:
            failed.append(f"{locality.name} ({locality.code}): {exc}")
    if failed:
        raise RuntimeError("South Cotabato boundary download was incomplete. No partial map was accepted. Failed localities:\n- " + "\n- ".join(failed))

    merged = {
        "type": "FeatureCollection",
        "name": "ORACLIS South Cotabato Barangay Boundaries (31 December 2023)",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": all_features,
    }
    temporary = cache_path.with_suffix(".tmp.geojson")
    temporary.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    try:
        validated = validate_cached_geojson(temporary)
        temporary.replace(cache_path)
    finally:
        temporary.unlink(missing_ok=True)
    metadata = {
        "cache_format_version": 2,
        "geometry_snapshot": "31 December 2023",
        "feature_count": len(validated["features"]),
        "locality_count": len(localities),
        "region_psgc": REGION_CODE,
        "province_psgc": PROVINCE_CODE,
        "province_name": "South Cotabato",
        "source_repository": BOUNDARY_REPOSITORY,
        "source_commit": BOUNDARY_COMMIT,
        "catalogue_file": str(LOCALITY_CATALOGUE.relative_to(ROOT)),
        "administrative_scope": "All barangays in the Province of South Cotabato only",
        "geometry_quality": validate_real_boundary_geometry(validated["features"]),
        "files": sources,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved complete South Cotabato boundary cache with %s barangays across %s localities.", len(validated["features"]), len(localities))
    return validated, metadata


def iter_geojson_coordinates(geometry: dict[str, Any]) -> Iterable[tuple[float, float]]:
    coordinates = geometry["coordinates"]
    polygons = [coordinates] if geometry["type"] == "Polygon" else coordinates
    for polygon in polygons:
        for ring in polygon:
            for coordinate in ring:
                if len(coordinate) >= 2:
                    yield float(coordinate[0]), float(coordinate[1])
