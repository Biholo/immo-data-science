"""
Shared matplotlib style for ml/scripts/generate_charts.py — static PNG
figures for the mémoire (print/PDF target, no dark-mode toggle to serve).

Palette from the dataviz skill's validated default palette
(references/palette.md): categorical slots 1-3 (blue/orange/aqua) for
clusters and multi-series charts, status red/green for
extrapolation_risk/target-lines, chart chrome tokens for ink/grid.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Categorical (fixed order — never cycle/reassign per filter)
CAT_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
COLOR_MODEL = "#2a78d6"       # categorical slot 1 — "the model"
COLOR_BASELINE = "#898781"    # muted ink — baseline is a reference, not a series
COLOR_CRITICAL = "#d03b3b"    # status critical — extrapolation_risk, outliers
COLOR_GOOD = "#0ca30c"        # status good — target lines

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
AXIS = "#c3c2b7"


def setup_style() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.edgecolor": AXIS,
        "axes.labelcolor": INK_SECONDARY,
        "text.color": INK_PRIMARY,
        "xtick.color": INK_MUTED,
        "ytick.color": INK_MUTED,
        "grid.color": GRIDLINE,
        "grid.linewidth": 0.6,
        "font.family": "sans-serif",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 150,
        "savefig.dpi": 150,
        "savefig.facecolor": SURFACE,
        "legend.frameon": False,
    })


def save(fig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {path.name}")
