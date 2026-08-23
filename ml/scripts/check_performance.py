"""
Reads run_metadata.json from every ml/artifacts/<model>/latest/ on disk and
prints whether each model currently beats its baseline — the real, live
answer to "are the models performant right now", not a recap from memory.

Run any time after training/retraining:
  python -m ml.scripts.check_performance
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.config import ARTIFACTS_DIR  # noqa: E402


def _load(model: str) -> dict | None:
    path = ARTIFACTS_DIR / model / "latest" / "run_metadata.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_pct(x: float) -> str:
    return f"{x*100:.1f}%"


def check_clustering() -> None:
    d = _load("clustering")
    print("\n=== Modèle 1 — Clustering ===")
    if not d:
        print("  Pas encore lancé (ml/artifacts/clustering/latest/ absent)")
        return
    sil = d["silhouette_at_chosen_k"]
    f_stat = d["external_validation_price"]["anova_f"]
    p_val = d["external_validation_price"]["anova_p"]
    sil_verdict = "structure forte" if sil > 0.5 else "structure faible-à-raisonnable" if sil > 0.25 else "pas de structure réelle"
    print(f"  k={d['k_chosen']}, silhouette={sil:.4f} ({sil_verdict})")
    print(f"  Validation externe (prix jamais en feature): F={f_stat:.2f}, p={p_val:.2e}"
          f" ({'significatif' if p_val < 0.05 else 'PAS significatif — clusters arbitraires'})")


def check_regression(model: str, label: str) -> None:
    d = _load(model)
    print(f"\n=== {label} ===")
    if not d:
        print("  Pas encore lancé")
        return
    m = d.get("model_metrics_test") or d.get("model_metrics")
    b = d.get("baseline_metrics_test") or d.get("baseline_metrics")
    if not m or not b:
        return
    beats = m["mae"] < b["mae"]
    gain = (b["mae"] - m["mae"]) / b["mae"]
    print(f"  R²={m['r2']:.4f} | MAE={m['mae']:.1f} vs baseline={b['mae']:.1f}"
          f" ({'BAT' if beats else 'PERD CONTRE'} la baseline, {_fmt_pct(gain)} de gain)")


def check_forecasting() -> None:
    d = _load("forecasting")
    print("\n=== Modèle 3 — Forecasting ===")
    if not d:
        print("  Pas encore lancé")
        return
    for h, m in d["metrics_by_horizon"].items():
        mm, bm = m["model_metrics_test"], m["baseline_metrics_test"]
        beats = mm["mae"] < bm["mae"]
        cov = m.get("interval_coverage_test")
        cov_str = f", coverage={_fmt_pct(cov)}" if cov is not None else ""
        print(f"  t+{h}: R²={mm['r2']:.4f}, MAE={mm['mae']:.1f} vs {bm['mae']:.1f}"
              f" ({'BAT' if beats else 'PERD CONTRE'} baseline){cov_str}")
    n_flagged = d.get("n_flagged_extrapolation_risk")
    n_total = d.get("n_communes_with_t4_forecast")
    if n_flagged is not None and n_total:
        print(f"  extrapolation_risk: {n_flagged}/{n_total} ({_fmt_pct(n_flagged/n_total)}) communes à prévision peu fiable")


def check_quantile() -> None:
    d = _load("quantile")
    print("\n=== Modèle 4 — Quantile ===")
    if not d:
        print("  Pas encore lancé")
        return
    cov_raw = d.get("empirical_coverage_q10_q90_raw")
    cov_conf = d.get("empirical_coverage_q10_q90_conformal")
    target = 0.80
    print(f"  Coverage brute: {_fmt_pct(cov_raw)} (cible {_fmt_pct(target)}, écart {_fmt_pct(abs(cov_raw-target))})")
    if cov_conf is not None:
        print(f"  Coverage conformal: {_fmt_pct(cov_conf)} (écart {_fmt_pct(abs(cov_conf-target))}) — RECOMMANDÉE")


def main() -> None:
    print("État réel des modèles — lu directement depuis ml/artifacts/*/latest/run_metadata.json")
    check_clustering()
    check_regression("price_model", "Modèle 2 — Prix/m²")
    check_forecasting()
    check_quantile()
    check_regression("price_hedonic", "Modèle hédonique (extension)")
    print()


if __name__ == "__main__":
    main()
