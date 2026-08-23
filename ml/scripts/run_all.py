"""
Runs the 4 models in the order recommended by CONTEXTE_ML_MEMOIRE.md §9:
clustering -> price model (+ opportunity score) -> forecasting -> quantile.

Each model's individual module is also directly runnable on its own, e.g.:
  python -m ml.models.clustering --dept 77

Run (no flags needed — defaults already match the "official" PERFORMANCE.md
version: clustering k=3 weighted, price_model tuned):
  python -m ml.scripts.run_all [--dept 77] [--skip-quantile]
  python -m ml.scripts.run_all --no-weighted --no-tune --k 0   # old fast/unweighted behavior
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.models import clustering, forecasting, price_model, quantile  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--k", type=int, default=3, help="Force k for clustering (0 = pick by max silhouette)")
    parser.add_argument("--weighted", action=argparse.BooleanOptionalAction, default=True,
                         help="Reweight clustering features by ANOVA F-stat; --no-weighted to disable (see EXPERIMENTS_LOG.md)")
    parser.add_argument("--skip-quantile", action="store_true", help="Skip Model 4 (optional per spec)")
    parser.add_argument("--tune", action=argparse.BooleanOptionalAction, default=True,
                         help="RandomizedSearchCV for price_model; --no-tune for fixed hyperparameters (faster)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    k = None if args.k == 0 else args.k

    print("=" * 70)
    print("Model 1 — Clustering")
    print("=" * 70)
    clustering.run(dept_filter=args.dept, k=k, weighted=args.weighted, dry_run=args.dry_run)

    print("\n" + "=" * 70)
    print("Model 2 — Price / opportunity score")
    print("=" * 70)
    price_model.run(dept_filter=args.dept, dry_run=args.dry_run, tune=args.tune)

    print("\n" + "=" * 70)
    print("Model 3 — Forecasting")
    print("=" * 70)
    forecasting.run(dept_filter=args.dept, dry_run=args.dry_run)

    if not args.skip_quantile:
        print("\n" + "=" * 70)
        print("Model 4 — Quantile regression")
        print("=" * 70)
        quantile.run(dept_filter=args.dept, dry_run=args.dry_run)

    print("\nAll done. Artifacts under ml/artifacts/<model_name>/latest/")


if __name__ == "__main__":
    main()
