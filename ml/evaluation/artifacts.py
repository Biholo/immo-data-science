"""
Run artifact conventions — every model script (ml/models/*.py) writes through
these helpers so outputs are consistent and match the "Artefacts à sauvegarder"
checklist in CONTEXTE_ML_MEMOIRE.md §10 (date, features, hyperparams, split
sizes, metrics, training time, serialized model, predictions, examples, limits).

Layout: ml/artifacts/<model_name>/<timestamp>/{run_metadata.json, model.joblib,
predictions.csv, ...}, mirrored into ml/artifacts/<model_name>/latest/ for easy
reference when writing the mémoire (path never changes between runs).
"""

from __future__ import annotations

import json
import shutil
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd

from ml.config import ARTIFACTS_DIR


def new_run_dir(model_name: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = ARTIFACTS_DIR / model_name / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


@contextmanager
def timer():
    """Usage: with timer() as t: ...  → t['elapsed_seconds'] populated after the block."""
    start = time.perf_counter()
    result: dict = {}
    try:
        yield result
    finally:
        result["elapsed_seconds"] = round(time.perf_counter() - start, 2)


def save_metadata(run_dir: Path, metadata: dict) -> None:
    payload = {"run_timestamp": datetime.now().isoformat(), **metadata}
    (run_dir / "run_metadata.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )


def save_model(run_dir: Path, model, name: str = "model.joblib") -> None:
    joblib.dump(model, run_dir / name)


def save_dataframe(run_dir: Path, df: pd.DataFrame, name: str) -> None:
    df.to_csv(run_dir / name, index=True)


def publish_latest(model_name: str, run_dir: Path) -> Path:
    """Copies run_dir into artifacts/<model_name>/latest/ (plain copy — no symlink, Windows-safe)."""
    latest_dir = ARTIFACTS_DIR / model_name / "latest"
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.copytree(run_dir, latest_dir)
    return latest_dir
