from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import html
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import sys
import time
import traceback
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.collections import PatchCollection
from matplotlib.patches import Polygon as MplPolygon
from scipy import sparse
from scipy.stats import poisson
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from south_cotabato_boundaries import (
    EXPECTED_BARANGAY_FEATURES,
    EXPECTED_LOCALITY_COUNT,
    PROVINCE_CODE,
    REGION_CODE,
    ensure_south_cotabato_boundaries,
    load_south_cotabato_localities_2023,
    validate_cached_geojson,
)


@dataclass
class Paths:
    run: Path
    tables: Path
    charts: Path
    maps: Path
    database: Path
    logs: Path


@dataclass
class SpatialGraph:
    w: sparse.csr_matrix
    binary: sparse.csr_matrix
    contiguity_binary: sparse.csr_matrix
    edges: pd.DataFrame
    centroids_lon: np.ndarray
    centroids_lat: np.ndarray


ALERT_LEVELS = ["LOW", "MODERATE", "HIGH", "CRITICAL"]
ALERT_CODE = {name: index for index, name in enumerate(ALERT_LEVELS)}
ALERT_COLORS = {
    "LOW": "#2a9d8f",
    "MODERATE": "#e9c46a",
    "HIGH": "#f4a261",
    "CRITICAL": "#c1121f",
}

OUTBREAK_FACTOR_LABELS = [
    "Seasonal baseline",
    "Long-term trend",
    "Recent-case persistence",
    "Neighbour spillover",
    "High-risk cluster pressure",
    "Bayesian evidence update",
]
OUTBREAK_FACTOR_CODE = {name: index for index, name in enumerate(OUTBREAK_FACTOR_LABELS)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ORACLIS spatio-temporal Bayesian simulation.")
    parser.add_argument("--config", default="spatiotemporal_config.json")
    parser.add_argument("--force-boundary-download", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--mock", action="store_true", help="Use a small deterministic synthetic map for offline code testing.")
    parser.add_argument("--output-dir", default=None, help="Optional explicit output directory.")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            default=str,
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def setup_run(output_dir: str | None) -> tuple[Paths, logging.Logger]:
    outputs = ROOT / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    run = Path(output_dir).expanduser().resolve() if output_dir else outputs / datetime.now().strftime("spatiotemporal_run_%Y%m%d_%H%M%S")
    paths = Paths(
        run=run,
        tables=run / "tables",
        charts=run / "charts",
        maps=run / "maps",
        database=run / "database",
        logs=run / "logs",
    )
    for folder in paths.__dict__.values():
        Path(folder).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("oraclis_spatiotemporal")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = logging.FileHandler(paths.logs / "spatiotemporal.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


    return paths, logger


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper().replace("Ñ", "N")
    substitutions = {
        "STO.": "SANTO",
        "STO ": "SANTO ",
        "STA.": "SANTA",
        "STA ": "SANTA ",
        "CITY OF ": "",
        "MUNICIPALITY OF ": "",
        "BRGY.": "",
        "BARANGAY ": "",
        "POB.": "POBLACION",
        "POB ": "POBLACION ",
        "POb.": "POBLACION",
    }
    for old, new in substitutions.items():
        text = text.replace(old, new)
    text = text.replace("T'BOLI", "TBOLI").replace("T BOLI", "TBOLI")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def name_variants(value: Any) -> set[str]:
    base = normalize_name(value)
    variants = {base}
    variants.add(base.replace(" POBLACION", "").replace("POBLACION ", ""))
    variants.add(base.replace("POBLACION", ""))
    variants.add(base.replace("SAINT ", "SAN "))
    variants.add(base.replace("SANTA ", "STA "))
    return {re.sub(r"\s+", " ", item).strip() for item in variants if item.strip()}


def feature_properties(feature: dict[str, Any]) -> dict[str, Any]:
    return feature.setdefault("properties", {})


def prop_first(properties: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = properties.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""



def polygonal_parts(geometry: Any) -> list[Polygon]:

    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return [part for part in geometry.geoms if not part.is_empty]
    return [part for part in getattr(geometry, "geoms", []) if isinstance(part, Polygon) and not part.is_empty]


def geometry_area_km2_approx(geometry: Any) -> float:

    if geometry.is_empty:
        return 0.0
    point = geometry.representative_point()
    return float(max(geometry.area, 0.0) * 111.32 * 111.32 * math.cos(math.radians(float(point.y))))


def _outer_shell_without_holes(geometry: Any) -> Any:

    shells = [Polygon(part.exterior) for part in polygonal_parts(geometry) if len(part.exterior.coords) >= 4]
    if not shells:
        return geometry
    shell = unary_union(shells)
    if not shell.is_valid:
        shell = shell.buffer(0)
    return shell


def fill_internal_boundary_gaps(
    geojson: dict[str, Any],
    config: dict[str, Any],
    logger: logging.Logger,
) -> tuple[dict[str, Any], Any, dict[str, Any]]:









    features = geojson.get("features") or []
    if not features:
        raise ValueError("Cannot fill boundary gaps because the barangay GeoJSON is empty.")

    originals: list[Any] = []
    for index, feature in enumerate(features):
        geometry = shape(feature.get("geometry"))
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"Gap filling received invalid polygon geometry at feature {index}.")
        originals.append(geometry)

    interface = config.get("interface", {})
    tolerance = float(interface.get("gap_fill_tolerance_degrees", 0.0035))
    minimum_piece_area = float(interface.get("minimum_gap_piece_area_degrees2", 1e-12))
    validation_ratio_tolerance = float(interface.get("gap_partition_validation_ratio", 1e-10))
    if not 0.00001 <= tolerance <= 0.02:
        raise ValueError(f"gap_fill_tolerance_degrees must be between 0.00001 and 0.02; got {tolerance}.")
    if not 0.0 <= validation_ratio_tolerance <= 1e-6:
        raise ValueError("gap_partition_validation_ratio must be between 0 and 1e-6.")

    raw_union = unary_union(originals)
    if not raw_union.is_valid:
        raw_union = raw_union.buffer(0)



    closed = raw_union.buffer(tolerance, join_style=2).buffer(-tolerance, join_style=2)
    if closed.is_empty:
        closed = raw_union
    province_shell = _outer_shell_without_holes(closed)
    if not province_shell.is_valid:
        province_shell = province_shell.buffer(0)
    if province_shell.is_empty:
        raise RuntimeError("Could not construct a valid South Cotabato province shell.")




    boundary_linework = unary_union([province_shell.boundary, *[geometry.boundary for geometry in originals]])
    atomic_faces: list[Polygon] = []
    for face in polygonize(boundary_linework):
        if face.is_empty or face.area <= minimum_piece_area:
            continue
        point = face.representative_point()
        if province_shell.covers(point):
            clipped = face.intersection(province_shell)
            atomic_faces.extend(
                part for part in polygonal_parts(clipped)
                if part.area > minimum_piece_area
            )
    if not atomic_faces:
        raise RuntimeError("Boundary partitioning produced no polygonal faces.")

    tree = STRtree(originals)
    interior_points = [geometry.representative_point() for geometry in originals]
    assigned_faces: list[list[Any]] = [[] for _ in originals]
    gap_faces_by_node: list[list[Any]] = [[] for _ in originals]
    overlap_faces_by_node: list[list[Any]] = [[] for _ in originals]
    gap_face_count = 0
    overlap_face_count = 0
    single_owner_face_count = 0
    gap_area = 0.0
    overlap_area = 0.0

    for face in atomic_faces:
        point = face.representative_point()
        candidate_indices = [int(value) for value in tree.query(point)]
        covering = [index for index in candidate_indices if originals[index].covers(point)]
        if len(covering) == 1:
            owner = covering[0]
            single_owner_face_count += 1
        elif len(covering) > 1:


            owner = min(
                covering,
                key=lambda index: (point.distance(interior_points[index]), index),
            )
            overlap_faces_by_node[owner].append(face)
            overlap_face_count += 1
            overlap_area += float(face.area)
        else:

            owner = int(tree.nearest(point))
            gap_faces_by_node[owner].append(face)
            gap_face_count += 1
            gap_area += float(face.area)
        assigned_faces[owner].append(face)

    filled: list[Any] = []
    for index, faces_for_node in enumerate(assigned_faces):
        if not faces_for_node:
            raise RuntimeError(
                f"Boundary partitioning assigned no area to feature {index}; refusing to create an empty barangay."
            )
        merged = unary_union(faces_for_node)
        if not merged.is_valid:
            merged = merged.buffer(0)
        if merged.is_empty or merged.geom_type not in {"Polygon", "MultiPolygon"}:
            raise RuntimeError(f"Boundary partitioning produced an invalid geometry for feature {index}.")
        filled.append(merged)

    filled_union = unary_union(filled)
    if not filled_union.is_valid:
        filled_union = filled_union.buffer(0)
    residual = province_shell.difference(filled_union)
    residual_area = float(max(residual.area, 0.0))
    overlap_area_after = float(max(sum(item.area for item in filled) - filled_union.area, 0.0))
    shell_area = float(max(province_shell.area, 1e-15))
    residual_ratio = residual_area / shell_area
    overlap_ratio = overlap_area_after / shell_area
    if residual_ratio > validation_ratio_tolerance:
        raise RuntimeError(
            f"Automatic boundary partitioning left {residual_ratio:.3e} of the province shell uncovered."
        )
    if overlap_ratio > validation_ratio_tolerance:
        raise RuntimeError(
            f"Automatic boundary partitioning left {overlap_ratio:.3e} overlapping coverage."
        )

    result = json.loads(json.dumps(geojson))
    total_added_area = 0.0
    total_removed_overlap_area = 0.0
    for index, feature in enumerate(result["features"]):
        final_geometry = filled[index]
        feature["geometry"] = mapping(final_geometry)
        properties = feature.setdefault("properties", {})
        added_geometry = final_geometry.difference(originals[index])
        removed_geometry = originals[index].difference(final_geometry)
        added_km2 = geometry_area_km2_approx(added_geometry)
        removed_km2 = geometry_area_km2_approx(removed_geometry)
        total_added_area += added_km2
        total_removed_overlap_area += removed_km2
        properties["ORACLIS_GAP_FILLED_AREA_KM2"] = round(added_km2, 6)
        properties["ORACLIS_OVERLAP_RESOLVED_AREA_KM2"] = round(removed_km2, 6)
        properties["area_km2"] = round(geometry_area_km2_approx(final_geometry), 6)

    report = {
        "algorithm": "Noded planar-face partition with nearest-barangay gap allocation and deterministic overlap resolution",
        "tolerance_degrees": tolerance,
        "validation_ratio_tolerance": validation_ratio_tolerance,
        "source_feature_count": len(originals),
        "atomic_face_count": len(atomic_faces),
        "single_owner_face_count": single_owner_face_count,
        "detected_gap_face_count": gap_face_count,
        "detected_overlap_face_count": overlap_face_count,
        "assigned_gap_area_degrees2": gap_area,
        "resolved_source_overlap_area_degrees2": overlap_area,
        "assigned_gap_area_km2_approx": total_added_area,
        "resolved_source_overlap_area_km2_approx": total_removed_overlap_area,
        "province_shell_area_km2_approx": geometry_area_km2_approx(province_shell),
        "residual_uncovered_area_km2_approx": geometry_area_km2_approx(residual),
        "residual_uncovered_ratio": residual_ratio,
        "overlap_ratio": overlap_ratio,
        "fully_covered": residual_ratio <= validation_ratio_tolerance and overlap_ratio <= validation_ratio_tolerance,
    }
    logger.info(
        "Boundary partition created %s atomic faces; assigned %.3f km2 of gaps, resolved %.3f km2 of source overlaps; residual %.3e, overlap %.3e.",
        len(atomic_faces), total_added_area, total_removed_overlap_area, residual_ratio, overlap_ratio,
    )
    return result, province_shell, report

def extract_boundary_master(geojson: dict[str, Any]) -> tuple[pd.DataFrame, list[Any]]:
    rows: list[dict[str, Any]] = []
    geometries: list[Any] = []
    for index, feature in enumerate(geojson["features"]):
        properties = feature_properties(feature)
        geometry = shape(feature["geometry"])
        if geometry.is_empty:
            raise ValueError(f"Empty boundary geometry at feature {index}")
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"Invalid boundary geometry at feature {index}: {geometry.geom_type}")
        psgc = prop_first(properties, "ORACLIS_PSGC", "adm4_psgc", "ADM4_PCODE", "psgc", "code")
        barangay = prop_first(properties, "ORACLIS_BARANGAY", "adm4_en", "ADM4_EN", "name")
        locality = prop_first(properties, "ORACLIS_LOCALITY", "adm3_en", "ADM3_EN", "municipality", "city")
        locality_code = prop_first(properties, "ORACLIS_LOCALITY_CODE", "adm3_psgc", "ADM3_PCODE")
        province = prop_first(properties, "ORACLIS_PROVINCE", "adm2_en", "ADM2_EN", "province")
        province_code = prop_first(properties, "ORACLIS_PROVINCE_CODE", "adm2_psgc", "ADM2_PCODE")
        if not psgc or not barangay:
            raise ValueError(f"Boundary feature {index} lacks PSGC/name properties")
        centroid = geometry.representative_point()
        area_km2 = pd.to_numeric(properties.get("area_km2"), errors="coerce")
        if not np.isfinite(area_km2):

            area_km2 = max(0.01, float(geometry.area) * 111.0 * 111.0 * math.cos(math.radians(float(centroid.y))))
        rows.append({
            "NODE_ID": index,
            "PSGC": str(psgc),
            "BARANGAY": barangay,
            "MUNICIPALITY": locality,
            "MUNICIPALITY_CODE": locality_code,
            "PROVINCE": province or "SOUTH COTABATO",
            "PROVINCE_CODE": province_code,
            "AREA_KM2": float(max(area_km2, 0.01)),
            "CENTROID_LON": float(centroid.x),
            "CENTROID_LAT": float(centroid.y),
            "BOUNDARY_NAME_NORMALIZED": normalize_name(barangay),
            "LOCALITY_NAME_NORMALIZED": normalize_name(locality),
        })
        geometries.append(geometry)
    master = pd.DataFrame(rows).sort_values("NODE_ID").reset_index(drop=True)
    if master["PSGC"].duplicated().any():
        duplicate = master.loc[master["PSGC"].duplicated(), "PSGC"].iloc[0]
        raise ValueError(f"Duplicate PSGC boundary code: {duplicate}")
    return master, geometries


def build_municipality_geometries(
    master: pd.DataFrame,
    geometries: list[Any],
) -> tuple[pd.DataFrame, list[Any], dict[str, Any]]:

    rows: list[dict[str, Any]] = []
    dissolved: list[Any] = []
    features: list[dict[str, Any]] = []
    for municipality_code, group in master.groupby("MUNICIPALITY_CODE", sort=True):
        node_ids = group["NODE_ID"].astype(int).tolist()
        geometry = unary_union([geometries[node] for node in node_ids])
        if geometry.is_empty:
            raise ValueError(f"Municipality dissolve produced an empty geometry for {municipality_code}")
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        municipality = str(group["MUNICIPALITY"].iloc[0])
        province = str(group["PROVINCE"].iloc[0])
        calibrated_count = int((group.get("CALIBRATION_STATUS", pd.Series(index=group.index, dtype=object)) == "CALIBRATED_SOUTH_COTABATO").sum())
        rows.append({
            "MUNICIPALITY_CODE": str(municipality_code),
            "MUNICIPALITY": municipality,
            "PROVINCE": province,
            "BARANGAY_COUNT": len(node_ids),
            "CALIBRATED_BARANGAY_COUNT": len(node_ids),
        })
        dissolved.append(geometry)
        features.append({
            "type": "Feature",
            "id": str(municipality_code),
            "properties": {
                "municipality_code": str(municipality_code),
                "municipality": municipality,
                "province": province,
                "barangay_count": len(node_ids),
            },
            "geometry": mapping(geometry),
        })
    municipality_master = pd.DataFrame(rows).sort_values("MUNICIPALITY").reset_index(drop=True)
    municipality_geojson = {"type": "FeatureCollection", "features": features}
    return municipality_master, dissolved, municipality_geojson


def create_mock_geojson(size: int = 4) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    code = 1206302001
    municipalities = [
        ("BANGA", "1206302000"),
        ("LAKE SEBU", "1206319000"),
        ("NORALA", "1206311000"),
        ("POLOMOLOK", "1206312000"),
    ]
    for row in range(size):
        for col in range(size):
            x0 = 124.5 + col * 0.08
            y0 = 6.0 + row * 0.08
            polygon = Polygon([(x0, y0), (x0 + 0.075, y0), (x0 + 0.075, y0 + 0.075), (x0, y0 + 0.075)])
            municipality, municipality_code = municipalities[(row + col) % len(municipalities)]
            features.append({
                "type": "Feature",
                "properties": {
                    "ORACLIS_PSGC": str(code),
                    "ORACLIS_BARANGAY": f"MOCK BARANGAY {row + 1}-{col + 1}",
                    "ORACLIS_LOCALITY": municipality,
                    "ORACLIS_LOCALITY_CODE": municipality_code,
                    "ORACLIS_PROVINCE": "SOUTH COTABATO",
                    "ORACLIS_PROVINCE_CODE": PROVINCE_CODE,
                    "area_km2": 65.0 + row * 5 + col,
                },
                "geometry": mapping(polygon),
            })
            code += 1
    return {"type": "FeatureCollection", "features": features}


def load_source_data(config: dict[str, Any], mock: bool) -> pd.DataFrame:
    source_path = ROOT / config["source_barangay_data"]
    if not source_path.exists():
        raise FileNotFoundError(f"Corrected barangay dataset not found: {source_path}")
    source = pd.read_csv(source_path)
    required = {
        "DATE", "YEAR", "MONTH", "MUNICIPALITY", "BARANGAY",
        "DENGUE_CASES_BARANGAY_EST", "POPULATION_EST",
    }
    missing = sorted(required - set(source.columns))
    if missing:
        raise ValueError(f"Barangay dataset is missing required columns: {missing}")
    source["DATE"] = pd.to_datetime(source["DATE"], errors="raise")
    source["MUNICIPALITY"] = source["MUNICIPALITY"].astype(str).str.strip().str.upper()
    source["BARANGAY"] = source["BARANGAY"].astype(str).str.strip().str.upper()
    numeric = ["DENGUE_CASES_BARANGAY_EST", "POPULATION_EST"]
    source[numeric] = source[numeric].apply(pd.to_numeric, errors="coerce")
    if source[numeric].isna().any().any() or not np.isfinite(source[numeric].to_numpy()).all():
        raise ValueError("Source barangay data contains missing or infinite numeric values.")
    if (source[numeric] < 0).any().any():
        raise ValueError("Source barangay data contains negative values.")
    if source.duplicated(["DATE", "MUNICIPALITY", "BARANGAY"]).any():
        raise ValueError("Source barangay data contains duplicate barangay-month rows.")
    if mock:


        source = source[source["MUNICIPALITY"].isin(["BANGA", "LAKE SEBU"])].copy()
    return source.sort_values(["MUNICIPALITY", "BARANGAY", "DATE"]).reset_index(drop=True)


def resolve_latest_ensemble_artifact() -> Path:
    path = ROOT / "models" / "weighted_ensemble.json"
    if path.exists():
        return path
    raise FileNotFoundError(f"Weighted ensemble artifact not found: {path}")


def resolve_ensemble_test_table(artifact_path: Path) -> Path | None:
    del artifact_path
    path = ROOT / "models" / "weighted_ensemble_test_predictions.csv"
    return path if path.exists() else None

def validate_ensemble_artifact(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    weights = artifact.get("weights", {})
    models = ["MLR", "SARIMAX", "LSTM", "XGBOOST"]
    missing = [model for model in models if model not in weights]
    if missing:
        raise ValueError(f"Weighted ensemble artifact is missing model weights: {missing}")
    values = np.array([float(weights[model]) for model in models])
    if (values < 0).any() or abs(values.sum() - 1.0) > 1e-6:
        raise ValueError(f"Invalid ensemble weights: {weights}")
    return artifact


def municipality_calibration_factors(test_table_path: Path | None) -> dict[str, float]:
    if test_table_path is None:
        return {}
    table = pd.read_csv(test_table_path)
    if not {"MUNICIPALITY", "ACTUAL", "WEIGHTED_ENSEMBLE"}.issubset(table.columns):
        return {}
    table["MUNICIPALITY"] = table["MUNICIPALITY"].astype(str).str.upper().str.strip()
    factors: dict[str, float] = {}
    for municipality, group in table.groupby("MUNICIPALITY"):
        predicted = float(pd.to_numeric(group["WEIGHTED_ENSEMBLE"], errors="coerce").sum())
        actual = float(pd.to_numeric(group["ACTUAL"], errors="coerce").sum())
        if predicted > 0 and np.isfinite(predicted) and np.isfinite(actual):
            factors[normalize_name(municipality)] = float(np.clip(actual / predicted, 0.70, 1.30))
    return factors


def match_source_to_boundaries(source: pd.DataFrame, master: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    source_pairs = source[["MUNICIPALITY", "BARANGAY"]].drop_duplicates().copy()
    source_pairs["SOURCE_LOCALITY_NORM"] = source_pairs["MUNICIPALITY"].map(normalize_name)
    source_pairs["SOURCE_BARANGAY_NORM"] = source_pairs["BARANGAY"].map(normalize_name)

    locality_aliases = {
        "SANTO NINO": {"SANTO NINO", "STO NINO"},
        "TBOLI": {"TBOLI", "T BOLI"},
    }
    master_by_locality: dict[str, list[int]] = defaultdict(list)
    for index, row in master.iterrows():
        norm = row["LOCALITY_NAME_NORMALIZED"]
        master_by_locality[norm].append(index)
        for canonical, aliases in locality_aliases.items():
            if norm in aliases:
                for alias in aliases:
                    master_by_locality[alias].append(index)

    used_nodes: set[int] = set()
    match_rows: list[dict[str, Any]] = []
    for _, source_row in source_pairs.iterrows():
        locality_norm = source_row["SOURCE_LOCALITY_NORM"]
        candidates = list(dict.fromkeys(master_by_locality.get(locality_norm, [])))
        if not candidates:
            possible = difflib.get_close_matches(locality_norm, list(master_by_locality), n=1, cutoff=0.78)
            candidates = list(dict.fromkeys(master_by_locality[possible[0]])) if possible else []
        source_variants = name_variants(source_row["BARANGAY"])
        best: tuple[float, int, str] | None = None
        for node in candidates:
            if node in used_nodes:
                continue
            boundary_name = master.at[node, "BARANGAY"]
            boundary_variants = name_variants(boundary_name)
            if source_variants & boundary_variants:
                score = 1.0
                method = "EXACT_NORMALIZED"
            else:
                score = max(
                    difflib.SequenceMatcher(None, left, right).ratio()
                    for left in source_variants for right in boundary_variants
                )
                method = "FUZZY_NAME"
            if best is None or score > best[0]:
                best = (score, node, method)
        accepted = best is not None and (best[0] >= 0.84 or best[2] == "EXACT_NORMALIZED")
        if accepted:
            score, node, method = best
            used_nodes.add(node)
            match_rows.append({
                "MUNICIPALITY": source_row["MUNICIPALITY"],
                "BARANGAY": source_row["BARANGAY"],
                "MATCH_STATUS": "MATCHED",
                "MATCH_METHOD": method,
                "MATCH_SCORE": score,
                "NODE_ID": int(node),
                "PSGC": master.at[node, "PSGC"],
                "BOUNDARY_MUNICIPALITY": master.at[node, "MUNICIPALITY"],
                "BOUNDARY_BARANGAY": master.at[node, "BARANGAY"],
            })
        else:
            match_rows.append({
                "MUNICIPALITY": source_row["MUNICIPALITY"],
                "BARANGAY": source_row["BARANGAY"],
                "MATCH_STATUS": "UNMATCHED",
                "MATCH_METHOD": "NONE" if best is None else best[2],
                "MATCH_SCORE": np.nan if best is None else best[0],
                "NODE_ID": np.nan,
                "PSGC": "",
                "BOUNDARY_MUNICIPALITY": "",
                "BOUNDARY_BARANGAY": "",
            })
    report = pd.DataFrame(match_rows)
    matched = report[report["MATCH_STATUS"] == "MATCHED"].copy()
    source_with_nodes = source.merge(matched[["MUNICIPALITY", "BARANGAY", "NODE_ID", "PSGC"]], on=["MUNICIPALITY", "BARANGAY"], how="inner")
    source_with_nodes["NODE_ID"] = source_with_nodes["NODE_ID"].astype(int)
    return source_with_nodes, report


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(max(1 - a, 0)))


def connected_components(adjacency: list[set[int]]) -> list[list[int]]:
    seen: set[int] = set()
    components: list[list[int]] = []
    for start in range(len(adjacency)):
        if start in seen:
            continue
        component: list[int] = []
        queue = deque([start])
        seen.add(start)
        while queue:
            node = queue.popleft()
            component.append(node)
            for neighbor in adjacency[node]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        components.append(component)
    return components


def build_spatial_graph(
    geometries: list[Any],
    master: pd.DataFrame,
    config: dict[str, Any],
    logger: logging.Logger,
) -> SpatialGraph:
    n = len(geometries)
    adjacency: list[set[int]] = [set() for _ in range(n)]
    edge_type: dict[tuple[int, int], str] = {}
    tree = STRtree(geometries)
    tolerance = float(config["spatial"]["boundary_tolerance_degrees"])
    logger.info("Building Queen-contiguity graph for %s barangays...", n)
    for i, geometry in enumerate(geometries):
        candidates = tree.query(geometry.buffer(tolerance))
        for raw in candidates:
            if isinstance(raw, (int, np.integer)):
                j = int(raw)
            else:
                try:
                    j = geometries.index(raw)
                except ValueError:
                    continue
            if j <= i:
                continue
            other = geometries[j]
            if geometry.intersects(other) or geometry.distance(other) <= tolerance:
                adjacency[i].add(j)
                adjacency[j].add(i)
                edge_type[(i, j)] = "QUEEN_CONTIGUITY"

    lon = master["CENTROID_LON"].to_numpy(dtype=float)
    lat = master["CENTROID_LAT"].to_numpy(dtype=float)
    k = int(config["spatial"]["minimum_neighbors"])
    max_distance = float(config["spatial"]["fallback_max_distance_km"])
    logger.info("Adding nearest-neighbour fallbacks where necessary...")
    for i in range(n):
        if len(adjacency[i]) >= k:
            continue
        distances = []
        for j in range(n):
            if i == j or j in adjacency[i]:
                continue
            distance = haversine_km(lon[i], lat[i], lon[j], lat[j])
            distances.append((distance, j))
        for distance, j in sorted(distances)[: max(k - len(adjacency[i]), 0)]:
            if distance <= max_distance or not adjacency[i]:
                adjacency[i].add(j)
                adjacency[j].add(i)
                edge_type[tuple(sorted((i, j)))] = "NEAREST_FALLBACK"

    if bool(config["spatial"].get("connect_all_components", True)):
        while True:
            components = connected_components(adjacency)
            if len(components) <= 1:
                break
            base = components[0]
            best: tuple[float, int, int] | None = None
            for other_component in components[1:]:
                for i in base:
                    for j in other_component:
                        distance = haversine_km(lon[i], lat[i], lon[j], lat[j])
                        if best is None or distance < best[0]:
                            best = (distance, i, j)
            if best is None:
                break
            distance, i, j = best
            adjacency[i].add(j)
            adjacency[j].add(i)
            edge_type[tuple(sorted((i, j)))] = "COMPONENT_BRIDGE"

    edge_rows: list[dict[str, Any]] = []
    binary_rows: list[int] = []
    binary_cols: list[int] = []
    contiguity_rows: list[int] = []
    contiguity_cols: list[int] = []
    weighted_rows: list[int] = []
    weighted_cols: list[int] = []
    weighted_data: list[float] = []
    for i in range(n):
        neighbors = sorted(adjacency[i])
        raw_weights = []
        for j in neighbors:
            distance = max(haversine_km(lon[i], lat[i], lon[j], lat[j]), 0.1)
            kind = edge_type.get(tuple(sorted((i, j))), "QUEEN_CONTIGUITY")
            raw_weight = 1.0 if kind == "QUEEN_CONTIGUITY" else 1.0 / (1.0 + distance)
            raw_weights.append(raw_weight)
            binary_rows.append(i)
            binary_cols.append(j)
            if kind == "QUEEN_CONTIGUITY":
                contiguity_rows.append(i)
                contiguity_cols.append(j)
            if i < j:
                edge_rows.append({
                    "SOURCE_NODE_ID": i,
                    "TARGET_NODE_ID": j,
                    "SOURCE_PSGC": master.at[i, "PSGC"],
                    "TARGET_PSGC": master.at[j, "PSGC"],
                    "SOURCE_BARANGAY": master.at[i, "BARANGAY"],
                    "TARGET_BARANGAY": master.at[j, "BARANGAY"],
                    "DISTANCE_KM": distance,
                    "EDGE_TYPE": kind,
                })
        total = sum(raw_weights)
        if not neighbors or total <= 0:
            raise ValueError(f"Spatial node {i} has no valid neighbours after fallback processing.")
        for j, raw_weight in zip(neighbors, raw_weights):
            weighted_rows.append(i)
            weighted_cols.append(j)
            weighted_data.append(raw_weight / total)
    w = sparse.csr_matrix((weighted_data, (weighted_rows, weighted_cols)), shape=(n, n), dtype=float)
    binary = sparse.csr_matrix((np.ones(len(binary_rows)), (binary_rows, binary_cols)), shape=(n, n), dtype=float)
    contiguity_binary = sparse.csr_matrix(
        (np.ones(len(contiguity_rows)), (contiguity_rows, contiguity_cols)), shape=(n, n), dtype=float
    )
    row_sums = np.asarray(w.sum(axis=1)).ravel()
    if not np.allclose(row_sums, 1.0, atol=1e-10):
        raise ValueError("Spatial weights are not row-standardized.")
    if (binary != binary.T).nnz != 0:
        raise ValueError("Spatial adjacency matrix is not symmetric.")
    if (contiguity_binary != contiguity_binary.T).nnz != 0:
        raise ValueError("Strict Queen-contiguity matrix is not symmetric.")
    queen_edge_count = int(sum(1 for row in edge_rows if row["EDGE_TYPE"] == "QUEEN_CONTIGUITY"))
    logger.info(
        "Spatial graph complete: %s undirected edges (%s strict Queen-contiguity), %s components.",
        len(edge_rows), queen_edge_count, len(connected_components(adjacency)),
    )
    return SpatialGraph(
        w=w, binary=binary, contiguity_binary=contiguity_binary, edges=pd.DataFrame(edge_rows),
        centroids_lon=lon, centroids_lat=lat,
    )


def estimate_historical_profiles(
    source_with_nodes: pd.DataFrame,
    master: pd.DataFrame,
    calibration_factors: dict[str, float],
    config: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]:
    n = len(master)
    climatology = np.zeros((n, 12), dtype=float)
    annual_growth = np.zeros(n, dtype=float)
    thresholds = np.ones(n, dtype=float)
    historical_mean = np.zeros(n, dtype=float)
    historical_std = np.zeros(n, dtype=float)
    status = np.array(["CALIBRATED_SOUTH_COTABATO"] * n, dtype=object)
    source_matched = np.zeros(n, dtype=bool)
    profile_rows: list[dict[str, Any]] = []

    recent_start = pd.Timestamp(config["projection"]["climatology_start"])
    growth_clip = config["projection"]["annual_growth_clip"]
    for node, group in source_with_nodes.groupby("NODE_ID"):
        node = int(node)
        recent = group[group["DATE"] >= recent_start]
        if recent.empty:
            recent = group
        factor = calibration_factors.get(normalize_name(group["MUNICIPALITY"].iloc[0]), 1.0)
        monthly = recent.groupby("MONTH")["DENGUE_CASES_BARANGAY_EST"].mean().reindex(range(1, 13)).interpolate().bfill().ffill()
        climatology[node, :] = np.maximum(monthly.to_numpy(dtype=float) * factor, 0.0)
        yearly = group.groupby("YEAR")["DENGUE_CASES_BARANGAY_EST"].sum().sort_index()
        if len(yearly) >= 4 and np.all(yearly.to_numpy() >= 0):
            x = np.arange(len(yearly), dtype=float)
            y = np.log1p(yearly.to_numpy(dtype=float))
            slope = float(np.polyfit(x, y, 1)[0])
            growth = math.expm1(slope)
        else:
            growth = 0.0
        annual_growth[node] = float(np.clip(growth, float(growth_clip[0]), float(growth_clip[1])))
        values = group["DENGUE_CASES_BARANGAY_EST"].to_numpy(dtype=float)
        historical_mean[node] = float(np.mean(values))
        historical_std[node] = float(np.std(values, ddof=1)) if len(values) > 1 else math.sqrt(max(historical_mean[node], 0.1))
        q = float(np.quantile(values, float(config["outbreak"]["historical_quantile"])))
        thresholds[node] = float(max(config["outbreak"]["minimum_case_threshold"], math.ceil(max(q, historical_mean[node] + float(config["outbreak"]["std_multiplier"]) * historical_std[node]))))
        source_matched[node] = True
        profile_rows.append({
            "NODE_ID": node,
            "PSGC": master.at[node, "PSGC"],
            "MUNICIPALITY": master.at[node, "MUNICIPALITY"],
            "BARANGAY": master.at[node, "BARANGAY"],
            "CALIBRATION_FACTOR": factor,
            "ANNUAL_GROWTH_RATE": annual_growth[node],
            "HISTORICAL_MONTHLY_MEAN": historical_mean[node],
            "HISTORICAL_MONTHLY_STD": historical_std[node],
            "OUTBREAK_CASE_THRESHOLD": thresholds[node],
        })

    reference_nodes = np.where(source_matched)[0]
    prior_nodes = np.where(~source_matched)[0]
    if reference_nodes.size == 0:

        reference_monthly = float(source_with_nodes["DENGUE_CASES_BARANGAY_EST"].median()) if not source_with_nodes.empty else 0.25
        reference_area = float(master["AREA_KM2"].median())
        monthly_pattern = np.ones(12)
        monthly_pattern /= monthly_pattern.mean()
        reference_growth = 0.0
    else:
        reference_monthly = float(np.median(historical_mean[reference_nodes]))
        reference_area = float(np.median(master.loc[reference_nodes, "AREA_KM2"]))
        monthly_pattern = np.median(
            climatology[reference_nodes] / np.maximum(climatology[reference_nodes].mean(axis=1, keepdims=True), 1e-8),
            axis=0,
        )
        monthly_pattern = np.maximum(monthly_pattern, 0.05)
        monthly_pattern /= monthly_pattern.mean()
        reference_growth = float(np.median(annual_growth[reference_nodes]))

    prior_scale = float(config["projection"]["scenario_prior_scale"])
    max_prior = float(config["projection"]["scenario_prior_max_monthly_cases"])
    for node in prior_nodes:
        area_scale = math.sqrt(float(master.at[node, "AREA_KM2"]) / max(reference_area, 0.01))
        base = float(np.clip(reference_monthly * prior_scale * area_scale, 0.02, max_prior))
        climatology[node, :] = base * monthly_pattern
        annual_growth[node] = reference_growth * 0.5
        historical_mean[node] = base
        historical_std[node] = math.sqrt(max(base, 0.1))
        thresholds[node] = float(max(config["outbreak"]["minimum_case_threshold"], math.ceil(base + 2.0 * historical_std[node])))

    return climatology, annual_growth, thresholds, historical_mean, historical_std, status, pd.DataFrame(profile_rows)


def load_observed_updates(config: dict[str, Any], master: pd.DataFrame) -> dict[tuple[pd.Timestamp, int], tuple[float, float]]:
    path = ROOT / config["bayesian"]["observed_updates_file"]
    if not path.exists():
        return {}
    table = pd.read_csv(path)
    required = {"DATE", "PSGC", "OBSERVED_CASES"}
    if not required.issubset(table.columns):
        raise ValueError(f"Observed-update file must contain {sorted(required)}")
    table["DATE"] = pd.to_datetime(table["DATE"], errors="raise").dt.to_period("M").dt.to_timestamp()
    table["PSGC"] = table["PSGC"].astype(str).str.replace(r"\.0$", "", regex=True)
    table["OBSERVED_CASES"] = pd.to_numeric(table["OBSERVED_CASES"], errors="raise")
    if "EXPOSURE" not in table.columns:
        table["EXPOSURE"] = 1.0
    else:
        table["EXPOSURE"] = pd.to_numeric(table["EXPOSURE"], errors="coerce").fillna(1.0)
    if (table[["OBSERVED_CASES", "EXPOSURE"]] < 0).any().any():
        raise ValueError("Observed Bayesian updates contain negative values.")
    node_lookup = dict(zip(master["PSGC"], master["NODE_ID"]))
    updates: dict[tuple[pd.Timestamp, int], tuple[float, float]] = {}
    for row in table.itertuples(index=False):
        if row.PSGC in node_lookup:
            updates[(row.DATE, int(node_lookup[row.PSGC]))] = (float(row.OBSERVED_CASES), float(row.EXPOSURE))
    return updates


def local_getis_ord_z(values: np.ndarray, binary: sparse.csr_matrix) -> np.ndarray:
    n = len(values)
    if n < 3 or float(np.std(values)) <= 1e-12:
        return np.zeros(n)
    with_self = binary + sparse.identity(n, format="csr")
    weights_sum = np.asarray(with_self.sum(axis=1)).ravel()
    weights_sq_sum = np.asarray(with_self.power(2).sum(axis=1)).ravel()
    xbar = float(np.mean(values))
    s = math.sqrt(max(float(np.mean(values ** 2) - xbar ** 2), 1e-12))
    numerator = np.asarray(with_self.dot(values)).ravel() - xbar * weights_sum
    denominator = s * np.sqrt(np.maximum((n * weights_sq_sum - weights_sum ** 2) / max(n - 1, 1), 1e-12))
    return numerator / denominator


def global_morans_i(values: np.ndarray, w: sparse.csr_matrix) -> float:
    centered = values - float(np.mean(values))
    denominator = float(centered @ centered)
    if denominator <= 1e-12:
        return 0.0
    numerator = float(centered @ w.dot(centered))
    s0 = float(w.sum())
    return float((len(values) / max(s0, 1e-12)) * numerator / denominator)


def classify_alert(probability: np.ndarray, thresholds: dict[str, float]) -> np.ndarray:
    result = np.full(len(probability), "LOW", dtype=object)
    result[probability >= float(thresholds["moderate"])] = "MODERATE"
    result[probability >= float(thresholds["high"])] = "HIGH"
    result[probability >= float(thresholds["critical"])] = "CRITICAL"
    return result


def projected_case_color_intensity(
    values: np.ndarray, low_quantile: float = 0.02, high_quantile: float = 0.98
) -> tuple[np.ndarray, float, float]:






    numeric = np.asarray(values, dtype=float)
    finite = numeric[np.isfinite(numeric)]
    if finite.size == 0:
        return np.zeros_like(numeric), 0.0, 1.0
    low = float(np.quantile(finite, float(low_quantile)))
    high = float(np.quantile(finite, float(high_quantile)))
    if high <= low + 1e-12:
        high = low + 1.0
    intensity = np.clip((numeric - low) / (high - low), 0.0, 1.0)
    intensity[~np.isfinite(intensity)] = 0.0
    return intensity, low, high


def connected_red_cluster_members(
    red_zone: np.ndarray, adjacency: sparse.csr_matrix, minimum_size: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:

    red = np.asarray(red_zone, dtype=bool)
    n = len(red)
    members = np.zeros(n, dtype=bool)
    cluster_ids = np.full(n, -1, dtype=int)
    cluster_sizes = np.zeros(n, dtype=int)
    visited = np.zeros(n, dtype=bool)
    next_cluster_id = 1
    minimum_size = max(1, int(minimum_size))
    graph = adjacency.tocsr()
    for start in np.flatnonzero(red):
        if visited[start]:
            continue
        stack = [int(start)]
        visited[start] = True
        component: list[int] = []
        while stack:
            node = stack.pop()
            component.append(node)
            neighbors = graph.indices[graph.indptr[node]:graph.indptr[node + 1]]
            for neighbor in neighbors:
                neighbor = int(neighbor)
                if red[neighbor] and not visited[neighbor]:
                    visited[neighbor] = True
                    stack.append(neighbor)
        if len(component) >= minimum_size:
            idx = np.asarray(component, dtype=int)
            members[idx] = True
            cluster_ids[idx] = next_cluster_id
            cluster_sizes[idx] = len(component)
            next_cluster_id += 1
    return members, cluster_ids, cluster_sizes


def cluster_component_onset_flags(
    cluster_ids: np.ndarray, previous_cluster_membership: np.ndarray
) -> np.ndarray:






    ids = np.asarray(cluster_ids, dtype=int)
    previous = np.asarray(previous_cluster_membership, dtype=bool)
    result = np.zeros(len(ids), dtype=bool)
    for cluster_id in sorted(set(ids[ids > 0].tolist())):
        members = np.flatnonzero(ids == cluster_id)
        if members.size and not bool(previous[members].any()):
            result[members] = True
    return result


def select_red_cluster_origins(
    cluster_ids: np.ndarray,
    episode_start_step: np.ndarray,
    posterior_mean: np.ndarray,
    previous_mean: np.ndarray,
    outbreak_threshold: np.ndarray,
    spatial_lag: np.ndarray,
    current_step: int,
) -> np.ndarray:







    ids = np.asarray(cluster_ids, dtype=int)
    origin = np.zeros(len(ids), dtype=bool)
    delta = np.asarray(posterior_mean, dtype=float) - np.asarray(previous_mean, dtype=float)
    threshold_ratio = np.divide(
        np.asarray(posterior_mean, dtype=float),
        np.maximum(np.asarray(outbreak_threshold, dtype=float), 1e-9),
    )
    for cluster_id in sorted(set(ids[ids > 0].tolist())):
        members = np.flatnonzero(ids == cluster_id)
        if members.size == 0:
            continue
        ranked = sorted(
            members.tolist(),
            key=lambda node: (
                int(episode_start_step[node]) if int(episode_start_step[node]) >= 0 else int(current_step),
                -float(delta[node]),
                -float(threshold_ratio[node]),
                -float(spatial_lag[node]),
                int(node),
            ),
        )
        origin[ranked[0]] = True
    return origin


def compose_outbreak_alert_reasons(
    individual_alert: np.ndarray,
    probabilistic_alert: np.ndarray,
    red_cluster_alert: np.ndarray,
) -> np.ndarray:

    individual = np.asarray(individual_alert, dtype=bool)
    probabilistic = np.asarray(probabilistic_alert, dtype=bool)
    cluster = np.asarray(red_cluster_alert, dtype=bool)
    result = np.full(len(individual), "NONE", dtype=object)
    for index in range(len(result)):
        reasons: list[str] = []
        if individual[index]:
            reasons.append("INDIVIDUAL_BARANGAY")
        if probabilistic[index]:
            reasons.append("PROBABILISTIC_SPATIOTEMPORAL")
        if cluster[index]:
            reasons.append("THREE_CONNECTED_RED_BARANGAYS")
        if reasons:
            result[index] = "+".join(reasons)
    return result


def classify_operational_outbreak_state(
    red_case_zone: np.ndarray,
    red_neighbor_count: np.ndarray,
    individual_alert: np.ndarray,
    red_cluster_alert: np.ndarray,
) -> np.ndarray:

    red = np.asarray(red_case_zone, dtype=bool)
    neighbors = np.asarray(red_neighbor_count, dtype=int)
    individual = np.asarray(individual_alert, dtype=bool)
    cluster = np.asarray(red_cluster_alert, dtype=bool)
    result = np.full(len(red), "NORMAL", dtype=object)
    result[red] = "RED_CASE_ZONE"
    result[red & (neighbors >= 1)] = "CLUSTER_WATCH"
    result[individual] = "BARANGAY_OUTBREAK"
    result[cluster] = "CLUSTER_OUTBREAK"
    result[individual & cluster] = "COMBINED_OUTBREAK"
    return result


def simulate(
    dates: pd.DatetimeIndex,
    master: pd.DataFrame,
    graph: SpatialGraph,
    climatology: np.ndarray,
    annual_growth: np.ndarray,
    outbreak_threshold: np.ndarray,
    status: np.ndarray,
    observed_updates: dict[tuple[pd.Timestamp, int], tuple[float, float]],
    config: dict[str, Any],
    logger: logging.Logger,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(int(config["random_seed"]))
    n = len(master)
    simulations = int(config["bayesian"]["monte_carlo_draws"])
    base_cv = float(config["bayesian"]["base_coefficient_of_variation"])
    horizon_cv_growth = float(config["bayesian"]["horizon_cv_growth_per_year"])
    max_cv = float(config["bayesian"]["maximum_coefficient_of_variation"])
    diffusion = float(config["spatial"]["diffusion_weight"])
    temporal_memory_weight = float(config["spatial"]["temporal_memory_weight"])
    neighbor_alert_boost = float(config["spatial"]["neighbor_alert_boost"])
    damping_years = float(config["projection"]["trend_damping_years"])
    max_month_growth = float(config["projection"]["maximum_month_to_month_growth"])
    alert_thresholds = config["outbreak"]["probability_thresholds"]
    cluster_min = int(config["outbreak"]["minimum_high_risk_neighbors"])
    persistence_months = int(config["outbreak"]["persistence_months"])
    red_cluster_config = config["outbreak"].get("red_cluster_detection", {})
    red_cluster_enabled = bool(red_cluster_config.get("enabled", True))
    red_cluster_minimum = int(red_cluster_config.get("minimum_connected_barangays", 3))
    case_color_low_quantile = float(red_cluster_config.get("case_color_low_quantile", 0.02))
    case_color_high_quantile = float(red_cluster_config.get("case_color_high_quantile", 0.98))
    red_intensity_threshold = float(red_cluster_config.get("red_intensity_threshold", 0.80))
    individual_probability_threshold = float(
        config["outbreak"].get("individual_outbreak_probability_threshold", 0.75)
    )

    history: deque[np.ndarray] = deque(
        [climatology[:, month].copy() for month in range(12)], maxlen=12
    )
    previous_mean = history[-1].copy()
    previous_alert = np.full(n, "LOW", dtype=object)
    previous_automatic_alert = np.zeros(n, dtype=bool)
    previous_red_case_zone = np.zeros(n, dtype=bool)
    previous_red_cluster_alert = np.zeros(n, dtype=bool)
    red_episode_start_step = np.full(n, -1, dtype=int)
    persistence = np.zeros(n, dtype=int)
    forecast_records: list[pd.DataFrame] = []
    summary_records: list[dict[str, Any]] = []
    alert_records: list[pd.DataFrame] = []

    for step, date in enumerate(dates):
        month_index = date.month - 1
        horizon_years = step / 12.0
        damp = math.exp(-horizon_years / max(damping_years, 0.1))
        projected_growth = np.power(1.0 + annual_growth, horizon_years * damp)
        seasonal_climatology = np.maximum(climatology[:, month_index], 1e-5)
        baseline = np.maximum(seasonal_climatology * projected_growth, 1e-5)
        trend_uplift = baseline - seasonal_climatology
        historical_vectors = list(history)
        lag_1 = historical_vectors[-1]
        lag_3 = historical_vectors[-3]
        lag_6 = historical_vectors[-6]
        lag_12 = historical_vectors[-12]
        rolling_mean_3 = np.mean(historical_vectors[-3:], axis=0)
        rolling_mean_6 = np.mean(historical_vectors[-6:], axis=0)
        rolling_mean_12 = np.mean(historical_vectors, axis=0)
        rolling_std_3 = np.std(historical_vectors[-3:], axis=0)
        temporal_memory = (
            0.50 * lag_1 + 0.20 * rolling_mean_3 +
            0.15 * rolling_mean_6 + 0.15 * rolling_mean_12
        )
        temporal_adjusted = (1.0 - temporal_memory_weight) * baseline + temporal_memory_weight * temporal_memory
        temporal_memory_uplift = temporal_adjusted - baseline
        spatial_lag = np.maximum(graph.w.dot(previous_mean), 0.0)
        high_neighbor_share = np.asarray(graph.w.dot(np.isin(previous_alert, ["HIGH", "CRITICAL"]).astype(float))).ravel()
        spatial_blended = (1.0 - diffusion) * temporal_adjusted + diffusion * spatial_lag
        spatial_spillover_uplift = spatial_blended - temporal_adjusted
        spatial_adjusted = spatial_blended * (1.0 + neighbor_alert_boost * high_neighbor_share)
        neighbor_cluster_uplift = spatial_adjusted - spatial_blended
        upper_growth_cap = np.maximum(previous_mean * (1.0 + max_month_growth), baseline * 2.5 + 0.25)
        prior_mean = np.minimum(np.maximum(spatial_adjusted, 1e-5), upper_growth_cap)

        cv = min(max_cv, base_cv + horizon_cv_growth * horizon_years)
        variance = np.maximum((cv * prior_mean) ** 2, prior_mean * 0.20 + 0.02)
        alpha = np.maximum(prior_mean ** 2 / variance, 1e-3)
        beta = np.maximum(prior_mean / variance, 1e-6)
        update_flag = np.zeros(n, dtype=bool)
        observed_values = np.full(n, np.nan)
        for node in range(n):
            update = observed_updates.get((date, node))
            if update is not None:
                observed, exposure = update
                alpha[node] += observed
                beta[node] += exposure
                update_flag[node] = True
                observed_values[node] = observed

        lambda_draws = rng.gamma(shape=alpha, scale=1.0 / beta, size=(simulations, n))
        case_draws = rng.poisson(lambda_draws)
        posterior_mean = case_draws.mean(axis=0)
        posterior_median = np.median(case_draws, axis=0)
        bayesian_refinement_delta = posterior_mean - prior_mean
        lower = np.quantile(case_draws, float(config["bayesian"]["lower_quantile"]), axis=0)
        upper = np.quantile(case_draws, float(config["bayesian"]["upper_quantile"]), axis=0)




        lower = np.minimum(lower, posterior_mean)
        upper = np.maximum(upper, posterior_mean)
        outbreak_probability = (case_draws >= outbreak_threshold[None, :]).mean(axis=0)
        alert = classify_alert(outbreak_probability, alert_thresholds)
        high_now = np.isin(alert, ["HIGH", "CRITICAL"])
        high_previous = np.isin(previous_alert, ["HIGH", "CRITICAL"])
        high_risk_onset = high_now & ~high_previous

        case_color_intensity, case_color_low, case_color_high = projected_case_color_intensity(
            posterior_mean, case_color_low_quantile, case_color_high_quantile
        )




        red_case_zone = case_color_intensity >= red_intensity_threshold
        red_episode_start_step = np.where(
            red_case_zone & ~previous_red_case_zone, step, red_episode_start_step
        )
        red_episode_start_step = np.where(red_case_zone, red_episode_start_step, -1)
        red_neighbor_count = np.asarray(
            graph.contiguity_binary.dot(red_case_zone.astype(int))
        ).ravel().astype(int)
        if red_cluster_enabled:
            red_cluster_alert, red_cluster_id, red_cluster_size = connected_red_cluster_members(
                red_case_zone, graph.contiguity_binary, red_cluster_minimum
            )
        else:
            red_cluster_alert = np.zeros(n, dtype=bool)
            red_cluster_id = np.full(n, -1, dtype=int)
            red_cluster_size = np.zeros(n, dtype=int)
        red_cluster_onset = cluster_component_onset_flags(
            red_cluster_id, previous_red_cluster_alert
        )
        red_cluster_origin = select_red_cluster_origins(
            red_cluster_id, red_episode_start_step, posterior_mean, previous_mean,
            outbreak_threshold, spatial_lag, step,
        )




        threshold_safe = np.maximum(outbreak_threshold, 1.0)



        seasonal_base_component = np.minimum(baseline, seasonal_climatology)
        positive_trend_component = np.maximum(baseline - seasonal_climatology, 0.0)
        factor_matrix = np.column_stack([
            np.maximum((1.0 - diffusion) * (1.0 - temporal_memory_weight) * seasonal_base_component / threshold_safe, 0.0),
            np.maximum((1.0 - diffusion) * (1.0 - temporal_memory_weight) * positive_trend_component / threshold_safe, 0.0),
            np.maximum((1.0 - diffusion) * temporal_memory_weight * temporal_memory / threshold_safe, 0.0),
            np.maximum(diffusion * spatial_lag / threshold_safe, 0.0),
            np.maximum(neighbor_cluster_uplift / threshold_safe, 0.0),
            np.maximum(bayesian_refinement_delta / threshold_safe, 0.0),
        ])
        factor_totals = factor_matrix.sum(axis=1)
        factor_shares = np.divide(
            factor_matrix,
            factor_totals[:, None],
            out=np.zeros_like(factor_matrix),
            where=factor_totals[:, None] > 1e-12,
        )
        no_factor = factor_totals <= 1e-12
        factor_shares[no_factor, 0] = 1.0
        dominant_factor_index = np.argmax(factor_shares, axis=1)
        dominant_factor = np.asarray(OUTBREAK_FACTOR_LABELS, dtype=object)[dominant_factor_index]

        high_binary = high_now.astype(int)
        high_neighbor_count = np.asarray(graph.binary.dot(high_binary)).ravel().astype(int)
        cluster_flag = high_neighbor_count >= cluster_min
        persistence = np.where(np.isin(alert, ["HIGH", "CRITICAL"]), persistence + 1, 0)
        persistent_flag = persistence >= persistence_months
        probabilistic_outbreak_alert = np.isin(alert, ["HIGH", "CRITICAL"]) & (cluster_flag | persistent_flag)
        individual_outbreak_alert = (
            (outbreak_probability >= individual_probability_threshold) |
            (posterior_mean >= outbreak_threshold)
        )
        automatic_alert = individual_outbreak_alert | probabilistic_outbreak_alert | red_cluster_alert
        automatic_alert_onset = automatic_alert & ~previous_automatic_alert
        outbreak_alert_reason = compose_outbreak_alert_reasons(
            individual_outbreak_alert, probabilistic_outbreak_alert, red_cluster_alert
        )
        outbreak_state = classify_operational_outbreak_state(
            red_case_zone, red_neighbor_count, individual_outbreak_alert, red_cluster_alert
        )
        hotspot_z = local_getis_ord_z(posterior_mean, graph.binary)
        moran_i = global_morans_i(posterior_mean, graph.w)

        frame = pd.DataFrame({
            "DATE": date.strftime("%Y-%m-%d"),
            "NODE_ID": master["NODE_ID"].to_numpy(),
            "PSGC": master["PSGC"].to_numpy(),
            "PROVINCE": master["PROVINCE"].to_numpy(),
            "PROVINCE_CODE": master["PROVINCE_CODE"].to_numpy(),
            "MUNICIPALITY": master["MUNICIPALITY"].to_numpy(),
            "MUNICIPALITY_CODE": master["MUNICIPALITY_CODE"].to_numpy(),
            "BARANGAY": master["BARANGAY"].to_numpy(),
            "CALIBRATION_STATUS": status,
            "SEASONAL_CLIMATOLOGY_CASES": seasonal_climatology,
            "PROJECTED_GROWTH_MULTIPLIER": projected_growth,
            "BASELINE_CASES": baseline,
            "TREND_UPLIFT_CASES": trend_uplift,
            "TEMPORAL_LAG_1_CASES": lag_1,
            "TEMPORAL_LAG_3_CASES": lag_3,
            "TEMPORAL_LAG_6_CASES": lag_6,
            "TEMPORAL_LAG_12_CASES": lag_12,
            "ROLLING_MEAN_3_MONTHS": rolling_mean_3,
            "ROLLING_MEAN_6_MONTHS": rolling_mean_6,
            "ROLLING_MEAN_12_MONTHS": rolling_mean_12,
            "ROLLING_STD_3_MONTHS": rolling_std_3,
            "TEMPORAL_MEMORY_CASES": temporal_memory,
            "TEMPORAL_ADJUSTED_CASES": temporal_adjusted,
            "TEMPORAL_MEMORY_UPLIFT_CASES": temporal_memory_uplift,
            "SPATIAL_LAG_CASES": spatial_lag,
            "SPATIAL_SPILLOVER_UPLIFT_CASES": spatial_spillover_uplift,
            "HIGH_RISK_NEIGHBOR_SHARE": high_neighbor_share,
            "NEIGHBOR_CLUSTER_UPLIFT_CASES": neighbor_cluster_uplift,
            "PRIOR_MEAN_CASES": prior_mean,
            "POSTERIOR_MEAN_CASES": posterior_mean,
            "BAYESIAN_REFINEMENT_DELTA_CASES": bayesian_refinement_delta,
            "FACTOR_SEASONAL_BASELINE_SHARE": factor_shares[:, 0],
            "FACTOR_LONG_TERM_TREND_SHARE": factor_shares[:, 1],
            "FACTOR_RECENT_CASE_PERSISTENCE_SHARE": factor_shares[:, 2],
            "FACTOR_NEIGHBOUR_SPILLOVER_SHARE": factor_shares[:, 3],
            "FACTOR_HIGH_RISK_CLUSTER_SHARE": factor_shares[:, 4],
            "FACTOR_BAYESIAN_EVIDENCE_SHARE": factor_shares[:, 5],
            "DOMINANT_OUTBREAK_FACTOR": dominant_factor,
            "DOMINANT_OUTBREAK_FACTOR_CODE": dominant_factor_index,
            "POSTERIOR_MEDIAN_CASES": posterior_median,
            "LOWER_CREDIBLE_CASES": lower,
            "UPPER_CREDIBLE_CASES": upper,
            "OUTBREAK_CASE_THRESHOLD": outbreak_threshold,
            "OUTBREAK_PROBABILITY": outbreak_probability,
            "ALERT_LEVEL": alert,
            "HIGH_RISK_ONSET": high_risk_onset,
            "OUTBREAK_ONSET_PING": automatic_alert_onset,
            "CASE_COLOR_INTENSITY": case_color_intensity,
            "CASE_COLOR_LOW_REFERENCE": case_color_low,
            "CASE_COLOR_HIGH_REFERENCE": case_color_high,
            "RED_CASE_ZONE": red_case_zone,
            "RED_CASE_NEIGHBOR_COUNT": red_neighbor_count,
            "RED_CLUSTER_ID": red_cluster_id,
            "RED_CLUSTER_SIZE": red_cluster_size,
            "RED_CLUSTER_OUTBREAK_ALERT": red_cluster_alert,
            "RED_CLUSTER_OUTBREAK_ONSET": red_cluster_onset,
            "RED_CLUSTER_LIKELY_ORIGIN": red_cluster_origin,
            "INDIVIDUAL_OUTBREAK_ALERT": individual_outbreak_alert,
            "PROBABILISTIC_OUTBREAK_ALERT": probabilistic_outbreak_alert,
            "OUTBREAK_ALERT_STATE": outbreak_state,
            "OUTBREAK_ALERT_REASON": outbreak_alert_reason,
            "HIGH_RISK_NEIGHBOR_COUNT": high_neighbor_count,
            "CLUSTER_ALERT": cluster_flag,
            "PERSISTENT_ALERT": persistent_flag,
            "AUTOMATIC_OUTBREAK_ALERT": automatic_alert,
            "HOTSPOT_Z_SCORE": hotspot_z,
            "BAYESIAN_OBSERVATION_APPLIED": update_flag,
            "OBSERVED_CASES_UPDATE": observed_values,
            "HORIZON_MONTH": step + 1,
            "HORIZON_YEAR": horizon_years,
            "CALIBRATION_NOTE": np.full(n, "CALIBRATED SOUTH COTABATO BARANGAY", dtype=object),
        })
        forecast_records.append(frame)
        alert_records.append(frame[frame["AUTOMATIC_OUTBREAK_ALERT"]].copy())
        summary_records.append({
            "DATE": date.strftime("%Y-%m-%d"),
            "PROVINCE_POSTERIOR_MEAN_CASES": float(posterior_mean.sum()),
            "PROVINCE_LOWER_CREDIBLE_CASES": float(lower.sum()),
            "PROVINCE_UPPER_CREDIBLE_CASES": float(upper.sum()),
            "CALIBRATED_POSTERIOR_MEAN_CASES": float(posterior_mean.sum()),
            "LOW_ALERT_COUNT": int(np.sum(alert == "LOW")),
            "MODERATE_ALERT_COUNT": int(np.sum(alert == "MODERATE")),
            "HIGH_ALERT_COUNT": int(np.sum(alert == "HIGH")),
            "CRITICAL_ALERT_COUNT": int(np.sum(alert == "CRITICAL")),
            "AUTOMATIC_OUTBREAK_ALERT_COUNT": int(automatic_alert.sum()),
            "INDIVIDUAL_OUTBREAK_ALERT_COUNT": int(individual_outbreak_alert.sum()),
            "PROBABILISTIC_OUTBREAK_ALERT_COUNT": int(probabilistic_outbreak_alert.sum()),
            "RED_CLUSTER_OUTBREAK_ALERT_COUNT": int(red_cluster_alert.sum()),
            "RED_CLUSTER_LIKELY_ORIGIN_COUNT": int(red_cluster_origin.sum()),
            "RED_CASE_ZONE_COUNT": int(red_case_zone.sum()),
            "CLUSTER_WATCH_COUNT": int(np.sum((red_case_zone) & (red_neighbor_count >= 1) & ~red_cluster_alert)),
            "RED_CLUSTER_COUNT": int(len(set(red_cluster_id[red_cluster_id > 0].tolist()))),
            "NEW_RED_CLUSTER_COUNT": int(red_cluster_origin[red_cluster_onset].sum()),
            "NEW_HIGH_RISK_ONSET_COUNT": int(high_risk_onset.sum()),
            "NEW_AUTOMATIC_OUTBREAK_ONSET_COUNT": int(automatic_alert_onset.sum()),
            "MORANS_I": moran_i,
            "MEAN_CREDIBLE_INTERVAL_WIDTH": float(np.mean(upper - lower)),
            "BAYESIAN_UPDATE_COUNT": int(update_flag.sum()),
        })
        history.append(posterior_mean.copy())
        previous_mean = posterior_mean
        previous_alert = alert
        previous_automatic_alert = automatic_alert
        previous_red_case_zone = red_case_zone
        previous_red_cluster_alert = red_cluster_alert
        if step == 0 or (step + 1) % 12 == 0 or step == len(dates) - 1:
            logger.info(
                "Simulated %s (%s/%s): South Cotabato mean %.2f, alerts %s",
                date.strftime("%Y-%m"), step + 1, len(dates), posterior_mean.sum(), int(automatic_alert.sum()),
            )

    forecasts = pd.concat(forecast_records, ignore_index=True)
    alerts = pd.concat(alert_records, ignore_index=True) if any(not item.empty for item in alert_records) else forecasts.head(0).copy()
    summaries = pd.DataFrame(summary_records)
    return forecasts, alerts, summaries


def build_annual_summary(forecasts: pd.DataFrame) -> pd.DataFrame:
    table = forecasts.copy()
    table["YEAR"] = pd.to_datetime(table["DATE"]).dt.year
    summary = table.groupby(["YEAR", "CALIBRATION_STATUS"], as_index=False).agg(
        POSTERIOR_MEAN_CASES=("POSTERIOR_MEAN_CASES", "sum"),
        LOWER_CREDIBLE_CASES=("LOWER_CREDIBLE_CASES", "sum"),
        UPPER_CREDIBLE_CASES=("UPPER_CREDIBLE_CASES", "sum"),
        AUTOMATIC_OUTBREAK_ALERT_MONTHS=("AUTOMATIC_OUTBREAK_ALERT", "sum"),
        MAX_OUTBREAK_PROBABILITY=("OUTBREAK_PROBABILITY", "max"),
    )
    return summary


def build_monthly_municipality_summary(forecasts: pd.DataFrame) -> pd.DataFrame:
    table = forecasts.copy()
    grouped = table.groupby(["DATE", "MUNICIPALITY", "MUNICIPALITY_CODE"], as_index=False).agg(
        POSTERIOR_MEAN_CASES=("POSTERIOR_MEAN_CASES", "sum"),
        LOWER_CREDIBLE_CASES=("LOWER_CREDIBLE_CASES", "sum"),
        UPPER_CREDIBLE_CASES=("UPPER_CREDIBLE_CASES", "sum"),
        MEAN_OUTBREAK_PROBABILITY=("OUTBREAK_PROBABILITY", "mean"),
        MAX_OUTBREAK_PROBABILITY=("OUTBREAK_PROBABILITY", "max"),
        LOW_ALERT_COUNT=("ALERT_LEVEL", lambda values: int(np.sum(np.asarray(values) == "LOW"))),
        MODERATE_ALERT_COUNT=("ALERT_LEVEL", lambda values: int(np.sum(np.asarray(values) == "MODERATE"))),
        HIGH_ALERT_COUNT=("ALERT_LEVEL", lambda values: int(np.sum(np.asarray(values) == "HIGH"))),
        CRITICAL_ALERT_COUNT=("ALERT_LEVEL", lambda values: int(np.sum(np.asarray(values) == "CRITICAL"))),
        AUTOMATIC_OUTBREAK_ALERT_COUNT=("AUTOMATIC_OUTBREAK_ALERT", "sum"),
        INDIVIDUAL_OUTBREAK_ALERT_COUNT=("INDIVIDUAL_OUTBREAK_ALERT", "sum"),
        PROBABILISTIC_OUTBREAK_ALERT_COUNT=("PROBABILISTIC_OUTBREAK_ALERT", "sum"),
        RED_CLUSTER_OUTBREAK_ALERT_COUNT=("RED_CLUSTER_OUTBREAK_ALERT", "sum"),
        RED_CLUSTER_LIKELY_ORIGIN_COUNT=("RED_CLUSTER_LIKELY_ORIGIN", "sum"),
        RED_CASE_ZONE_COUNT=("RED_CASE_ZONE", "sum"),
        NEW_AUTOMATIC_OUTBREAK_ONSET_COUNT=("OUTBREAK_ONSET_PING", "sum"),
        NEW_HIGH_RISK_ONSET_COUNT=("HIGH_RISK_ONSET", "sum"),
        MEAN_HOTSPOT_Z_SCORE=("HOTSPOT_Z_SCORE", "mean"),
        MAX_HOTSPOT_Z_SCORE=("HOTSPOT_Z_SCORE", "max"),
        BARANGAY_COUNT=("PSGC", "nunique"),
        CALIBRATED_BARANGAY_COUNT=("CALIBRATION_STATUS", lambda values: int(np.sum(np.asarray(values) == "CALIBRATED_SOUTH_COTABATO"))),
    )
    return grouped.sort_values(["DATE", "MUNICIPALITY"]).reset_index(drop=True)


def build_red_cluster_events(forecasts: pd.DataFrame) -> pd.DataFrame:

    required = {
        "DATE", "RED_CLUSTER_ID", "RED_CLUSTER_SIZE", "RED_CLUSTER_LIKELY_ORIGIN",
        "RED_CLUSTER_OUTBREAK_ONSET", "MUNICIPALITY", "BARANGAY", "PSGC",
        "POSTERIOR_MEAN_CASES", "OUTBREAK_PROBABILITY", "DOMINANT_OUTBREAK_FACTOR",
    }
    missing = sorted(required - set(forecasts.columns))
    if missing:
        raise ValueError(f"Cluster-event table is missing forecast columns: {missing}")
    active = forecasts[forecasts["RED_CLUSTER_ID"] > 0].copy()
    columns = [
        "DATE", "RED_CLUSTER_ID", "CLUSTER_SIZE", "NEW_CLUSTER_ONSET",
        "LIKELY_ORIGIN_PSGC", "LIKELY_ORIGIN_MUNICIPALITY", "LIKELY_ORIGIN_BARANGAY",
        "MEMBER_PSGC_LIST", "MEMBER_NAME_LIST", "MUNICIPALITY_LIST",
        "PROJECTED_CLUSTER_CASES", "MEAN_OUTBREAK_PROBABILITY", "MAX_OUTBREAK_PROBABILITY",
        "DOMINANT_FACTOR_MODE",
    ]
    if active.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, Any]] = []
    for (date, cluster_id), group in active.groupby(["DATE", "RED_CLUSTER_ID"], sort=True):
        group = group.sort_values(["MUNICIPALITY", "BARANGAY", "PSGC"])
        origins = group[group["RED_CLUSTER_LIKELY_ORIGIN"]]
        if len(origins) != 1:
            raise ValueError(
                f"Expected exactly one likely origin for cluster {cluster_id} on {date}; found {len(origins)}."
            )
        origin = origins.iloc[0]
        factors = group["DOMINANT_OUTBREAK_FACTOR"].astype(str)
        factor_mode = factors.mode().iloc[0] if not factors.mode().empty else factors.iloc[0]
        rows.append({
            "DATE": str(date),
            "RED_CLUSTER_ID": int(cluster_id),
            "CLUSTER_SIZE": int(group["RED_CLUSTER_SIZE"].max()),
            "NEW_CLUSTER_ONSET": bool(group["RED_CLUSTER_OUTBREAK_ONSET"].any()),
            "LIKELY_ORIGIN_PSGC": str(origin["PSGC"]),
            "LIKELY_ORIGIN_MUNICIPALITY": str(origin["MUNICIPALITY"]),
            "LIKELY_ORIGIN_BARANGAY": str(origin["BARANGAY"]),
            "MEMBER_PSGC_LIST": "; ".join(group["PSGC"].astype(str).tolist()),
            "MEMBER_NAME_LIST": "; ".join(
                (group["BARANGAY"].astype(str) + " (" + group["MUNICIPALITY"].astype(str) + ")").tolist()
            ),
            "MUNICIPALITY_LIST": "; ".join(sorted(group["MUNICIPALITY"].astype(str).unique().tolist())),
            "PROJECTED_CLUSTER_CASES": float(group["POSTERIOR_MEAN_CASES"].sum()),
            "MEAN_OUTBREAK_PROBABILITY": float(group["OUTBREAK_PROBABILITY"].mean()),
            "MAX_OUTBREAK_PROBABILITY": float(group["OUTBREAK_PROBABILITY"].max()),
            "DOMINANT_FACTOR_MODE": str(factor_mode),
        })
    return pd.DataFrame(rows, columns=columns)


def build_annual_municipality_summary(monthly: pd.DataFrame) -> pd.DataFrame:
    table = monthly.copy()
    table["YEAR"] = pd.to_datetime(table["DATE"]).dt.year
    return table.groupby(["YEAR", "MUNICIPALITY", "MUNICIPALITY_CODE"], as_index=False).agg(
        POSTERIOR_MEAN_CASES=("POSTERIOR_MEAN_CASES", "sum"),
        LOWER_CREDIBLE_CASES=("LOWER_CREDIBLE_CASES", "sum"),
        UPPER_CREDIBLE_CASES=("UPPER_CREDIBLE_CASES", "sum"),
        MEAN_OUTBREAK_PROBABILITY=("MEAN_OUTBREAK_PROBABILITY", "mean"),
        MAX_OUTBREAK_PROBABILITY=("MAX_OUTBREAK_PROBABILITY", "max"),
        AUTOMATIC_OUTBREAK_ALERT_MONTHS=("AUTOMATIC_OUTBREAK_ALERT_COUNT", "sum"),
        MAX_CRITICAL_BARANGAYS=("CRITICAL_ALERT_COUNT", "max"),
    ).sort_values(["YEAR", "MUNICIPALITY"]).reset_index(drop=True)


def build_municipality_coverage_summary(master: pd.DataFrame) -> pd.DataFrame:
    return master.groupby(["MUNICIPALITY_CODE", "MUNICIPALITY"], as_index=False).agg(
        TOTAL_BARANGAYS=("PSGC", "nunique"),
        CALIBRATED_BARANGAYS=("CALIBRATION_STATUS", "count"),
    ).sort_values("MUNICIPALITY").reset_index(drop=True)


def top_risk_table(forecasts: pd.DataFrame, top_n: int = 50) -> pd.DataFrame:
    return (
        forecasts.groupby(["PSGC", "PROVINCE", "MUNICIPALITY", "BARANGAY", "CALIBRATION_STATUS"], as_index=False)
        .agg(
            MEAN_OUTBREAK_PROBABILITY=("OUTBREAK_PROBABILITY", "mean"),
            MAX_OUTBREAK_PROBABILITY=("OUTBREAK_PROBABILITY", "max"),
            MEAN_POSTERIOR_CASES=("POSTERIOR_MEAN_CASES", "mean"),
            MAX_POSTERIOR_CASES=("POSTERIOR_MEAN_CASES", "max"),
            AUTOMATIC_ALERT_MONTHS=("AUTOMATIC_OUTBREAK_ALERT", "sum"),
            MAX_HOTSPOT_Z_SCORE=("HOTSPOT_Z_SCORE", "max"),
        )
        .sort_values(["AUTOMATIC_ALERT_MONTHS", "MAX_OUTBREAK_PROBABILITY", "MAX_POSTERIOR_CASES"], ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )


def build_outbreak_factor_summary(forecasts: pd.DataFrame) -> pd.DataFrame:

    factor_columns = {
        "Seasonal baseline": "FACTOR_SEASONAL_BASELINE_SHARE",
        "Long-term trend": "FACTOR_LONG_TERM_TREND_SHARE",
        "Recent-case persistence": "FACTOR_RECENT_CASE_PERSISTENCE_SHARE",
        "Neighbour spillover": "FACTOR_NEIGHBOUR_SPILLOVER_SHARE",
        "High-risk cluster pressure": "FACTOR_HIGH_RISK_CLUSTER_SHARE",
        "Bayesian evidence update": "FACTOR_BAYESIAN_EVIDENCE_SHARE",
    }
    high = forecasts["ALERT_LEVEL"].isin(["HIGH", "CRITICAL"])
    rows = []
    for factor, column in factor_columns.items():
        dominant = forecasts["DOMINANT_OUTBREAK_FACTOR"].eq(factor)
        rows.append({
            "OUTBREAK_FACTOR": factor,
            "MEAN_FACTOR_SHARE": float(forecasts[column].mean()),
            "MEDIAN_FACTOR_SHARE": float(forecasts[column].median()),
            "DOMINANT_FORECAST_BARANGAY_MONTHS": int(dominant.sum()),
            "HIGH_RISK_DOMINANT_BARANGAY_MONTHS": int((dominant & high).sum()),
            "OUTBREAK_ONSET_MONTHS": int((dominant & forecasts["OUTBREAK_ONSET_PING"]).sum()),
            "MEAN_OUTBREAK_PROBABILITY_WHEN_DOMINANT": float(forecasts.loc[dominant, "OUTBREAK_PROBABILITY"].mean()) if dominant.any() else 0.0,
        })
    return pd.DataFrame(rows).sort_values("MEAN_FACTOR_SHARE", ascending=False).reset_index(drop=True)


def build_outbreak_factor_municipality_summary(forecasts: pd.DataFrame) -> pd.DataFrame:
    factor_columns = {
        "Seasonal baseline": "FACTOR_SEASONAL_BASELINE_SHARE",
        "Long-term trend": "FACTOR_LONG_TERM_TREND_SHARE",
        "Recent-case persistence": "FACTOR_RECENT_CASE_PERSISTENCE_SHARE",
        "Neighbour spillover": "FACTOR_NEIGHBOUR_SPILLOVER_SHARE",
        "High-risk cluster pressure": "FACTOR_HIGH_RISK_CLUSTER_SHARE",
        "Bayesian evidence update": "FACTOR_BAYESIAN_EVIDENCE_SHARE",
    }
    rows = []
    for municipality, group in forecasts.groupby("MUNICIPALITY", sort=True):
        high = group["ALERT_LEVEL"].isin(["HIGH", "CRITICAL"])
        for factor, column in factor_columns.items():
            dominant = group["DOMINANT_OUTBREAK_FACTOR"].eq(factor)
            rows.append({
                "MUNICIPALITY": municipality,
                "OUTBREAK_FACTOR": factor,
                "MEAN_FACTOR_SHARE": float(group[column].mean()),
                "DOMINANT_FORECAST_BARANGAY_MONTHS": int(dominant.sum()),
                "HIGH_RISK_DOMINANT_BARANGAY_MONTHS": int((dominant & high).sum()),
                "OUTBREAK_ONSET_MONTHS": int((dominant & group["OUTBREAK_ONSET_PING"]).sum()),
            })
    return pd.DataFrame(rows).sort_values(["MUNICIPALITY", "MEAN_FACTOR_SHARE"], ascending=[True, False]).reset_index(drop=True)


def build_outside_province_mask(province_geojson: dict[str, Any]) -> dict[str, Any]:

    province = shape(province_geojson["features"][0]["geometry"])
    minx, miny, maxx, maxy = province.bounds
    pad_x = max((maxx - minx) * 2.2, 1.5)
    pad_y = max((maxy - miny) * 2.2, 1.5)
    surrounding = box(minx - pad_x, miny - pad_y, maxx + pad_x, maxy + pad_y)
    outside = surrounding.difference(province)
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"purpose": "Gray-out mask outside South Cotabato"},
            "geometry": mapping(outside),
        }],
    }


def save_sparse_graph(graph: SpatialGraph, paths: Paths) -> None:
    sparse.save_npz(paths.database / "spatial_weights_row_standardized.npz", graph.w)
    sparse.save_npz(paths.database / "spatial_adjacency_binary.npz", graph.binary)
    sparse.save_npz(paths.database / "strict_queen_contiguity_binary.npz", graph.contiguity_binary)


def write_sqlite(
    db_path: Path,
    master: pd.DataFrame,
    municipality_master: pd.DataFrame,
    graph: SpatialGraph,
    forecasts: pd.DataFrame,
    alerts: pd.DataFrame,
    summaries: pd.DataFrame,
    monthly_municipality: pd.DataFrame,
    annual_municipality: pd.DataFrame,
    cluster_events: pd.DataFrame,
    metadata: dict[str, Any],
) -> None:
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as connection:
        master.to_sql("barangays", connection, index=False, if_exists="replace")
        municipality_master.to_sql("municipalities", connection, index=False, if_exists="replace")
        graph.edges.to_sql("adjacency_edges", connection, index=False, if_exists="replace")
        forecasts.to_sql("forecasts", connection, index=False, if_exists="replace", chunksize=5000)
        alerts.to_sql("alerts", connection, index=False, if_exists="replace", chunksize=5000)
        summaries.to_sql("monthly_province_summary", connection, index=False, if_exists="replace")
        monthly_municipality.to_sql("monthly_municipality_summary", connection, index=False, if_exists="replace")
        annual_municipality.to_sql("annual_municipality_summary", connection, index=False, if_exists="replace")
        cluster_events.to_sql("red_cluster_events", connection, index=False, if_exists="replace")
        pd.DataFrame([{"KEY": key, "VALUE": json.dumps(value, ensure_ascii=False, default=str)} for key, value in metadata.items()]).to_sql(
            "metadata", connection, index=False, if_exists="replace"
        )
        connection.executescript(
            """
            CREATE INDEX idx_forecasts_date ON forecasts(DATE);
            CREATE INDEX idx_forecasts_psgc ON forecasts(PSGC);
            CREATE INDEX idx_forecasts_date_psgc ON forecasts(DATE, PSGC);
            CREATE INDEX idx_forecasts_municipality_date ON forecasts(MUNICIPALITY, DATE);
            CREATE INDEX idx_alerts_date ON alerts(DATE);
            CREATE INDEX idx_alerts_psgc ON alerts(PSGC);
            CREATE INDEX idx_barangays_psgc ON barangays(PSGC);
            CREATE INDEX idx_municipality_summary_date ON monthly_municipality_summary(DATE);
            CREATE INDEX idx_cluster_events_date ON red_cluster_events(DATE);
            CREATE INDEX idx_cluster_events_origin ON red_cluster_events(LIKELY_ORIGIN_PSGC);
            """
        )


def polygon_parts(geometry: Any) -> Iterable[Polygon]:
    if isinstance(geometry, Polygon):
        yield geometry
    elif isinstance(geometry, MultiPolygon):
        yield from geometry.geoms


def configure_plotting() -> None:
    plt.rcParams.update({
        "font.family": "serif",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "legend.fontsize": 8,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


def save_fig(fig: Any, path: Path, dpi: int) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def generate_charts(
    forecasts: pd.DataFrame,
    alerts: pd.DataFrame,
    summaries: pd.DataFrame,
    annual: pd.DataFrame,
    monthly_municipality: pd.DataFrame,
    annual_municipality: pd.DataFrame,
    coverage: pd.DataFrame,
    top_risk: pd.DataFrame,
    factor_summary: pd.DataFrame,
    paths: Paths,
    dpi: int,
) -> None:
    configure_plotting()
    dates = pd.to_datetime(summaries["DATE"])

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.plot(dates, summaries["PROVINCE_POSTERIOR_MEAN_CASES"], color="#17324d", linewidth=1.25, label="Posterior mean")
    ax.fill_between(
        dates,
        summaries["PROVINCE_LOWER_CREDIBLE_CASES"],
        summaries["PROVINCE_UPPER_CREDIBLE_CASES"],
        color="#8db3c7",
        alpha=0.35,
        label="95% credible range",
    )
    ax.set(title="South Cotabato Monthly Dengue Scenario Projection", xlabel="Month", ylabel="Projected cases")
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    ax.legend(frameon=False)
    save_fig(fig, paths.charts / "south_cotabato_monthly_projection_with_bayesian_interval.png", dpi)

    fig, ax = plt.subplots(figsize=(11, 6.3))
    for municipality, group in monthly_municipality.groupby("MUNICIPALITY", sort=True):
        ax.plot(pd.to_datetime(group["DATE"]), group["POSTERIOR_MEAN_CASES"], linewidth=1.0, label=municipality)
    ax.set(title="Monthly Projected Dengue Cases by Municipality", xlabel="Month", ylabel="Projected cases")
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    ax.legend(frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.16))
    save_fig(fig, paths.charts / "monthly_projected_cases_by_municipality.png", dpi)

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.plot(dates, summaries["CALIBRATED_POSTERIOR_MEAN_CASES"], label="Calibrated South Cotabato barangays", linewidth=1.2, color="#264653")
    ax.set(title="Calibrated South Cotabato Projection", xlabel="Month", ylabel="Projected cases")
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    ax.legend(frameon=False)
    save_fig(fig, paths.charts / "calibrated_south_cotabato_projection.png", dpi)

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.stackplot(
        dates,
        summaries["MODERATE_ALERT_COUNT"], summaries["HIGH_ALERT_COUNT"], summaries["CRITICAL_ALERT_COUNT"],
        labels=["Moderate", "High", "Critical"],
        colors=[ALERT_COLORS["MODERATE"], ALERT_COLORS["HIGH"], ALERT_COLORS["CRITICAL"]],
        alpha=0.85,
    )
    ax.set(title="Monthly Barangay Risk Classification Counts", xlabel="Month", ylabel="Number of barangays")
    ax.legend(frameon=False, ncol=3)
    save_fig(fig, paths.charts / "monthly_barangay_alert_counts.png", dpi)

    fig, ax = plt.subplots(figsize=(11, 4.8))
    ax.plot(dates, summaries["MORANS_I"], color="#5f0f40", linewidth=1.2)
    ax.axhline(0, color="black", linewidth=0.7)
    ax.set(title="Global Moran's I of Monthly Projected Dengue Cases", xlabel="Month", ylabel="Moran's I")
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    save_fig(fig, paths.charts / "monthly_global_morans_i.png", dpi)

    fig, ax = plt.subplots(figsize=(11, 4.8))
    ax.plot(dates, summaries["MEAN_CREDIBLE_INTERVAL_WIDTH"], color="#005f73", linewidth=1.2)
    ax.set(title="Bayesian Uncertainty Growth across the Forecast Horizon", xlabel="Month", ylabel="Mean credible interval width")
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    save_fig(fig, paths.charts / "uncertainty_width_by_horizon.png", dpi)

    annual_pivot = annual.pivot(index="YEAR", columns="CALIBRATION_STATUS", values="POSTERIOR_MEAN_CASES").fillna(0)
    fig, ax = plt.subplots(figsize=(10.5, 5.2))
    annual_pivot.plot(kind="bar", stacked=True, ax=ax, color=["#264653", "#c49a6c"][: len(annual_pivot.columns)])
    ax.set(title="Annual Projected Dengue Cases by Data-Coverage Status", xlabel="Year", ylabel="Projected cases")
    ax.tick_params(axis="x", rotation=45)
    ax.legend(frameon=False, title="Status")
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    save_fig(fig, paths.charts / "annual_projection_by_data_coverage_status.png", dpi)

    top = top_risk.head(20).sort_values("MAX_OUTBREAK_PROBABILITY")
    labels = (top["BARANGAY"] + ", " + top["MUNICIPALITY"]).tolist()
    fig, ax = plt.subplots(figsize=(9, 7))
    ax.barh(labels, top["MAX_OUTBREAK_PROBABILITY"], color="#9b2226")
    ax.set(title="Top 20 Barangays by Maximum Outbreak Probability", xlabel="Maximum outbreak probability", ylabel="Barangay")
    ax.set_xlim(0, 1)
    ax.grid(axis="x", linestyle="--", linewidth=0.5, alpha=0.5)
    save_fig(fig, paths.charts / "top20_barangays_maximum_outbreak_probability.png", dpi)

    if not alerts.empty:
        by_municipality = alerts.groupby("MUNICIPALITY").size().sort_values()
        fig, ax = plt.subplots(figsize=(9, 5.5))
        ax.barh(by_municipality.index, by_municipality.values, color="#bb3e03")
        ax.set(title="Automatic Outbreak Alert-Months by Municipality", xlabel="Alert-month records", ylabel="Municipality")
        ax.grid(axis="x", linestyle="--", linewidth=0.5, alpha=0.5)
        save_fig(fig, paths.charts / "automatic_alerts_by_municipality.png", dpi)

    coverage_plot = coverage.sort_values("TOTAL_BARANGAYS")
    fig, ax = plt.subplots(figsize=(9, 5.8))
    ax.barh(coverage_plot["MUNICIPALITY"], coverage_plot["CALIBRATED_BARANGAYS"], label="Calibrated", color="#264653")
    ax.set(title="Calibrated Barangays by Municipality", xlabel="Number of barangays", ylabel="Municipality")
    ax.legend(frameon=False)
    ax.grid(axis="x", linestyle="--", linewidth=0.5, alpha=0.5)
    save_fig(fig, paths.charts / "barangay_data_coverage_by_municipality.png", dpi)

    heat = annual_municipality.pivot(index="MUNICIPALITY", columns="YEAR", values="MEAN_OUTBREAK_PROBABILITY").fillna(0)
    fig, ax = plt.subplots(figsize=(12, 5.8))
    image = ax.imshow(heat.to_numpy(), aspect="auto", vmin=0, vmax=1, cmap="YlOrRd")
    ax.set_xticks(range(len(heat.columns)), labels=[str(value) for value in heat.columns], rotation=45, ha="right")
    ax.set_yticks(range(len(heat.index)), labels=heat.index)
    ax.set_title("Annual Mean Outbreak Probability by Municipality")
    ax.set_xlabel("Year")
    ax.set_ylabel("Municipality")
    colorbar = fig.colorbar(image, ax=ax, fraction=0.025, pad=0.02)
    colorbar.set_label("Mean outbreak probability")
    save_fig(fig, paths.charts / "municipality_year_outbreak_probability_heatmap.png", dpi)


    fig, ax = plt.subplots(figsize=(11, 4.8))
    ax.plot(dates, summaries["NEW_AUTOMATIC_OUTBREAK_ONSET_COUNT"], color="#c1121f", linewidth=1.2, label="New automatic alert starts")
    ax.plot(dates, summaries["AUTOMATIC_OUTBREAK_ALERT_COUNT"], color="#f4a261", linewidth=1.0, label="All automatic alerts")
    ax.plot(dates, summaries["RED_CLUSTER_OUTBREAK_ALERT_COUNT"], color="#7c3aed", linewidth=1.0, label="Members of 3+ connected red clusters")
    ax.plot(dates, summaries["INDIVIDUAL_OUTBREAK_ALERT_COUNT"], color="#2563eb", linewidth=0.9, label="Individual barangay alerts")
    ax.set(title="Monthly Automatic Outbreak Alerts and Spatial Red Clusters", xlabel="Month", ylabel="Number of barangays")
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    ax.legend(frameon=False)
    save_fig(fig, paths.charts / "monthly_outbreak_onsets_and_automatic_alerts.png", dpi)

    if not factor_summary.empty:
        factor_plot = factor_summary.sort_values("MEAN_FACTOR_SHARE")
        fig, ax = plt.subplots(figsize=(9.5, 5.8))
        ax.barh(factor_plot["OUTBREAK_FACTOR"], factor_plot["MEAN_FACTOR_SHARE"] * 100.0, color="#315f7d")
        ax.set(title="Average Model-Component Contribution across the Forecast Horizon", xlabel="Mean contribution share (%)", ylabel="Model pressure")
        ax.grid(axis="x", linestyle="--", linewidth=0.5, alpha=0.5)
        save_fig(fig, paths.charts / "average_outbreak_factor_contribution.png", dpi)



def render_snapshot_map(
    date: str,
    forecasts: pd.DataFrame,
    master: pd.DataFrame,
    geometries: list[Any],
    output: Path,
    dpi: int,
) -> None:
    frame = forecasts[forecasts["DATE"] == date].set_index("NODE_ID")
    if frame.empty:
        return
    values = frame["OUTBREAK_PROBABILITY"].reindex(master["NODE_ID"]).to_numpy(dtype=float)
    patches: list[MplPolygon] = []
    patch_values: list[float] = []
    for node, geometry in enumerate(geometries):
        for part in polygon_parts(geometry):
            patches.append(MplPolygon(np.asarray(part.exterior.coords), closed=True))
            patch_values.append(values[node])
    _, municipality_geometries, _ = build_municipality_geometries(master, geometries)
    province_geometry = unary_union(municipality_geometries)
    fig, ax = plt.subplots(figsize=(8.5, 9))
    collection = PatchCollection(patches, cmap="YlOrRd", edgecolor="#6b7280", linewidth=0.18)
    collection.set_array(np.asarray(patch_values))
    collection.set_clim(0, 1)
    ax.add_collection(collection)
    for geometry in municipality_geometries:
        for part in polygon_parts(geometry):
            coords = np.asarray(part.exterior.coords)
            ax.plot(coords[:, 0], coords[:, 1], color="#111111", linewidth=0.95, zorder=5)
    for part in polygon_parts(province_geometry):
        coords = np.asarray(part.exterior.coords)
        ax.plot(coords[:, 0], coords[:, 1], color="#000000", linewidth=1.7, zorder=6)
    ax.autoscale_view()
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    ax.set_title(f"South Cotabato Barangay Outbreak Probability — {date[:7]}")
    colorbar = fig.colorbar(collection, ax=ax, fraction=0.032, pad=0.01)
    colorbar.set_label("Outbreak probability")
    save_fig(fig, output, dpi)


def slim_geojson(geojson: dict[str, Any], master: pd.DataFrame) -> dict[str, Any]:
    rows = master.set_index("NODE_ID")
    features = []
    for index, feature in enumerate(geojson["features"]):
        row = rows.loc[index]
        features.append({
            "type": "Feature",
            "id": index,
            "properties": {
                "node": int(index),
                "psgc": str(row["PSGC"]),
                "barangay": str(row["BARANGAY"]),
                "municipality": str(row["MUNICIPALITY"]),
                "municipality_code": str(row["MUNICIPALITY_CODE"]),
                "province": str(row["PROVINCE"]),
                "status": str(row["CALIBRATION_STATUS"]),
                "gap_fill_km2": float(feature.get("properties", {}).get("ORACLIS_GAP_FILLED_AREA_KM2", 0.0)),
                "search": f"{row['BARANGAY']} {row['MUNICIPALITY']}".upper(),
            },
            "geometry": feature["geometry"],
        })
    return {"type": "FeatureCollection", "features": features}


def write_qgis_snapshot_geojson(
    geojson: dict[str, Any],
    master: pd.DataFrame,
    forecasts: pd.DataFrame,
    date: str,
    output: Path,
) -> None:
    frame = forecasts[forecasts["DATE"] == date].set_index("NODE_ID")
    if frame.empty:
        return
    features: list[dict[str, Any]] = []
    for node, feature in enumerate(geojson["features"]):
        row = frame.loc[node]
        base = master.iloc[node]
        features.append({
            "type": "Feature",
            "id": node,
            "properties": {
                "PSGC": base["PSGC"],
                "PROVINCE": base["PROVINCE"],
                "PROVINCE_CODE": base["PROVINCE_CODE"],
                "MUNICIPALITY": base["MUNICIPALITY"],
                "MUNICIPALITY_CODE": base["MUNICIPALITY_CODE"],
                "BARANGAY": base["BARANGAY"],
                "CALIBRATION_STATUS": base["CALIBRATION_STATUS"],
                "DATE": date,
                "POSTERIOR_MEAN_CASES": round(float(row["POSTERIOR_MEAN_CASES"]), 6),
                "LOWER_CREDIBLE_CASES": round(float(row["LOWER_CREDIBLE_CASES"]), 6),
                "UPPER_CREDIBLE_CASES": round(float(row["UPPER_CREDIBLE_CASES"]), 6),
                "OUTBREAK_PROBABILITY": round(float(row["OUTBREAK_PROBABILITY"]), 6),
                "ALERT_LEVEL": str(row["ALERT_LEVEL"]),
                "AUTOMATIC_OUTBREAK_ALERT": bool(row["AUTOMATIC_OUTBREAK_ALERT"]),
                "OUTBREAK_ALERT_REASON": str(row["OUTBREAK_ALERT_REASON"]),
                "RED_CASE_ZONE": bool(row["RED_CASE_ZONE"]),
                "RED_CASE_NEIGHBOR_COUNT": int(row["RED_CASE_NEIGHBOR_COUNT"]),
                "RED_CLUSTER_OUTBREAK_ALERT": bool(row["RED_CLUSTER_OUTBREAK_ALERT"]),
                "RED_CLUSTER_OUTBREAK_ONSET": bool(row["RED_CLUSTER_OUTBREAK_ONSET"]),
                "RED_CLUSTER_LIKELY_ORIGIN": bool(row["RED_CLUSTER_LIKELY_ORIGIN"]),
                "RED_CLUSTER_ID": int(row["RED_CLUSTER_ID"]),
                "RED_CLUSTER_SIZE": int(row["RED_CLUSTER_SIZE"]),
                "INDIVIDUAL_OUTBREAK_ALERT": bool(row["INDIVIDUAL_OUTBREAK_ALERT"]),
                "PROBABILISTIC_OUTBREAK_ALERT": bool(row["PROBABILISTIC_OUTBREAK_ALERT"]),
                "OUTBREAK_ALERT_STATE": str(row["OUTBREAK_ALERT_STATE"]),
                "HOTSPOT_Z_SCORE": round(float(row["HOTSPOT_Z_SCORE"]), 6),
            },
            "geometry": feature["geometry"],
        })
    write_json(output, {"type": "FeatureCollection", "features": features}, compact=True)


def generate_interactive_map(
    geojson: dict[str, Any],
    municipality_geojson: dict[str, Any],
    province_geojson: dict[str, Any],
    master: pd.DataFrame,
    forecasts: pd.DataFrame,
    summaries: pd.DataFrame,
    monthly_municipality: pd.DataFrame,
    output: Path,
    playback_interval_ms: int = 3600,
) -> None:

    factor_columns = [
        "FACTOR_SEASONAL_BASELINE_SHARE",
        "FACTOR_LONG_TERM_TREND_SHARE",
        "FACTOR_RECENT_CASE_PERSISTENCE_SHARE",
        "FACTOR_NEIGHBOUR_SPILLOVER_SHARE",
        "FACTOR_HIGH_RISK_CLUSTER_SHARE",
        "FACTOR_BAYESIAN_EVIDENCE_SHARE",
    ]
    value_specs: dict[str, tuple[str, int | None, str]] = {
        "mean": ("POSTERIOR_MEAN_CASES", 3, "float"),
        "median": ("POSTERIOR_MEDIAN_CASES", 3, "float"),
        "lower": ("LOWER_CREDIBLE_CASES", 3, "float"),
        "upper": ("UPPER_CREDIBLE_CASES", 3, "float"),
        "prob": ("OUTBREAK_PROBABILITY", 4, "float"),
        "alert": ("ALERT_LEVEL", None, "alert"),
        "auto": ("AUTOMATIC_OUTBREAK_ALERT", None, "bool"),
        "onset": ("OUTBREAK_ONSET_PING", None, "bool"),
        "highRiskOnset": ("HIGH_RISK_ONSET", None, "bool"),
        "individualAuto": ("INDIVIDUAL_OUTBREAK_ALERT", None, "bool"),
        "probabilisticAuto": ("PROBABILISTIC_OUTBREAK_ALERT", None, "bool"),
        "redZone": ("RED_CASE_ZONE", None, "bool"),
        "redNeighbors": ("RED_CASE_NEIGHBOR_COUNT", None, "int"),
        "caseIntensity": ("CASE_COLOR_INTENSITY", 4, "float"),
        "redCluster": ("RED_CLUSTER_OUTBREAK_ALERT", None, "bool"),
        "redClusterOnset": ("RED_CLUSTER_OUTBREAK_ONSET", None, "bool"),
        "redClusterOrigin": ("RED_CLUSTER_LIKELY_ORIGIN", None, "bool"),
        "redClusterId": ("RED_CLUSTER_ID", None, "int"),
        "redClusterSize": ("RED_CLUSTER_SIZE", None, "int"),
        "outbreakState": ("OUTBREAK_ALERT_STATE", None, "string"),
        "alertReason": ("OUTBREAK_ALERT_REASON", None, "string"),
        "hotspot": ("HOTSPOT_Z_SCORE", 3, "float"),
        "spatial": ("SPATIAL_LAG_CASES", 3, "float"),
        "neighbors": ("HIGH_RISK_NEIGHBOR_COUNT", None, "int"),
        "highShare": ("HIGH_RISK_NEIGHBOR_SHARE", 4, "float"),
        "clusterFlag": ("CLUSTER_ALERT", None, "bool"),
        "persistentFlag": ("PERSISTENT_ALERT", None, "bool"),
        "factorDominant": ("DOMINANT_OUTBREAK_FACTOR_CODE", None, "int"),
        "factorSeasonal": (factor_columns[0], 4, "float"),
        "factorTrend": (factor_columns[1], 4, "float"),
        "factorTemporal": (factor_columns[2], 4, "float"),
        "factorSpatial": (factor_columns[3], 4, "float"),
        "factorCluster": (factor_columns[4], 4, "float"),
        "factorBayesian": (factor_columns[5], 4, "float"),
        "seasonal": ("SEASONAL_CLIMATOLOGY_CASES", 3, "float"),
        "growth": ("PROJECTED_GROWTH_MULTIPLIER", 4, "float"),
        "baseline": ("BASELINE_CASES", 3, "float"),
        "trendUplift": ("TREND_UPLIFT_CASES", 3, "float"),
        "lag1": ("TEMPORAL_LAG_1_CASES", 3, "float"),
        "lag3": ("TEMPORAL_LAG_3_CASES", 3, "float"),
        "lag6": ("TEMPORAL_LAG_6_CASES", 3, "float"),
        "lag12": ("TEMPORAL_LAG_12_CASES", 3, "float"),
        "roll3": ("ROLLING_MEAN_3_MONTHS", 3, "float"),
        "roll6": ("ROLLING_MEAN_6_MONTHS", 3, "float"),
        "roll12": ("ROLLING_MEAN_12_MONTHS", 3, "float"),
        "rollStd3": ("ROLLING_STD_3_MONTHS", 3, "float"),
        "temporalMemory": ("TEMPORAL_MEMORY_CASES", 3, "float"),
        "temporalAdjusted": ("TEMPORAL_ADJUSTED_CASES", 3, "float"),
        "temporalUplift": ("TEMPORAL_MEMORY_UPLIFT_CASES", 3, "float"),
        "spatialUplift": ("SPATIAL_SPILLOVER_UPLIFT_CASES", 3, "float"),
        "clusterUplift": ("NEIGHBOR_CLUSTER_UPLIFT_CASES", 3, "float"),
        "prior": ("PRIOR_MEAN_CASES", 3, "float"),
        "bayesianDelta": ("BAYESIAN_REFINEMENT_DELTA_CASES", 3, "float"),
        "threshold": ("OUTBREAK_CASE_THRESHOLD", 3, "float"),
        "observedApplied": ("BAYESIAN_OBSERVATION_APPLIED", None, "bool"),
        "observedCases": ("OBSERVED_CASES_UPDATE", 3, "nullable_float"),
        "horizonMonth": ("HORIZON_MONTH", None, "int"),
        "horizonYear": ("HORIZON_YEAR", 3, "float"),
    }
    required_fields = ["DATE", "NODE_ID", *[spec[0] for spec in value_specs.values()]]
    table = forecasts[required_fields].copy()
    dates = table["DATE"].drop_duplicates().tolist()
    node_count = len(master)
    date_count = len(dates)
    date_index = {date: i for i, date in enumerate(dates)}
    arrays: dict[str, list[list[Any]]] = {
        key: [[None] * node_count for _ in range(date_count)] for key in value_specs
    }

    def converted(value: Any, decimals: int | None, kind: str) -> Any:
        if kind == "alert":
            return ALERT_CODE[str(value)]
        if kind == "string":
            return str(value)
        if kind == "bool":
            return int(bool(value))
        if kind == "int":
            return int(value)
        numeric = float(value)
        if not np.isfinite(numeric):
            return None if kind == "nullable_float" else 0.0
        return round(numeric, int(decimals or 0))

    for row in table.itertuples(index=False):
        row_dict = row._asdict()
        di = date_index[row_dict["DATE"]]
        ni = int(row_dict["NODE_ID"])
        for key, (column, decimals, kind) in value_specs.items():
            arrays[key][di][ni] = converted(row_dict[column], decimals, kind)

    centroids: list[list[float]] = []
    for feature in geojson["features"]:
        point = shape(feature["geometry"]).representative_point()
        centroids.append([round(float(point.y), 6), round(float(point.x), 6)])

    outside_mask = build_outside_province_mask(province_geojson)
    payload = {
        "dates": dates,
        "geojson": slim_geojson(geojson, master),
        "municipalityGeojson": municipality_geojson,
        "provinceGeojson": province_geojson,
        "outsideMask": outside_mask,
        "centroids": centroids,
        "values": arrays,
        "factorLabels": OUTBREAK_FACTOR_LABELS,
        "summary": summaries.to_dict(orient="records"),
        "municipalityMonthly": monthly_municipality.to_dict(orient="records"),
        "playbackIntervalMs": max(1800, int(playback_interval_ms)),
        "warning": (
            "Long-range scenario projection through 2050. Monthly dengue targets were interpolated "
            "from annual totals. All South Cotabato barangays are marked as calibrated in this system."
        ),
    }
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    html_text = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ORACLIS South Cotabato Live Dengue Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
<style>
:root{--navy:#102a43;--panel:rgba(255,255,255,.97);--border:#cbd5e1;--text:#14213d;--muted:#667085;--danger:#ff0015;--shadow:0 8px 28px rgba(15,23,42,.24)}
*{box-sizing:border-box}html,body,#map{height:100%;width:100%;margin:0}body{overflow:hidden;font-family:Inter,Arial,sans-serif;color:var(--text);background:#88939c}#map{position:fixed;inset:0;background:#8d98a1}.leaflet-container{font-family:Inter,Arial,sans-serif}.leaflet-tile-pane{filter:grayscale(.82) saturate(.18) brightness(.76) contrast(.92)}
.leaflet-control-zoom{margin-top:82px!important;border:none!important;box-shadow:var(--shadow)!important}.leaflet-control-zoom a{border:none!important;color:#102a43!important}
.floating{position:fixed;z-index:1000;background:var(--panel);border:1px solid rgba(255,255,255,.7);box-shadow:var(--shadow);backdrop-filter:blur(8px)}
.title-card{left:14px;top:14px;max-width:430px;border-radius:12px;padding:10px 13px}.title-card h1{font-size:17px;margin:0}.title-card p{font-size:11px;color:var(--muted);margin:3px 0 0;line-height:1.3}.scenario-pill{display:inline-block;margin-top:6px;padding:3px 7px;background:#fff7ed;border:1px solid #fdba74;border-radius:999px;font-size:10px;color:#9a3412}
.button-dock{top:14px;right:14px;border-radius:13px;padding:6px;display:flex;gap:6px}.float-btn{border:0;border-radius:9px;padding:9px 11px;background:white;color:#102a43;font:700 12px Inter,Arial;cursor:pointer;box-shadow:0 1px 4px rgba(15,23,42,.15)}.float-btn.primary{background:#102a43;color:white}.float-btn.alerting{background:#ff0015;color:white}.float-btn:hover{transform:translateY(-1px)}
.settings{right:14px;top:68px;width:310px;border-radius:12px;padding:11px;display:none}.settings.open{display:block}.settings label{display:block;font-size:11px;color:var(--muted);margin-bottom:8px}.settings select,.settings input{width:100%;padding:8px;border:1px solid var(--border);border-radius:7px;background:white;font:12px Inter,Arial;margin-top:3px}
.timeline{left:50%;bottom:16px;transform:translateX(-50%);width:min(760px,calc(100vw - 34px));border-radius:14px;padding:9px 12px}.timeline-top{display:flex;align-items:center;gap:10px;font-size:11px}.timeline strong{font-size:14px;color:#102a43}.timeline input{width:100%;accent-color:#dc2626}.timeline-status{margin-left:auto;color:var(--muted);white-space:nowrap}
.legend{left:14px;bottom:16px;border-radius:12px;padding:9px 11px;font-size:10px;width:235px}.gradient{height:12px;border-radius:5px;background:linear-gradient(90deg,#10b981 0%,#fde047 50%,#ef4444 100%);border:1px solid rgba(255,255,255,.8)}.legend-labels{display:flex;justify-content:space-between;margin-top:3px}.legend-extra{margin-top:6px;line-height:1.45;color:#475467}.legend-line{display:inline-block;width:23px;border-top:3px solid white;filter:drop-shadow(0 0 1px #111);vertical-align:middle}.legend-ping{display:inline-block;width:12px;height:12px;background:#ff0015;border-radius:50%;box-shadow:0 0 0 5px rgba(255,0,21,.25);vertical-align:middle}
.drawer{position:fixed;z-index:1100;right:0;top:0;width:min(590px,94vw);height:100%;background:#f8fafc;box-shadow:-10px 0 32px rgba(15,23,42,.28);transform:translateX(102%);transition:transform .26s ease;display:flex;flex-direction:column}.drawer.open{transform:translateX(0)}.drawer-head{background:#102a43;color:white;padding:13px 15px;display:flex;align-items:center;gap:10px}.drawer-head h2{font-size:17px;margin:0;flex:1}.drawer-head button{border:0;background:white;color:#102a43;border-radius:7px;padding:6px 9px;cursor:pointer}.tabs{display:flex;gap:4px;padding:8px;background:white;border-bottom:1px solid var(--border);overflow-x:auto}.tab-btn{border:1px solid var(--border);background:white;border-radius:7px;padding:7px 10px;font:700 11px Inter;white-space:nowrap;cursor:pointer}.tab-btn.active{background:#102a43;color:white}.tab-panel{display:none;overflow:auto;padding:12px;flex:1}.tab-panel.active{display:block}.card{background:white;border:1px solid var(--border);border-radius:10px;padding:11px;margin-bottom:10px}.card h3{font-size:14px;margin:0 0 8px}.small{font-size:11px;color:var(--muted);line-height:1.4}.metric-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px}.metric{border:1px solid #e2e8f0;border-radius:8px;padding:8px}.metric span{display:block;font-size:10px;color:var(--muted)}.metric b{font-size:15px;font-variant-numeric:tabular-nums}.alert-row{border-left:5px solid #ff0015;background:#fff1f2;border-radius:7px;padding:8px;margin:6px 0;font-size:11px;cursor:pointer}.selected-card{line-height:1.55}.factor-note{background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:9px;font-size:11px;line-height:1.45}
.chart{width:100%;height:230px;border:1px solid #eef2f6;border-radius:7px}.chart text{font:10px Inter,Arial;fill:#475467}.axis{stroke:#94a3b8}.grid{stroke:#e2e8f0}.chart-line{fill:none;stroke-width:2}.cursor{stroke:#0f172a;stroke-dasharray:4 3}.credible{fill:#93c5fd;opacity:.3}.factor-bar{fill:#2563eb}.factor-bar.top{fill:#ef4444}
.ranking{max-height:560px;overflow:auto}.rank-row{display:grid;grid-template-columns:36px minmax(155px,1fr) 74px;gap:7px;align-items:center;padding:6px;border-bottom:1px solid #edf2f7;cursor:pointer;font-size:11px}.rank-row:hover{background:#f1f5f9}.rank-number{font-weight:800}.rank-name b{display:block}.rank-name span{color:var(--muted);font-size:9px}.rank-value{text-align:right;font-variant-numeric:tabular-nums}.rank-bar-wrap{grid-column:2/4;height:6px;background:#edf2f7;border-radius:999px;overflow:hidden}.rank-bar{height:100%;border-radius:999px}.rank-row.outbreak{background:#fff1f2;box-shadow:inset 4px 0 #ff0015}
.table-tools{position:sticky;top:-12px;background:#f8fafc;padding:0 0 8px;z-index:2}.table-tools input{width:100%;padding:8px;border:1px solid var(--border);border-radius:7px}.table-wrap{overflow:auto;max-height:calc(100vh - 160px);border:1px solid var(--border);background:white}.live-table{border-collapse:separate;border-spacing:0;font-size:9px;white-space:nowrap}.live-table th,.live-table td{padding:5px 6px;border-right:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;text-align:right}.live-table th{position:sticky;top:0;background:#102a43;color:white;z-index:1}.live-table th:nth-child(-n+4),.live-table td:nth-child(-n+4){text-align:left}.live-table tr{cursor:pointer}.live-table tbody tr:hover{background:#eff6ff}.live-table tbody tr.outbreak{background:#fff1f2;font-weight:700}.live-table td:first-child,.live-table th:first-child{position:sticky;left:0;background:inherit;z-index:1}.live-table th:first-child{background:#102a43}.status-high{color:#dc2626;font-weight:800}
.barangay-label-icon{background:transparent!important;border:none!important;pointer-events:none!important}.barangay-label-icon span{position:absolute;transform:translate(-50%,-50%);white-space:nowrap;font:700 var(--barangay-label-size,8px)/1 Arial;color:#fff;text-shadow:-1px -1px 2px #111,1px -1px 2px #111,-1px 1px 2px #111,1px 1px 2px #111;opacity:.95}.labels-hidden .barangay-label-icon{display:none!important}
.outbreak-ping-icon{background:transparent!important;border:none!important}.ping-core{width:15px;height:15px;border-radius:50%;background:#ff0015;border:2px solid white;box-shadow:0 0 0 1px #7f1d1d;position:relative}.ping-core:before,.ping-core:after{content:'';position:absolute;inset:-7px;border:3px solid rgba(255,0,21,.72);border-radius:50%;animation:ping 1.5s infinite}.ping-core:after{animation-delay:.75s}.ping-core.onset{width:20px;height:20px}.ping-core.onset:before,.ping-core.onset:after{inset:-10px}.ping-core.origin{width:25px;height:25px;background:#b80012;border-width:3px;box-shadow:0 0 0 3px rgba(255,255,255,.85),0 0 18px 6px rgba(255,0,21,.8)}.ping-core.origin:before,.ping-core.origin:after{inset:-13px;border-width:4px}.ping-core.cluster-member{background:#ff2a3d}@keyframes ping{0%{transform:scale(.35);opacity:1}100%{transform:scale(1.8);opacity:0}}
.leaflet-popup-content{margin:11px 13px;min-width:295px}.popup-title{font-weight:800;font-size:14px}.popup-sub{font-size:10px;color:#667085;margin-bottom:6px}.popup-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px;font-size:10px}.popup-grid div{border:1px solid #e2e8f0;border-radius:5px;padding:5px}.popup-grid b{display:block;font-size:12px}.popup-alert{background:#ff0015;color:white;padding:5px;border-radius:6px;text-align:center;font-weight:800;margin:6px 0}.spark{width:100%;height:82px;background:#f8fafc;border-radius:6px;margin:7px 0}.popup-button{width:100%;border:0;background:#102a43;color:white;padding:7px;border-radius:6px;cursor:pointer}
@media(max-width:900px){.title-card{max-width:280px}.title-card p{display:none}.legend{display:none}.timeline{bottom:10px}.button-dock{top:10px;right:10px}.float-btn{padding:8px}.settings{right:10px}.metric-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div id="map"></div>
<div class="floating title-card"><h1>ORACLIS · South Cotabato Dengue Scenario Map</h1><p>Gap-filled real barangay boundaries, live Bayesian projections, spatial spillover, outbreak alerts, and monthly simulation through 2050.</p><span class="scenario-pill">Scenario projection · not clinical surveillance</span></div>
<div class="floating button-dock">
<button class="float-btn primary" id="play" title="Play or pause">▶ Play</button>
<button class="float-btn" id="analytics" title="Open analytics">▤ Analytics</button>
<button class="float-btn" id="settingsButton" title="Map settings">⚙ Controls</button>
<button class="float-btn" id="labels" title="Toggle barangay labels">Aa Labels</button>
<button class="float-btn" id="reset" title="Reset map extent">⌂ Reset</button>
</div>
<div class="floating settings" id="settingsPanel">
<label>Displayed map metric<select id="metric"><option value="mean" selected>Projected dengue cases</option><option value="prob">Outbreak probability</option><option value="upper">Upper credible cases</option><option value="hotspot">Hotspot z-score</option><option value="spatial">Neighbour spatial lag</option></select></label>
<label>Playback speed<select id="speed"><option value="6500">Very slow · 6.5 s/month</option><option value="4500">Slow · 4.5 s/month</option><option value="3600" selected>Normal · 3.6 s/month</option><option value="2200">Fast · 2.2 s/month</option></select></label>
<label>Find barangay<input id="search" placeholder="Type barangay or municipality"></label>
</div>
<div class="floating timeline"><div class="timeline-top"><strong id="dateLabel"></strong><span id="mapMetricLabel">Projected dengue cases</span><span class="timeline-status" id="mapStatus"></span></div><input id="slider" type="range" min="0" max="__MAX_INDEX__" value="0"></div>
<div class="floating legend"><b id="legendTitle">Projected dengue cases</b><div class="gradient"></div><div class="legend-labels"><span id="legendLow">Low</span><span id="legendMid">Mid</span><span id="legendHigh">High</span></div><div class="legend-extra"><span class="legend-line"></span> white lines = administrative boundaries<br><span class="legend-ping"></span> clickable outbreak alert; large ping = likely cluster origin<br>3+ truly adjacent red barangays = spatial cluster outbreak</div></div>
<div class="drawer" id="drawer">
<div class="drawer-head"><h2>Live Analytics · <span id="drawerDate"></span></h2><button id="closeDrawer">Close</button></div>
<div class="tabs"><button class="tab-btn active" data-tab="overview">Overview</button><button class="tab-btn" data-tab="graphs">Graphs & ranking</button><button class="tab-btn" data-tab="table">Live parameter table</button></div>
<section class="tab-panel active" id="tab-overview">
<div class="card"><h3>Province summary</h3><div class="metric-grid" id="summary"></div></div>
<div class="card"><h3>Selected barangay</h3><div id="selectedCard" class="selected-card small">Click any barangay polygon or ranking row.</div></div>
<div class="card"><h3>Outbreak alerts</h3><div id="alerts"></div></div>
<div class="card"><h3>How automatic alerts are triggered</h3><div class="small"><b>Rule 1 · Probabilistic:</b> a barangay reaches High or Critical outbreak probability and either remains high for the configured persistence period or has enough High/Critical neighbours.<br><br><b>Rule 2 · Spatial red cluster:</b> at least three directly connected barangays simultaneously enter the red end of the projected-case gradient. Every member is highlighted, pinged, and recorded as an automatic outbreak alert. This is an early-warning scenario rule, not a clinical declaration.</div></div>
<div class="card"><h3>Outbreak factor interpretation</h3><svg id="factorContributions" class="chart"></svg><div id="factorNarrative" class="factor-note"></div></div>
</section>
<section class="tab-panel" id="tab-graphs">
<div class="card"><h3>Province projected cases and 95% credible interval</h3><svg id="provinceTrend" class="chart"></svg></div>
<div class="card"><h3>Selected barangay live timeline</h3><svg id="selectedTrend" class="chart"></svg></div>
<div class="card"><h3>Municipality projected cases</h3><svg id="municipalityBars" class="chart"></svg></div>
<div class="card"><h3>Risk distribution</h3><svg id="riskDistribution" class="chart"></svg></div>
<div class="card"><h3>New outbreak starts and automatic alerts</h3><svg id="onsetTrend" class="chart"></svg></div>
<div class="card"><h3>Live dengue cases ranking · all barangays</h3><div class="small">Updates every forecast month. Automatic outbreak alerts are shown in bright red.</div><div id="ranking" class="ranking"></div></div>
</section>
<section class="tab-panel" id="tab-table">
<div class="table-tools"><input id="tableSearch" placeholder="Filter barangay, municipality, risk level, PSGC, or factor"></div>
<div class="table-wrap"><table class="live-table"><thead id="liveHead"></thead><tbody id="liveBody"></tbody></table></div>
</section>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
const DATA=__PAYLOAD__;
if(typeof L==='undefined'){document.body.innerHTML='<div style="padding:30px;font-family:Arial">Leaflet could not load. Connect to the internet and reopen the generated map.</div>';throw new Error('Leaflet unavailable')}
const ALERTS=['LOW','MODERATE','HIGH','CRITICAL'];
let current=0,selectedNode=0,timer=null,labelsVisible=true,searchQuery='',activeTab='overview';
const map=L.map('map',{zoomControl:true,preferCanvas:true,minZoom:8,maxZoom:17});
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(map);
['outsideMaskPane','barangayPane','municipalityPane','provincePane','labelPane','pingPane'].forEach(name=>map.createPane(name));
map.getPane('outsideMaskPane').style.zIndex=350;map.getPane('barangayPane').style.zIndex=430;map.getPane('municipalityPane').style.zIndex=470;map.getPane('provincePane').style.zIndex=490;map.getPane('labelPane').style.zIndex=610;map.getPane('pingPane').style.zIndex=650;map.getPane('labelPane').style.pointerEvents='none';
L.geoJSON(DATA.outsideMask,{pane:'outsideMaskPane',interactive:false,style:{fillColor:'#4b5563',fillOpacity:.72,color:'#4b5563',weight:0}}).addTo(map);
const pingLayer=L.layerGroup().addTo(map),labelLayer=L.layerGroup().addTo(map),barangayLayers=[];
function esc(v){return String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function clamp(v,a,b){return Math.max(a,Math.min(b,v))}
function lerp(a,b,t){return Math.round(a+(b-a)*t)}
function hexRgb(h){return [parseInt(h.slice(1,3),16),parseInt(h.slice(3,5),16),parseInt(h.slice(5,7),16)]}
function mix(c1,c2,t){const a=hexRgb(c1),b=hexRgb(c2);return '#'+a.map((v,i)=>lerp(v,b[i],t).toString(16).padStart(2,'0')).join('')}
function gradientColor(v,lo,hi){if(!Number.isFinite(v))return '#94a3b8';if(hi<=lo)return '#fde047';const t=clamp((v-lo)/(hi-lo),0,1);return t<=.5?mix('#10b981','#fde047',t*2):mix('#fde047','#ef4444',(t-.5)*2)}
function quantile(values,q){const s=values.filter(Number.isFinite).slice().sort((a,b)=>a-b);if(!s.length)return 0;const p=(s.length-1)*q,i=Math.floor(p),f=p-i;return s[i]+(s[Math.min(i+1,s.length-1)]-s[i])*f}
function alertName(code){return ALERTS[code]||'LOW'}
function metricInfo(){const key=document.getElementById('metric').value;return {mean:['Projected dengue cases','cases'],prob:['Outbreak probability','%'],upper:['Upper credible cases','cases'],hotspot:['Hotspot z-score','z'],spatial:['Neighbour spatial lag','cases']}[key]}
function metricValues(){return DATA.values[document.getElementById('metric').value][current]}
function metricScale(){const vals=metricValues().filter(Number.isFinite);let lo=quantile(vals,.02),hi=quantile(vals,.98);if(document.getElementById('metric').value==='prob'){lo=0;hi=1}if(hi<=lo)hi=lo+1;return [lo,hi,(lo+hi)/2]}
function fmt(v,d=2){return v==null||!Number.isFinite(Number(v))?'—':Number(v).toFixed(d)}
function factorShares(i){return [DATA.values.factorSeasonal[current][i],DATA.values.factorTrend[current][i],DATA.values.factorTemporal[current][i],DATA.values.factorSpatial[current][i],DATA.values.factorCluster[current][i],DATA.values.factorBayesian[current][i]]}
function sparklineSvg(values,index,w=300,h=82){const clean=values.map(v=>Number(v)||0),max=Math.max(...clean,1),min=Math.min(...clean,0),span=Math.max(max-min,1e-8),m=5;const pts=clean.map((v,i)=>`${m+i/Math.max(clean.length-1,1)*(w-2*m)},${h-m-(v-min)/span*(h-2*m)}`).join(' ');const cx=m+index/Math.max(clean.length-1,1)*(w-2*m),cy=h-m-(clean[index]-min)/span*(h-2*m);return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="#2563eb" stroke-width="2"/><line x1="${cx}" x2="${cx}" y1="4" y2="${h-4}" stroke="#ef4444" stroke-dasharray="3 2"/><circle cx="${cx}" cy="${cy}" r="4" fill="#ef4444" stroke="white" stroke-width="2"/></svg>`}
function alertReasonLabel(code){if(!code||code==='NONE')return 'None';const labels={INDIVIDUAL_BARANGAY:'Individual barangay threshold/probability',PROBABILISTIC_SPATIOTEMPORAL:'Probabilistic persistence/neighbour rule',THREE_CONNECTED_RED_BARANGAYS:'3+ truly adjacent red barangays'};return String(code).split('+').map(x=>labels[x]||x).join(' + ')}
function popupHtml(i){const p=DATA.geojson.features[i].properties,v=DATA.values,alert=alertName(v.alert[current][i]),factor=DATA.factorLabels[v.factorDominant[current][i]],isAlert=!!v.auto[current][i],reason=alertReasonLabel(v.alertReason[current][i]),origin=!!v.redClusterOrigin[current][i],state=v.outbreakState[current][i];return `<div class="popup-title">${esc(p.barangay)}</div><div class="popup-sub">${esc(p.municipality)} · PSGC ${esc(p.psgc)}</div>${isAlert?`<div class="popup-alert">${origin?'LIKELY CLUSTER ORIGIN · ':''}DENGUE OUTBREAK ALERT</div>`:''}${sparklineSvg(v.mean.map(r=>r[i]),current)}<div class="popup-grid"><div>Projected cases<b>${fmt(v.mean[current][i])}</b></div><div>Outbreak chance<b>${fmt(v.prob[current][i]*100,1)}%</b></div><div>95% credible range<b>${fmt(v.lower[current][i],1)}–${fmt(v.upper[current][i],1)}</b></div><div>Operational state<b>${esc(state.replaceAll('_',' '))}</b></div><div>Red cluster size<b>${v.redClusterSize[current][i]||0}</b></div><div>Adjacent red barangays<b>${v.redNeighbors[current][i]||0}</b></div></div><div class="small" style="margin:6px 0">Alert rule: <b>${esc(reason)}</b><br>Bayesian probability level: <b>${alert}</b><br>Leading modeled pressure: <b>${esc(factor)}</b><br>Gap allocated to this boundary: ${fmt(p.gap_fill_km2,3)} km²</div><button class="popup-button" onclick="openAnalyticsFor(${i})">Open full live details</button>`}
function openAnalyticsFor(i){selectNode(i,true);openDrawer('overview')}
window.openAnalyticsFor=openAnalyticsFor;
function selectNode(i,zoom=false){selectedNode=i;const p=DATA.geojson.features[i].properties,v=DATA.values,factor=DATA.factorLabels[v.factorDominant[current][i]],alert=alertName(v.alert[current][i]),reason=alertReasonLabel(v.alertReason[current][i]),origin=!!v.redClusterOrigin[current][i],state=v.outbreakState[current][i];document.getElementById('selectedCard').innerHTML=`<b>${esc(p.barangay)}</b><br>${esc(p.municipality)} · ${esc(p.psgc)}<br><span class="${v.auto[current][i]?'status-high':''}">${v.auto[current][i]?(origin?'LIKELY CLUSTER ORIGIN · DENGUE OUTBREAK ALERT · ':'DENGUE OUTBREAK ALERT · '):''}${esc(state.replaceAll('_',' '))}</span><br>Alert rule: <b>${esc(reason)}</b><br>Bayesian probability level: ${alert}<br>Projected cases: <b>${fmt(v.mean[current][i])}</b><br>95% range: ${fmt(v.lower[current][i])}–${fmt(v.upper[current][i])}<br>Outbreak chance: ${fmt(v.prob[current][i]*100,1)}%<br>Red-zone cluster size: ${v.redClusterSize[current][i]||0}<br>Adjacent red barangays: ${v.redNeighbors[current][i]||0}<br>Hotspot z-score: ${fmt(v.hotspot[current][i])}<br>Spatial lag: ${fmt(v.spatial[current][i])}<br>High-risk neighbours: ${v.neighbors[current][i]}<br>Leading pressure: ${esc(factor)}<br>Boundary gap allocated: ${fmt(p.gap_fill_km2,3)} km²`;drawSelectedTrend();drawFactorContributions();if(zoom){map.flyTo(DATA.centroids[i],Math.max(map.getZoom(),12),{duration:.55})}const popup=barangayLayers[i].getPopup();if(popup&&popup.isOpen())popup.setContent(popupHtml(i))}
function updateLabelSize(){const z=map.getZoom(),size=z<10?0:z<11?6:z<12?7:z<13?8:z<14?9:10;document.getElementById('map').style.setProperty('--barangay-label-size',size+'px');labelLayer.getLayers().forEach(m=>m.setOpacity(size?1:0))}
DATA.geojson.features.forEach((feature,i)=>{const layer=L.geoJSON(feature,{pane:'barangayPane',style:{color:'#fff',weight:.9,opacity:1,fillOpacity:.9},onEachFeature:(f,l)=>{l.bindPopup(()=>popupHtml(i),{maxWidth:360});l.on('click',()=>selectNode(i,false))}}).getLayers()[0];layer.addTo(map);barangayLayers.push(layer);const p=feature.properties,icon=L.divIcon({className:'barangay-label-icon',html:`<span>${esc(p.barangay)}</span>`,iconSize:[0,0]});L.marker(DATA.centroids[i],{icon,pane:'labelPane',interactive:false,keyboard:false}).addTo(labelLayer)});
const municipalityLayer=L.geoJSON(DATA.municipalityGeojson,{pane:'municipalityPane',interactive:false,style:{fill:false,color:'#fff',weight:3.5,opacity:1}}).addTo(map);
const provinceLayer=L.geoJSON(DATA.provinceGeojson,{pane:'provincePane',interactive:false,style:{fill:false,color:'#fff',weight:5.5,opacity:1}}).addTo(map);
const provinceBounds=provinceLayer.getBounds();map.fitBounds(provinceBounds,{padding:[25,25]});map.on('zoomend',updateLabelSize);
function updateMap(){const vals=metricValues(),[lo,hi,mid]=metricScale(),query=searchQuery,isCaseMetric=document.getElementById('metric').value==='mean';for(let i=0;i<barangayLayers.length;i++){const p=DATA.geojson.features[i].properties,match=!query||p.search.includes(query),isAlert=!!DATA.values.auto[current][i],fill=isAlert?'#ff0015':(isCaseMetric?gradientColor(DATA.values.caseIntensity[current][i],0,1):gradientColor(vals[i],lo,hi));barangayLayers[i].setStyle({fillColor:fill,fillOpacity:match?(isAlert?.98:.88):.12,color:'#fff',weight:isAlert?2.4:.85,opacity:match?1:.28});if(isAlert)barangayLayers[i].bringToFront()}municipalityLayer.bringToFront();provinceLayer.bringToFront();const info=metricInfo();document.getElementById('mapMetricLabel').textContent=info[0];document.getElementById('legendTitle').textContent=info[0];const scaleFactor=info[1]==='%'?100:1;document.getElementById('legendLow').textContent=fmt(lo*scaleFactor,info[1]==='%'?0:2)+(info[1]==='%'?'%':'');document.getElementById('legendMid').textContent=fmt(mid*scaleFactor,info[1]==='%'?0:2)+(info[1]==='%'?'%':'');document.getElementById('legendHigh').textContent=fmt(hi*scaleFactor,info[1]==='%'?0:2)+(info[1]==='%'?'%':'')}
function updatePings(){pingLayer.clearLayers();for(let i=0;i<DATA.geojson.features.length;i++){if(!DATA.values.auto[current][i])continue;const p=DATA.geojson.features[i].properties,onset=!!DATA.values.onset[current][i],origin=!!DATA.values.redClusterOrigin[current][i],member=!!DATA.values.redCluster[current][i],classes=[onset?'onset':'',origin?'origin':'',member&&!origin?'cluster-member':''].filter(Boolean).join(' '),size=origin?38:30,icon=L.divIcon({className:'outbreak-ping-icon',html:`<div class="ping-core ${classes}"></div>`,iconSize:[size,size],iconAnchor:[size/2,size/2]});L.marker(DATA.centroids[i],{icon,pane:'pingPane',keyboard:true,title:`${origin?'Likely cluster origin':'Outbreak alert'}: ${p.barangay}`}).bindPopup(()=>popupHtml(i),{maxWidth:380}).on('click',()=>selectNode(i,false)).addTo(pingLayer)}}
function S(name,attrs={}){const e=document.createElementNS('http://www.w3.org/2000/svg',name);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));return e}function clearSvg(el){while(el.firstChild)el.removeChild(el.firstChild)}
function axes(el,w,h,m,yMax){el.appendChild(S('line',{x1:m.l,y1:h-m.b,x2:w-m.r,y2:h-m.b,class:'axis'}));el.appendChild(S('line',{x1:m.l,y1:m.t,x2:m.l,y2:h-m.b,class:'axis'}));for(let i=0;i<=4;i++){const y=h-m.b-i/4*(h-m.t-m.b);el.appendChild(S('line',{x1:m.l,y1:y,x2:w-m.r,y2:y,class:'grid'}));const t=S('text',{x:m.l-5,y:y+3,'text-anchor':'end'});t.textContent=(yMax*i/4).toFixed(yMax<10?1:0);el.appendChild(t)}}
function linePath(values,w,h,m,yMax){return values.map((v,i)=>`${i?'L':'M'}${(m.l+i/Math.max(values.length-1,1)*(w-m.l-m.r)).toFixed(1)},${(h-m.b-(Number(v)||0)/Math.max(yMax,1e-9)*(h-m.t-m.b)).toFixed(1)}`).join(' ')}
function yearLabels(el,w,h,m){[0,.5,1].forEach((f,i)=>{const t=S('text',{x:m.l+f*(w-m.l-m.r),y:h-8,'text-anchor':'middle'});t.textContent=[DATA.dates[0].slice(0,4),DATA.dates[Math.floor(DATA.dates.length/2)].slice(0,4),DATA.dates.at(-1).slice(0,4)][i];el.appendChild(t)})}
function drawProvinceTrend(){const el=document.getElementById('provinceTrend'),w=540,h=230,m={l:50,r:14,t:10,b:28};clearSvg(el);el.setAttribute('viewBox',`0 0 ${w} ${h}`);const mean=DATA.summary.map(s=>s.PROVINCE_POSTERIOR_MEAN_CASES),lo=DATA.summary.map(s=>s.PROVINCE_LOWER_CREDIBLE_CASES),hi=DATA.summary.map(s=>s.PROVINCE_UPPER_CREDIBLE_CASES),max=Math.max(...hi,1)*1.04;axes(el,w,h,m,max);yearLabels(el,w,h,m);const pts=[];for(let i=0;i<hi.length;i++){const x=m.l+i/(hi.length-1)*(w-m.l-m.r),y=h-m.b-hi[i]/max*(h-m.t-m.b);pts.push(`${x},${y}`)}for(let i=lo.length-1;i>=0;i--){const x=m.l+i/(lo.length-1)*(w-m.l-m.r),y=h-m.b-lo[i]/max*(h-m.t-m.b);pts.push(`${x},${y}`)}el.appendChild(S('polygon',{points:pts.join(' '),class:'credible'}));el.appendChild(S('path',{d:linePath(mean,w,h,m,max),class:'chart-line',stroke:'#102a43'}));const x=m.l+current/(DATA.dates.length-1)*(w-m.l-m.r);el.appendChild(S('line',{x1:x,y1:m.t,x2:x,y2:h-m.b,class:'cursor'}))}
function drawSelectedTrend(){const el=document.getElementById('selectedTrend'),w=540,h=230,m={l:50,r:14,t:10,b:28};clearSvg(el);el.setAttribute('viewBox',`0 0 ${w} ${h}`);const vals=DATA.values.mean.map(r=>r[selectedNode]),max=Math.max(...vals,1)*1.08;axes(el,w,h,m,max);yearLabels(el,w,h,m);el.appendChild(S('path',{d:linePath(vals,w,h,m,max),class:'chart-line',stroke:'#2563eb'}));const x=m.l+current/(DATA.dates.length-1)*(w-m.l-m.r);el.appendChild(S('line',{x1:x,y1:m.t,x2:x,y2:h-m.b,class:'cursor'}))}
function currentMunicipalities(){return DATA.municipalityMonthly.filter(r=>r.DATE===DATA.dates[current]).sort((a,b)=>b.POSTERIOR_MEAN_CASES-a.POSTERIOR_MEAN_CASES)}
function drawMunicipalityBars(){const el=document.getElementById('municipalityBars'),w=540,h=260,m={l:115,r:35,t:8,b:8};clearSvg(el);el.setAttribute('viewBox',`0 0 ${w} ${h}`);const rows=currentMunicipalities(),max=Math.max(...rows.map(r=>r.POSTERIOR_MEAN_CASES),1);rows.forEach((r,i)=>{const step=(h-m.t-m.b)/rows.length,y=m.t+i*step+2,bh=step-4,bw=r.POSTERIOR_MEAN_CASES/max*(w-m.l-m.r);el.appendChild(S('rect',{x:m.l,y,width:bw,height:bh,fill:r.AUTOMATIC_OUTBREAK_ALERT_COUNT?'#ff0015':gradientColor(r.POSTERIOR_MEAN_CASES,0,max)}));const n=S('text',{x:m.l-5,y:y+bh*.72,'text-anchor':'end'});n.textContent=r.MUNICIPALITY;el.appendChild(n);const v=S('text',{x:m.l+bw+4,y:y+bh*.72});v.textContent=r.POSTERIOR_MEAN_CASES.toFixed(1);el.appendChild(v)})}
function drawRiskDistribution(){const el=document.getElementById('riskDistribution'),w=540,h=180;clearSvg(el);el.setAttribute('viewBox',`0 0 ${w} ${h}`);const s=DATA.summary[current],vals=[s.LOW_ALERT_COUNT,s.MODERATE_ALERT_COUNT,s.HIGH_ALERT_COUNT,s.CRITICAL_ALERT_COUNT],names=['Low','Moderate','High','Critical'],colors=['#10b981','#fde047','#f97316','#ff0015'],total=vals.reduce((a,b)=>a+b,0);let x=20;vals.forEach((v,i)=>{const bw=v/Math.max(total,1)*500;el.appendChild(S('rect',{x,y:70,width:bw,height:52,fill:colors[i]}));if(bw>35){const t=S('text',{x:x+bw/2,y:100,'text-anchor':'middle',fill:i===1?'#111':'#fff'});t.textContent=`${names[i]} ${v}`;el.appendChild(t)}x+=bw});const title=S('text',{x:270,y:38,'text-anchor':'middle'});title.textContent=`${total} barangays · ${s.AUTOMATIC_OUTBREAK_ALERT_COUNT} outbreak alerts`;el.appendChild(title)}
function drawOnsetTrend(){const el=document.getElementById('onsetTrend'),w=540,h=220,m={l:45,r:14,t:10,b:28};clearSvg(el);el.setAttribute('viewBox',`0 0 ${w} ${h}`);const a=DATA.summary.map(s=>s.NEW_AUTOMATIC_OUTBREAK_ONSET_COUNT),b=DATA.summary.map(s=>s.AUTOMATIC_OUTBREAK_ALERT_COUNT),c=DATA.summary.map(s=>s.RED_CLUSTER_OUTBREAK_ALERT_COUNT),d=DATA.summary.map(s=>s.INDIVIDUAL_OUTBREAK_ALERT_COUNT),max=Math.max(...a,...b,...c,...d,1)*1.08;axes(el,w,h,m,max);yearLabels(el,w,h,m);el.appendChild(S('path',{d:linePath(a,w,h,m,max),class:'chart-line',stroke:'#f97316'}));el.appendChild(S('path',{d:linePath(b,w,h,m,max),class:'chart-line',stroke:'#ff0015'}));el.appendChild(S('path',{d:linePath(c,w,h,m,max),class:'chart-line',stroke:'#7c3aed'}));el.appendChild(S('path',{d:linePath(d,w,h,m,max),class:'chart-line',stroke:'#2563eb'}));const x=m.l+current/(DATA.dates.length-1)*(w-m.l-m.r);el.appendChild(S('line',{x1:x,y1:m.t,x2:x,y2:h-m.b,class:'cursor'}))}
function drawFactorContributions(){const el=document.getElementById('factorContributions'),w=540,h=235,m={l:168,r:45,t:8,b:8};clearSvg(el);el.setAttribute('viewBox',`0 0 ${w} ${h}`);const shares=factorShares(selectedNode),order=shares.map((v,i)=>[v,i]).sort((a,b)=>b[0]-a[0]),top=order[0][1];DATA.factorLabels.forEach((label,i)=>{const step=(h-m.t-m.b)/DATA.factorLabels.length,y=m.t+i*step+4,bh=step-8,bw=shares[i]*(w-m.l-m.r);el.appendChild(S('rect',{x:m.l,y,width:bw,height:bh,class:i===top?'factor-bar top':'factor-bar'}));const n=S('text',{x:m.l-6,y:y+bh*.73,'text-anchor':'end'});n.textContent=label;el.appendChild(n);const v=S('text',{x:m.l+bw+4,y:y+bh*.73});v.textContent=(shares[i]*100).toFixed(1)+'%';el.appendChild(v)});const p=DATA.geojson.features[selectedNode].properties;document.getElementById('factorNarrative').innerHTML=`For <b>${esc(p.barangay)}</b> in <b>${DATA.dates[current].slice(0,7)}</b>, the leading modeled contribution is <b>${DATA.factorLabels[top]}</b> (${(shares[top]*100).toFixed(1)}%), followed by <b>${DATA.factorLabels[order[1][1]]}</b> (${(shares[order[1][1]]*100).toFixed(1)}%). These are model components, not proof of clinical causation.`}
function rankingRows(){return DATA.geojson.features.map((f,i)=>({i,p:f.properties,v:DATA.values.mean[current][i],auto:DATA.values.auto[current][i],prob:DATA.values.prob[current][i]})).sort((a,b)=>b.v-a.v)}
function renderRanking(){const rows=rankingRows(),max=Math.max(...rows.map(r=>r.v),1),el=document.getElementById('ranking');el.innerHTML=rows.map((r,idx)=>`<div class="rank-row ${r.auto?'outbreak':''}" data-node="${r.i}"><div class="rank-number">${idx+1}</div><div class="rank-name"><b>${esc(r.p.barangay)}</b><span>${esc(r.p.municipality)}</span></div><div class="rank-value">${fmt(r.v)} cases<br>${fmt(r.prob*100,1)}%</div><div class="rank-bar-wrap"><div class="rank-bar" style="width:${clamp(r.v/max*100,1,100)}%;background:${r.auto?'#ff0015':gradientColor(r.v,0,max)}"></div></div></div>`).join('');el.querySelectorAll('[data-node]').forEach(row=>row.addEventListener('click',()=>{selectNode(+row.dataset.node,true);openDrawer('overview')}))}
const TABLE_COLUMNS=[['rank','Rank'],['psgc','PSGC'],['municipality','Municipality'],['barangay','Barangay'],['status','Calibration status'],['mean','Posterior mean'],['median','Posterior median'],['lower','Lower 95%'],['upper','Upper 95%'],['threshold','Outbreak threshold'],['prob','Outbreak probability'],['alert','Alert level'],['auto','Automatic outbreak alert'],['outbreakState','Operational state'],['alertReason','Alert reason'],['onset','New automatic alert onset'],['individualAuto','Individual outbreak alert'],['redZone','Red case zone'],['redNeighbors','Adjacent red barangays'],['redCluster','Red-cluster alert'],['redClusterOnset','New red-cluster onset'],['redClusterOrigin','Likely cluster origin'],['redClusterId','Red cluster ID'],['redClusterSize','Red cluster size'],['probabilisticAuto','Probabilistic alert'],['hotspot','Hotspot z'],['spatial','Spatial lag'],['neighbors','High-risk neighbours'],['highShare','High-risk neighbour share'],['clusterFlag','Cluster alert'],['persistentFlag','Persistent alert'],['seasonal','Seasonal climatology'],['growth','Growth multiplier'],['baseline','Baseline cases'],['trendUplift','Trend uplift'],['lag1','Lag 1'],['lag3','Lag 3'],['lag6','Lag 6'],['lag12','Lag 12'],['roll3','Rolling mean 3'],['roll6','Rolling mean 6'],['roll12','Rolling mean 12'],['rollStd3','Rolling std 3'],['temporalMemory','Temporal memory'],['temporalAdjusted','Temporal adjusted'],['temporalUplift','Temporal uplift'],['spatialUplift','Spatial uplift'],['clusterUplift','Cluster uplift'],['prior','Prior mean'],['bayesianDelta','Bayesian delta'],['observedApplied','Observation applied'],['observedCases','Observed update'],['dominant','Dominant factor'],['factorSeasonal','Seasonal %'],['factorTrend','Trend %'],['factorTemporal','Persistence %'],['factorSpatial','Spillover %'],['factorCluster','Cluster %'],['factorBayesian','Bayesian %'],['horizonMonth','Horizon month'],['horizonYear','Horizon year']];
document.getElementById('liveHead').innerHTML='<tr>'+TABLE_COLUMNS.map(c=>`<th>${c[1]}</th>`).join('')+'</tr>';
function tableValue(key,r,rank){const v=DATA.values,p=r.p,i=r.i;if(key==='rank')return rank;if(key==='psgc'||key==='municipality'||key==='barangay'||key==='status')return p[key];if(key==='alert')return alertName(v.alert[current][i]);if(key==='alertReason')return alertReasonLabel(v.alertReason[current][i]);if(key==='outbreakState')return String(v.outbreakState[current][i]).replaceAll('_',' ');if(key==='dominant')return DATA.factorLabels[v.factorDominant[current][i]];const value=v[key][current][i];if(['auto','onset','individualAuto','redZone','redCluster','redClusterOnset','redClusterOrigin','probabilisticAuto','clusterFlag','persistentFlag','observedApplied'].includes(key))return value?'Yes':'No';if(key==='prob'||key.startsWith('factor')||key==='highShare')return fmt(value*100,1)+'%';return fmt(value,3)}
function renderTable(){const query=document.getElementById('tableSearch').value.toUpperCase().trim(),rows=rankingRows().filter(r=>!query||`${r.p.search} ${r.p.psgc} ${alertName(DATA.values.alert[current][r.i])} ${DATA.factorLabels[DATA.values.factorDominant[current][r.i]]}`.includes(query));document.getElementById('liveBody').innerHTML=rows.map((r,index)=>`<tr class="${r.auto?'outbreak':''}" data-node="${r.i}">${TABLE_COLUMNS.map(c=>`<td>${esc(tableValue(c[0],r,index+1))}</td>`).join('')}</tr>`).join('');document.querySelectorAll('#liveBody [data-node]').forEach(row=>row.addEventListener('click',()=>{selectNode(+row.dataset.node,true);barangayLayers[+row.dataset.node].openPopup()}))}
function renderAlerts(){const rows=rankingRows().filter(r=>r.auto);document.getElementById('alerts').innerHTML=rows.length?rows.map(r=>{const v=DATA.values,origin=!!v.redClusterOrigin[current][r.i],cluster=v.redClusterSize[current][r.i]||0;return `<div class="alert-row" data-node="${r.i}"><b>${origin?'★ ':''}${esc(r.p.barangay)}</b> · ${esc(r.p.municipality)}<br>${fmt(r.v)} cases · ${fmt(r.prob*100,1)}% chance${cluster?` · cluster of ${cluster}`:''}<br><b>${esc(alertReasonLabel(v.alertReason[current][r.i]))}</b> · ${esc(DATA.factorLabels[v.factorDominant[current][r.i]])}</div>`}).join(''):'<div class="small">No individual, probabilistic, or three-adjacent-red-barangay alerts this month.</div>';document.querySelectorAll('#alerts [data-node]').forEach(row=>row.addEventListener('click',()=>{selectNode(+row.dataset.node,true);barangayLayers[+row.dataset.node].openPopup()}))}
function renderSummary(){const s=DATA.summary[current];document.getElementById('summary').innerHTML=`<div class="metric"><span>Projected cases</span><b>${fmt(s.PROVINCE_POSTERIOR_MEAN_CASES,1)}</b></div><div class="metric"><span>95% credible range</span><b>${fmt(s.PROVINCE_LOWER_CREDIBLE_CASES,1)}–${fmt(s.PROVINCE_UPPER_CREDIBLE_CASES,1)}</b></div><div class="metric"><span>Automatic alerts</span><b>${s.AUTOMATIC_OUTBREAK_ALERT_COUNT}</b></div><div class="metric"><span>Connected red clusters</span><b>${s.RED_CLUSTER_COUNT}</b></div><div class="metric"><span>Cluster members</span><b>${s.RED_CLUSTER_OUTBREAK_ALERT_COUNT}</b></div><div class="metric"><span>Likely origins</span><b>${s.RED_CLUSTER_LIKELY_ORIGIN_COUNT}</b></div><div class="metric"><span>New alert starts</span><b>${s.NEW_AUTOMATIC_OUTBREAK_ONSET_COUNT}</b></div><div class="metric"><span>Moran's I</span><b>${fmt(s.MORANS_I,3)}</b></div>`}
function update(){document.getElementById('dateLabel').textContent=DATA.dates[current].slice(0,7);document.getElementById('drawerDate').textContent=DATA.dates[current].slice(0,7);const s=DATA.summary[current];document.getElementById('mapStatus').textContent=`${s.AUTOMATIC_OUTBREAK_ALERT_COUNT} alerts · ${fmt(s.PROVINCE_POSTERIOR_MEAN_CASES,1)} cases`;updateMap();updatePings();renderSummary();renderAlerts();selectNode(selectedNode,false);drawProvinceTrend();drawMunicipalityBars();drawRiskDistribution();drawOnsetTrend();if(activeTab==='graphs')renderRanking();if(activeTab==='table')renderTable()}
function openDrawer(tab=activeTab){document.getElementById('drawer').classList.add('open');setTab(tab)}function closeDrawer(){document.getElementById('drawer').classList.remove('open')}function setTab(tab){activeTab=tab;document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));document.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active',p.id===`tab-${tab}`));if(tab==='graphs'){drawProvinceTrend();drawSelectedTrend();drawMunicipalityBars();drawRiskDistribution();drawOnsetTrend();renderRanking()}if(tab==='table')renderTable()}
function stop(){if(timer){clearInterval(timer);timer=null;document.getElementById('play').textContent='▶ Play'}}function play(){stop();timer=setInterval(()=>{current=(current+1)%DATA.dates.length;document.getElementById('slider').value=current;update()},Number(document.getElementById('speed').value));document.getElementById('play').textContent='Ⅱ Pause'}
document.getElementById('play').onclick=()=>timer?stop():play();document.getElementById('analytics').onclick=()=>openDrawer();document.getElementById('closeDrawer').onclick=closeDrawer;document.getElementById('settingsButton').onclick=()=>document.getElementById('settingsPanel').classList.toggle('open');document.getElementById('labels').onclick=()=>{labelsVisible=!labelsVisible;document.getElementById('map').classList.toggle('labels-hidden',!labelsVisible);document.getElementById('labels').textContent=labelsVisible?'Aa Labels':'Aa Hidden'};document.getElementById('reset').onclick=()=>map.fitBounds(provinceBounds,{padding:[25,25]});document.getElementById('slider').oninput=e=>{current=+e.target.value;update()};document.getElementById('metric').onchange=update;document.getElementById('speed').onchange=()=>{if(timer)play()};document.getElementById('search').oninput=e=>{searchQuery=e.target.value.toUpperCase().trim();updateMap()};document.getElementById('tableSearch').oninput=renderTable;document.querySelectorAll('.tab-btn').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));
updateLabelSize();update();
</script>
</body></html>'''
    html_text = html_text.replace("__PAYLOAD__", payload_json).replace("__MAX_INDEX__", str(date_count - 1))
    output.write_text(html_text, encoding="utf-8")



def generate_report(
    paths: Paths,
    metadata: dict[str, Any],
    match_report: pd.DataFrame,
    coverage: pd.DataFrame,
    top_risk: pd.DataFrame,
) -> None:
    matched = int((match_report["MATCH_STATUS"] == "MATCHED").sum()) if not match_report.empty else 0
    unmatched = int((match_report["MATCH_STATUS"] != "MATCHED").sum()) if not match_report.empty else 0
    chart_items = "".join(
        f'<figure><img src="charts/{html.escape(path.name)}" alt="{html.escape(path.stem)}"><figcaption>{html.escape(path.stem.replace("_", " ").title())}</figcaption></figure>'
        for path in sorted(paths.charts.glob("*.png"))
    )
    top_html = top_risk.head(20).to_html(index=False, classes="data", border=0, float_format=lambda value: f"{value:.4f}")
    coverage_html = coverage.to_html(index=False, classes="data", border=0)
    report = f"""<!doctype html><html><head><meta charset="utf-8"><title>ORACLIS South Cotabato Spatio-Temporal Bayesian Report</title>
<style>body{{font:12pt 'Times New Roman',serif;max-width:1180px;margin:28px auto;padding:0 20px;color:#222}}h1,h2{{color:#17324d}}.warning{{border:2px solid #b45309;background:#fff7ed;padding:12px}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.metric{{border:1px solid #aaa;padding:10px}}figure{{margin:22px 0}}img{{max-width:100%;border:1px solid #ccc}}table.data{{border-collapse:collapse;width:100%;font-size:10pt}}table.data th,table.data td{{border:1px solid #aaa;padding:5px;text-align:right}}table.data th:first-child,table.data td:first-child{{text-align:left}}code{{background:#eee;padding:2px 4px}}</style></head><body>
<h1>ORACLIS South Cotabato Spatio-Temporal Feature Engineering, Bayesian Simulation, and Outbreak Detection</h1>
<div class="warning"><b>Scientific scope:</b> The geographic scope is the Province of South Cotabato only. The available monthly dengue targets were interpolated from annual totals. All South Cotabato barangays are marked as calibrated in this system. The 2026–2050 results are long-range projections, not validated clinical forecasts.</div>
<h2>Run summary</h2><div class="metrics">
<div class="metric"><b>South Cotabato barangays</b><br>{metadata['boundary_feature_count']}</div>
<div class="metric"><b>Matched source barangays</b><br>{matched}</div>
<div class="metric"><b>Unmatched source barangays</b><br>{unmatched}</div>
<div class="metric"><b>Monthly snapshots</b><br>{metadata['forecast_months']}</div></div>
<p><b>Projection period:</b> {metadata['forecast_start']} through {metadata['forecast_end']}<br>
<b>Boundary vintage:</b> {html.escape(str(metadata.get('boundary_geometry_snapshot', '2023')))}<br>
<b>Weighted ensemble formula:</b> <code>{html.escape(metadata['ensemble_formula'])}</code><br>
<b>Monte Carlo draws per barangay-month:</b> {metadata['monte_carlo_draws']}<br>
<b>Automatic outbreak rules:</b> individual barangay threshold/probability rule, probabilistic High/Critical persistence-neighbour rule, OR a connected cluster of at least three truly adjacent barangays in the visible red projected-case zone.</p>
<p><a href="maps/ORACLIS_SouthCotabato_Interactive_Forecast_Map.html">Open the interactive South Cotabato map and live charts</a></p>
<h2>Municipality data coverage</h2>{coverage_html}
<h2>Highest-risk barangays across the scenario horizon</h2>{top_html}
<h2>Generated figures</h2>{chart_items}
<h2>Backend files</h2><ul><li><code>database/oraclis_spatiotemporal.sqlite</code></li><li><code>tables/monthly_barangay_forecasts.csv</code></li><li><code>tables/monthly_municipality_summary.csv</code></li><li><code>tables/outbreak_alerts.csv</code></li><li><code>tables/red_cluster_outbreak_events.csv</code></li><li><code>tables/outbreak_factor_summary.csv</code></li><li><code>tables/outbreak_factor_by_municipality.csv</code></li><li><code>tables/spatial_adjacency_edges.csv</code></li><li><code>maps/south_cotabato_barangays_with_status.geojson</code></li><li><code>maps/south_cotabato_municipality_boundaries.geojson</code></li></ul>
</body></html>"""
    (paths.run / "ORACLIS_SouthCotabato_SpatioTemporal_Bayesian_Report.html").write_text(report, encoding="utf-8")



def run_self_tests() -> None:


    localities = load_south_cotabato_localities_2023()
    assert len(localities) == EXPECTED_LOCALITY_COUNT
    assert len({item.code for item in localities}) == EXPECTED_LOCALITY_COUNT
    assert all(len(item.code) == 10 and item.code.startswith("12063") for item in localities)
    assert all(item.code != "1903804000" for item in localities)
    assert all("CITY OF COTABATO" not in item.name.upper() for item in localities)
    assert REGION_CODE == "1200000000"
    assert PROVINCE_CODE == "1206300000"
    assert EXPECTED_BARANGAY_FEATURES == 199
    assert {item.code for item in localities} == {"1206302000", "1206306000", "1206311000", "1206312000", "1206313000", "1206314000", "1206315000", "1206316000", "1206317000", "1206318000", "1206319000"}

    probabilities = np.array([0.1, 0.3, 0.6, 0.8, 0.99])
    labels = classify_alert(probabilities, {"moderate": 0.30, "high": 0.60, "critical": 0.80})
    assert labels.tolist() == ["LOW", "MODERATE", "HIGH", "CRITICAL", "CRITICAL"]
    prior_mean = np.array([1.0, 4.0, 10.0])
    variance = np.array([2.0, 8.0, 25.0])
    alpha = prior_mean ** 2 / variance
    beta = prior_mean / variance
    assert np.allclose(alpha / beta, prior_mean)
    w = sparse.csr_matrix(np.array([[0, 1], [1, 0]], dtype=float))
    assert abs(global_morans_i(np.array([1.0, 2.0]), w) + 1.0) < 1e-9
    assert normalize_name("Sto. Niño") == "SANTO NINO"
    assert "POBLACION" in name_variants("Pob.")
    previous = np.array(["LOW", "HIGH", "MODERATE"], dtype=object)
    current = np.array(["HIGH", "HIGH", "CRITICAL"], dtype=object)
    onset = np.isin(current, ["HIGH", "CRITICAL"]) & ~np.isin(previous, ["HIGH", "CRITICAL"])
    assert onset.tolist() == [True, False, True]
    intensity, low_reference, high_reference = projected_case_color_intensity(np.array([1.0, 2.0, 8.0, 10.0]), 0.0, 1.0)
    assert low_reference == 1.0 and high_reference == 10.0
    assert np.allclose(intensity, np.array([0.0, 1.0 / 9.0, 7.0 / 9.0, 1.0]))
    toy_adjacency = sparse.csr_matrix(np.array([
        [0, 1, 0, 0, 0],
        [1, 0, 1, 0, 0],
        [0, 1, 0, 1, 0],
        [0, 0, 1, 0, 0],
        [0, 0, 0, 0, 0],
    ], dtype=int))
    red_members, red_ids, red_sizes = connected_red_cluster_members(
        np.array([True, True, True, False, True]), toy_adjacency, 3
    )
    assert red_members.tolist() == [True, True, True, False, False]
    assert red_sizes.tolist() == [3, 3, 3, 0, 0]
    assert len(set(red_ids[red_ids > 0].tolist())) == 1
    cluster_onset = cluster_component_onset_flags(red_ids, np.array([False, False, False, False, False]))
    assert cluster_onset.tolist() == [True, True, True, False, False]
    continued_onset = cluster_component_onset_flags(red_ids, np.array([False, True, False, False, False]))
    assert not continued_onset.any()
    origins = select_red_cluster_origins(
        red_ids, np.array([3, 2, 2, -1, -1]), np.array([4.0, 4.2, 4.1, 0.0, 0.0]),
        np.array([3.5, 3.0, 3.8, 0.0, 0.0]), np.array([4.0, 4.0, 4.0, 1.0, 1.0]),
        np.array([1.0, 2.0, 1.5, 0.0, 0.0]), 4,
    )
    assert origins.tolist() == [False, True, False, False, False]
    reasons = compose_outbreak_alert_reasons(
        np.array([True, False, False]), np.array([False, True, False]), np.array([True, False, False])
    )
    assert reasons.tolist() == ["INDIVIDUAL_BARANGAY+THREE_CONNECTED_RED_BARANGAYS", "PROBABILISTIC_SPATIOTEMPORAL", "NONE"]
    states = classify_operational_outbreak_state(
        np.array([True, True, True, False]), np.array([0, 1, 2, 0]),
        np.array([False, False, True, False]), np.array([False, False, True, False]),
    )
    assert states.tolist() == ["RED_CASE_ZONE", "CLUSTER_WATCH", "COMBINED_OUTBREAK", "NORMAL"]
    toy_factors = np.array([0.57, 0.03, 0.22, 0.18, 0.0, 0.0])
    assert abs(float(toy_factors.sum()) - 1.0) < 1e-9
    test_config = {"interface": {"gap_fill_tolerance_degrees": 0.004, "gap_partition_validation_ratio": 1e-10}}
    filled_mock, shell, report = fill_internal_boundary_gaps(create_mock_geojson(3), test_config, logging.getLogger("oraclis-self-test"))
    filled_geometries = [shape(feature["geometry"]) for feature in filled_mock["features"]]
    filled_union = unary_union(filled_geometries)
    assert report["fully_covered"]
    assert shell.difference(filled_union).area <= 1e-10
    assert report["detected_gap_face_count"] > 0
    assert max(0.0, sum(item.area for item in filled_geometries) - filled_union.area) <= 1e-10




    overlap_mock = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"area_km2": 1.0}, "geometry": mapping(Polygon([(0, 0), (1.06, 0), (1.06, 1), (0, 1)]))},
            {"type": "Feature", "properties": {"area_km2": 1.0}, "geometry": mapping(Polygon([(0.94, 0), (2, 0), (2, 1), (0.94, 1)]))},
            {"type": "Feature", "properties": {"area_km2": 1.0}, "geometry": mapping(Polygon([(2.006, 0), (3, 0), (3, 1), (2.006, 1)]))},
        ],
    }
    repaired, repaired_shell, repaired_report = fill_internal_boundary_gaps(overlap_mock, test_config, logging.getLogger("oraclis-overlap-test"))
    repaired_geometries = [shape(feature["geometry"]) for feature in repaired["features"]]
    repaired_union = unary_union(repaired_geometries)
    assert repaired_report["detected_overlap_face_count"] > 0
    assert repaired_report["detected_gap_face_count"] > 0
    assert repaired_report["fully_covered"]
    assert repaired_shell.difference(repaired_union).area <= 1e-10
    assert max(0.0, sum(item.area for item in repaired_geometries) - repaired_union.area) <= 1e-10


    overlap_width = 0.008646
    exact_overlap_mock = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"area_km2": 1.0}, "geometry": mapping(Polygon([(0, 0), (1 + overlap_width / 2, 0), (1 + overlap_width / 2, 1), (0, 1)]))},
            {"type": "Feature", "properties": {"area_km2": 1.0}, "geometry": mapping(Polygon([(1 - overlap_width / 2, 0), (2, 0), (2, 1), (1 - overlap_width / 2, 1)]))},
        ],
    }
    exact_originals = [shape(feature["geometry"]) for feature in exact_overlap_mock["features"]]
    exact_original_union = unary_union(exact_originals)
    exact_input_ratio = (sum(item.area for item in exact_originals) - exact_original_union.area) / exact_original_union.area
    assert abs(exact_input_ratio - 0.004323) < 1e-12
    exact_fixed, exact_shell, exact_report = fill_internal_boundary_gaps(exact_overlap_mock, test_config, logging.getLogger("oraclis-exact-overlap-test"))
    exact_geometries = [shape(feature["geometry"]) for feature in exact_fixed["features"]]
    exact_union = unary_union(exact_geometries)
    assert exact_report["detected_overlap_face_count"] == 1
    assert exact_report["overlap_ratio"] <= 1e-10
    assert exact_report["residual_uncovered_ratio"] <= 1e-10
    assert exact_shell.difference(exact_union).area <= 1e-10


def main() -> int:
    args = parse_args()
    config_path = ROOT / args.config
    config = read_json(config_path)
    red_cluster_config = config.get("outbreak", {}).get("red_cluster_detection", {})
    red_cluster_minimum = int(red_cluster_config.get("minimum_connected_barangays", 3))
    red_intensity_threshold = float(red_cluster_config.get("red_intensity_threshold", 0.80))
    individual_probability_threshold = float(
        config.get("outbreak", {}).get("individual_outbreak_probability_threshold", 0.75)
    )
    paths, logger = setup_run(args.output_dir)
    try:
        if args.self_test:
            run_self_tests()
            logger.info("Deterministic internal self-tests passed.")

        logger.info("Loading and validating weighted ensemble artifact...")
        ensemble_path = resolve_latest_ensemble_artifact()
        ensemble = validate_ensemble_artifact(ensemble_path)
        ensemble_test_path = resolve_ensemble_test_table(ensemble_path)
        calibration_factors = municipality_calibration_factors(ensemble_test_path)

        source = load_source_data(config, args.mock)
        boundary_cache = ROOT / config["boundaries"]["cache_geojson"]
        boundary_metadata_path = ROOT / config["boundaries"]["cache_metadata"]
        if args.mock:
            geojson = create_mock_geojson(int(config.get("mock_grid_size", 4)))
            boundary_metadata = {"geometry_snapshot": "MOCK", "source_repository": "INTERNAL TEST GEOMETRY"}
        else:
            geojson, boundary_metadata = ensure_south_cotabato_boundaries(
                boundary_cache,
                boundary_metadata_path,
                int(config["boundaries"]["request_timeout_seconds"]),
                args.force_boundary_download,
                logger,
            )
        geojson, province_geometry, boundary_gap_report = fill_internal_boundary_gaps(geojson, config, logger)
        boundary_metadata["boundary_gap_filling"] = boundary_gap_report
        master, geometries = extract_boundary_master(geojson)
        source_with_nodes, match_report = match_source_to_boundaries(source, master)

        match_report.to_csv(paths.tables / "source_boundary_match_report.csv", index=False)
        if not args.mock:
            match_rate = float((match_report["MATCH_STATUS"] == "MATCHED").mean()) if len(match_report) else 0.0
            if match_rate < float(config["matching"]["minimum_source_match_rate"]):
                raise RuntimeError(
                    f"Only {match_rate:.1%} of source barangays matched real boundaries; "
                    f"minimum required is {float(config['matching']['minimum_source_match_rate']):.1%}. "
                    "Review tables/source_boundary_match_report.csv before proceeding."
                )
        logger.info("Matched %s/%s source barangays to boundary polygons.", len(source_with_nodes[["MUNICIPALITY", "BARANGAY"]].drop_duplicates()), len(match_report))

        graph = build_spatial_graph(geometries, master, config, logger)
        climatology, growth, thresholds, hist_mean, hist_std, status, profiles = estimate_historical_profiles(
            source_with_nodes, master, calibration_factors, config
        )
        master["CALIBRATION_STATUS"] = status
        master["OUTBREAK_CASE_THRESHOLD"] = thresholds
        master["HISTORICAL_OR_PRIOR_MONTHLY_MEAN"] = hist_mean
        master["HISTORICAL_OR_PRIOR_MONTHLY_STD"] = hist_std
        master["ANNUAL_GROWTH_RATE"] = growth
        master["NEIGHBOR_COUNT"] = np.asarray(graph.binary.sum(axis=1)).ravel().astype(int)
        municipality_master, municipality_geometries, municipality_geojson = build_municipality_geometries(master, geometries)
        province_geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "id": PROVINCE_CODE,
                "properties": {"province_code": PROVINCE_CODE, "province": "South Cotabato"},
                "geometry": mapping(province_geometry),
            }],
        }
        coverage = build_municipality_coverage_summary(master)

        start = pd.Timestamp(config["projection"]["forecast_start"])
        end = pd.Timestamp(config["projection"]["forecast_end"])
        dates = pd.date_range(start, end, freq="MS")
        if len(dates) < 12:
            raise ValueError("Forecast horizon must contain at least 12 monthly snapshots.")
        observed_updates = load_observed_updates(config, master)
        forecasts, alerts, summaries = simulate(
            dates, master, graph, climatology, growth, thresholds, status, observed_updates, config, logger
        )
        annual = build_annual_summary(forecasts)
        monthly_municipality = build_monthly_municipality_summary(forecasts)
        annual_municipality = build_annual_municipality_summary(monthly_municipality)
        top_risk = top_risk_table(forecasts, int(config["outputs"]["top_risk_rows"]))
        factor_summary = build_outbreak_factor_summary(forecasts)
        factor_municipality_summary = build_outbreak_factor_municipality_summary(forecasts)
        cluster_events = build_red_cluster_events(forecasts)

        logger.info("Writing tables and database...")
        master.to_csv(paths.tables / "barangay_master.csv", index=False)
        profiles.to_csv(paths.tables / "calibrated_historical_profiles.csv", index=False)
        graph.edges.to_csv(paths.tables / "spatial_adjacency_edges.csv", index=False)
        forecasts.to_csv(paths.tables / "monthly_barangay_forecasts.csv", index=False, float_format="%.6f")
        alerts.to_csv(paths.tables / "outbreak_alerts.csv", index=False, float_format="%.6f")
        summaries.to_csv(paths.tables / "monthly_south_cotabato_summary.csv", index=False, float_format="%.6f")
        annual.to_csv(paths.tables / "annual_projection_summary.csv", index=False, float_format="%.6f")
        monthly_municipality.to_csv(paths.tables / "monthly_municipality_summary.csv", index=False, float_format="%.6f")
        annual_municipality.to_csv(paths.tables / "annual_municipality_summary.csv", index=False, float_format="%.6f")
        coverage.to_csv(paths.tables / "municipality_data_coverage.csv", index=False)
        municipality_master.to_csv(paths.tables / "municipality_boundary_master.csv", index=False)
        write_json(paths.tables / "boundary_gap_filling_report.json", boundary_gap_report)
        pd.DataFrame([{
            "NODE_ID": int(index),
            "PSGC": master.at[index, "PSGC"],
            "MUNICIPALITY": master.at[index, "MUNICIPALITY"],
            "BARANGAY": master.at[index, "BARANGAY"],
            "GAP_FILLED_AREA_KM2": float(feature.get("properties", {}).get("ORACLIS_GAP_FILLED_AREA_KM2", 0.0)),
            "OVERLAP_RESOLVED_AREA_KM2": float(feature.get("properties", {}).get("ORACLIS_OVERLAP_RESOLVED_AREA_KM2", 0.0)),
        } for index, feature in enumerate(geojson["features"])]).to_csv(
            paths.tables / "boundary_gap_allocation_by_barangay.csv", index=False, float_format="%.6f"
        )
        top_risk.to_csv(paths.tables / "top_risk_barangays.csv", index=False, float_format="%.6f")
        factor_summary.to_csv(paths.tables / "outbreak_factor_summary.csv", index=False, float_format="%.6f")
        factor_municipality_summary.to_csv(paths.tables / "outbreak_factor_by_municipality.csv", index=False, float_format="%.6f")
        cluster_events.to_csv(paths.tables / "red_cluster_outbreak_events.csv", index=False, float_format="%.6f")
        save_sparse_graph(graph, paths)

        map_geojson = slim_geojson(geojson, master)
        write_json(paths.maps / "south_cotabato_barangays_with_status.geojson", map_geojson, compact=True)
        write_json(paths.maps / "south_cotabato_municipality_boundaries.geojson", municipality_geojson, compact=True)
        write_json(paths.maps / "south_cotabato_province_boundary.geojson", province_geojson, compact=True)
        generate_interactive_map(
            geojson, municipality_geojson, province_geojson, master, forecasts, summaries, monthly_municipality,
            paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html",
            int(config.get("interface", {}).get("playback_interval_ms", 2800)),
        )

        dpi = int(config["outputs"]["plot_dpi"])
        generate_charts(
            forecasts, alerts, summaries, annual, monthly_municipality, annual_municipality,
            coverage, top_risk, factor_summary, paths, dpi,
        )
        snapshot_dates = config["outputs"]["map_snapshot_dates"]
        for snapshot in snapshot_dates:
            render_snapshot_map(snapshot, forecasts, master, geometries, paths.maps / f"outbreak_probability_map_{snapshot[:7]}.png", dpi)
            write_qgis_snapshot_geojson(
                geojson, master, forecasts, snapshot,
                paths.maps / f"qgis_hotspot_snapshot_{snapshot[:7]}.geojson",
            )

        metadata = {
            "project": "ORACLIS South Cotabato Spatio-Temporal Bayesian Scenario Engine",
            "administrative_scope": "Province of South Cotabato only",
            "province_name": "South Cotabato",
            "province_psgc": PROVINCE_CODE,
            "municipality_count": len(municipality_master),
            "expected_municipality_count": EXPECTED_LOCALITY_COUNT,
            "expected_barangay_count": EXPECTED_BARANGAY_FEATURES,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "forecast_start": dates[0].strftime("%Y-%m-%d"),
            "forecast_end": dates[-1].strftime("%Y-%m-%d"),
            "forecast_months": len(dates),
            "boundary_feature_count": len(master),
            "calibrated_boundary_count": int(np.sum(status == "CALIBRATED_SOUTH_COTABATO")),
            "boundary_geometry_snapshot": boundary_metadata.get("geometry_snapshot", "2023"),
            "boundary_gap_filling": boundary_gap_report,
            "boundary_source": boundary_metadata.get("source_repository", "faeldon/philippines-json-maps"),
            "ensemble_artifact": str(ensemble_path.relative_to(ROOT) if ensemble_path.is_relative_to(ROOT) else ensemble_path),
            "ensemble_artifact_sha256": sha256_file(ensemble_path),
            "ensemble_formula": ensemble.get("formula_machine_readable", ""),
            "monte_carlo_draws": int(config["bayesian"]["monte_carlo_draws"]),
            "spatial_diffusion_weight": float(config["spatial"]["diffusion_weight"]),
            "automatic_outbreak_rules": {
                "individual_rule": f"Posterior mean reaches the barangay threshold or outbreak probability is at least {individual_probability_threshold:.2f}",
                "probabilistic_rule": "High/Critical probability plus persistence or high-risk neighbours",
                "red_cluster_rule": f"At least {red_cluster_minimum} strict Queen-contiguous barangays with projected-case color intensity >= {red_intensity_threshold:.2f}",
            },
            "spatial_edge_count": len(graph.edges),
            "scientific_warning": (
                "Long-range projection. Monthly dengue targets were interpolated from annual totals; "
                "all South Cotabato barangays are marked as calibrated in this system."
            ),
        }
        write_json(paths.run / "run_metadata.json", metadata)
        write_json(paths.run / "spatiotemporal_config_used.json", config)
        write_sqlite(
            paths.database / "oraclis_spatiotemporal.sqlite", master, municipality_master, graph, forecasts, alerts,
            summaries, monthly_municipality, annual_municipality, cluster_events, metadata,
        )
        generate_report(paths, metadata, match_report, coverage, top_risk)

        verification_checks = {
            "probabilities_within_0_1": bool(forecasts["OUTBREAK_PROBABILITY"].between(0, 1).all()),
            "credible_interval_ordering_valid": bool(((forecasts["LOWER_CREDIBLE_CASES"] <= forecasts["POSTERIOR_MEAN_CASES"]) & (forecasts["POSTERIOR_MEAN_CASES"] <= forecasts["UPPER_CREDIBLE_CASES"])).all()),
            "no_negative_posterior_predictions": bool((forecasts["POSTERIOR_MEAN_CASES"] >= 0).all()),
            "spatial_weights_row_sums_equal_1": bool(np.allclose(np.asarray(graph.w.sum(axis=1)).ravel(), 1.0)),
            "all_nodes_are_south_cotabato": bool(master["PROVINCE_CODE"].astype(str).eq(PROVINCE_CODE).all()),
            "municipality_count_valid": bool(len(municipality_master) == (len(municipality_master) if args.mock else EXPECTED_LOCALITY_COUNT)),
            "barangay_count_valid": bool(len(master) == (len(master) if args.mock else EXPECTED_BARANGAY_FEATURES)),
            "forecast_row_count_valid": bool(len(forecasts) == len(master) * len(dates)),
            "interactive_map_exists": bool((paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html").exists()),
            "html_report_exists": bool((paths.run / "ORACLIS_SouthCotabato_SpatioTemporal_Bayesian_Report.html").exists()),
            "municipality_boundary_count_valid": bool(len(municipality_geojson["features"]) == len(municipality_master)),
            "sqlite_exists": bool((paths.database / "oraclis_spatiotemporal.sqlite").exists()),
            "factor_shares_sum_to_one": bool(np.allclose(
                forecasts[[
                    "FACTOR_SEASONAL_BASELINE_SHARE", "FACTOR_LONG_TERM_TREND_SHARE",
                    "FACTOR_RECENT_CASE_PERSISTENCE_SHARE", "FACTOR_NEIGHBOUR_SPILLOVER_SHARE",
                    "FACTOR_HIGH_RISK_CLUSTER_SHARE", "FACTOR_BAYESIAN_EVIDENCE_SHARE",
                ]].sum(axis=1).to_numpy(), 1.0, atol=1e-6
            )),
            "automatic_outbreak_onsets_are_active_alerts": bool((
                ~forecasts["OUTBREAK_ONSET_PING"] | forecasts["AUTOMATIC_OUTBREAK_ALERT"]
            ).all()),
            "red_cluster_alerts_have_at_least_three_members": bool((
                ~forecasts["RED_CLUSTER_OUTBREAK_ALERT"] | (forecasts["RED_CLUSTER_SIZE"] >= red_cluster_minimum)
            ).all()),
            "each_red_cluster_has_exactly_one_likely_origin": bool(
                forecasts.loc[forecasts["RED_CLUSTER_ID"] > 0]
                .groupby(["DATE", "RED_CLUSTER_ID"])["RED_CLUSTER_LIKELY_ORIGIN"].sum().eq(1).all()
            ),
            "cluster_origins_are_cluster_members": bool((
                ~forecasts["RED_CLUSTER_LIKELY_ORIGIN"] | forecasts["RED_CLUSTER_OUTBREAK_ALERT"]
            ).all()),
            "strict_contiguity_is_symmetric": bool((graph.contiguity_binary != graph.contiguity_binary.T).nnz == 0),
            "automatic_alert_reason_is_populated": bool((
                (~forecasts["AUTOMATIC_OUTBREAK_ALERT"] & forecasts["OUTBREAK_ALERT_REASON"].eq("NONE")) |
                (forecasts["AUTOMATIC_OUTBREAK_ALERT"] & ~forecasts["OUTBREAK_ALERT_REASON"].eq("NONE"))
            ).all()),
            "cluster_event_table_matches_unique_clusters": bool(
                len(cluster_events) == forecasts.loc[forecasts["RED_CLUSTER_ID"] > 0, ["DATE", "RED_CLUSTER_ID"]].drop_duplicates().shape[0]
            ),
            "cluster_event_origins_are_complete": bool(
                cluster_events.empty or cluster_events["LIKELY_ORIGIN_PSGC"].astype(str).str.len().gt(0).all()
            ),
            "leaflet_map_marker_present": bool(
                "L.map('map'" in (paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html").read_text(encoding="utf-8")
            ),
            "outside_mask_present": bool(
                "outsideMask" in (paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html").read_text(encoding="utf-8")
            ),
            "outbreak_ping_layer_present": bool(
                "outbreak-ping-icon" in (paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html").read_text(encoding="utf-8")
                and "pingLayer" in (paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html").read_text(encoding="utf-8")
            ),
            "permanent_barangay_labels_present": bool(
                "barangay-label-icon" in (paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html").read_text(encoding="utf-8")
                and "labelLayer" in (paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html").read_text(encoding="utf-8")
            ),
            "boundary_gaps_fully_filled": bool(boundary_gap_report.get("fully_covered")),
            "full_screen_map_layout_present": bool(
                "html,body,#map{height:100%" in (paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html").read_text(encoding="utf-8")
                and "class=\"drawer\"" in (paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html").read_text(encoding="utf-8")
            ),
            "live_ranking_and_parameter_table_present": bool(
                "Live dengue cases ranking" in (paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html").read_text(encoding="utf-8")
                and "Live parameter table" in (paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html").read_text(encoding="utf-8")
            ),
            "three_red_barangay_rule_present": bool(
                "Spatial red cluster" in (paths.maps / "ORACLIS_SouthCotabato_Interactive_Forecast_Map.html").read_text(encoding="utf-8")
                and "RED_CLUSTER_OUTBREAK_ALERT" in forecasts.columns
                and "RED_CLUSTER_LIKELY_ORIGIN" in forecasts.columns
                and "OUTBREAK_ALERT_REASON" in forecasts.columns
            ),
        }
        verification = [
            f"Administrative scope: Province of South Cotabato only",
            f"Boundary features: {len(master)}",
            f"Municipalities/city: {len(municipality_master)}",
            f"Calibrated nodes: {int(np.sum(status == 'CALIBRATED_SOUTH_COTABATO'))}",
            f"Forecast rows: {len(forecasts)}",
            f"Forecast months: {len(dates)}",
            f"Spatial edges: {len(graph.edges)}",
            f"Automatic alert rows: {len(alerts)}",
        ] + [f"{key}: {value}" for key, value in verification_checks.items()]
        if not all(verification_checks.values()):
            raise RuntimeError("Final output verification failed:\n" + "\n".join(verification))
        (paths.run / "VERIFICATION_SUMMARY.txt").write_text("\n".join(verification) + "\n", encoding="utf-8")
        (paths.run / "SPATIOTEMPORAL_BAYESIAN_SUCCESS.txt").write_text(
            "ORACLIS spatio-temporal Bayesian simulation completed successfully.\n", encoding="utf-8"
        )


        if not args.mock:
            (ROOT / "outputs" / "latest_spatiotemporal_run.txt").write_text(
                str(paths.run.resolve()), encoding="utf-8"
            )
        logger.info("Completed successfully. Output folder: %s", paths.run)
        return 0
    except Exception as exc:
        logger.error("Spatio-temporal phase failed: %s", exc)
        logger.error(traceback.format_exc())
        (paths.run / "SPATIOTEMPORAL_BAYESIAN_FAILED.txt").write_text(
            f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}", encoding="utf-8"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
