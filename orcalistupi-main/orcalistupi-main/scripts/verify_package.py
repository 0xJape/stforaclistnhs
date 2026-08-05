from __future__ import annotations

import ast
import json
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    required = [
        ROOT / "README.md",
        ROOT / "requirements.txt",
        ROOT / "spatiotemporal_config.json",
        ROOT / "models/weighted_ensemble.json",
        ROOT / "models/weighted_ensemble_test_predictions.csv",
        ROOT / "data/ORACLIS_Monthly_Barangay_Data_Corrected.csv",
        ROOT / "data/south_cotabato_localities_2023.csv",
        ROOT / "src/run_spatiotemporal_bayesian.py",
        ROOT / "src/south_cotabato_boundaries.py",
        ROOT / "src/spatiotemporal_runtime_api.py",
        ROOT / "integration/ingest_live_observations.py",
        ROOT / "integration/send_make_alerts.py",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing files: " + ", ".join(missing))
    for path in list((ROOT / "src").glob("*.py")) + list((ROOT / "integration").glob("*.py")):
        py_compile.compile(str(path), doraise=True)
        ast.parse(path.read_text(encoding="utf-8"))
    json.loads((ROOT / "spatiotemporal_config.json").read_text(encoding="utf-8"))
    artifact = json.loads((ROOT / "models/weighted_ensemble.json").read_text(encoding="utf-8"))
    if abs(sum(float(value) for value in artifact["weights"].values()) - 1.0) > 1e-6:
        raise ValueError("Ensemble weights do not sum to one")
    with tempfile.TemporaryDirectory(prefix="oraclis_verify_") as temp_dir:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "src/run_spatiotemporal_bayesian.py"),
                "--config",
                str(ROOT / "spatiotemporal_config.json"),
                "--self-test",
                "--mock",
                "--output-dir",
                str(Path(temp_dir) / "mock_run"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=240,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stdout + "\n" + result.stderr)
        success = Path(temp_dir) / "mock_run/SPATIOTEMPORAL_BAYESIAN_SUCCESS.txt"
        if not success.exists():
            raise RuntimeError("Mock simulation did not create the success marker")
    result = subprocess.run([sys.executable, str(ROOT / "integration/send_make_alerts.py"), "--self-test"], cwd=ROOT, text=True, capture_output=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stdout + "\n" + result.stderr)
    print("ORACLIS package verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
