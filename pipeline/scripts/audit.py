"""
DVF data quality audit — terminal visualization.

Usage:
  python -m pipeline.scripts.audit                          # dept + region + country
  python -m pipeline.scripts.audit --geo department         # dept only
  python -m pipeline.scripts.audit --no-chart               # skip plotext charts
  python -m pipeline.scripts.audit --qoq 0.25               # anomaly threshold (default 30%)
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime

import plotext as plt
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..services.dvf import compute_all
from ..services.series import SERIES

console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _quarter_label(dt: datetime) -> str:
    return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"


def _national(rows: list[tuple]) -> list[tuple[datetime, float]]:
    return sorted([(p, v) for g, p, v in rows if g == "FR" and v is not None])


def _by_period(rows: list[tuple]) -> dict[datetime, list[float]]:
    d: dict[datetime, list[float]] = defaultdict(list)
    for _, period, value in rows:
        if value is not None:
            d[period].append(value)
    return d


def _by_geo(rows: list[tuple]) -> dict[str, list[tuple[datetime, float]]]:
    d: dict[str, list] = defaultdict(list)
    for geo_code, period, value in rows:
        if value is not None:
            d[geo_code].append((period, value))
    return {k: sorted(v) for k, v in d.items()}


def _all_periods(all_results: dict) -> list[datetime]:
    periods: set[datetime] = set()
    for rows in all_results.values():
        for _, p, _ in rows:
            periods.add(p)
    return sorted(periods)


# ── 1. Summary table ──────────────────────────────────────────────────────────

def show_summary(all_results: dict, geo_level: str) -> None:
    table = Table(
        title=f"  Series overview — {geo_level}  ",
        box=box.ROUNDED,
        header_style="bold cyan",
        show_lines=False,
        min_width=90,
    )
    table.add_column("Serie", style="bold white", min_width=22)
    table.add_column("Unit", style="dim")
    table.add_column("Geo", justify="right")
    table.add_column("Periods", justify="right")
    table.add_column("Min", justify="right")
    table.add_column("Median", justify="right")
    table.add_column("Max", justify="right")
    table.add_column("YoY", justify="right", min_width=8)

    for s in SERIES:
        rows = all_results.get((s["name"], geo_level), [])
        if not rows:
            table.add_row(s["name"], s.get("unit", ""), "—", "—", "—", "—", "—", "—")
            continue

        values = sorted(v for _, _, v in rows if v is not None)
        geo_count = len({g for g, _, _ in rows})
        period_count = len({p for _, p, _ in rows})
        median = values[len(values) // 2]

        # YoY: compare mean of last 4 quarters vs same 4 quarters prior year (national)
        nat = _national(rows)
        if len(nat) >= 8:
            last4 = [v for _, v in nat[-4:]]
            prev4 = [v for _, v in nat[-8:-4]]
            yoy = (sum(last4) / 4 - sum(prev4) / 4) / (sum(prev4) / 4) * 100
            yoy_text = (
                f"[green]+{yoy:.1f}%[/]" if yoy > 0 else f"[red]{yoy:.1f}%[/]"
            )
        else:
            yoy_text = "—"

        unit = s.get("unit", "")
        fmt = (
            (lambda v: f"{v:,.1f}")
            if unit in ("%",)
            else (lambda v: f"{v:,.0f}")
        )

        table.add_row(
            s["name"],
            unit,
            str(geo_count),
            str(period_count),
            fmt(values[0]),
            fmt(median),
            fmt(values[-1]),
            yoy_text,
        )

    console.print(table)
    console.print()


# ── 2. Trend charts ───────────────────────────────────────────────────────────

CHART_GROUPS = [
    ("Prix au m2 - tous types (France)", ["price_sqm_all", "price_sqm_appt", "price_sqm_house"]),
    ("Prix au m2 - appartements par type (France)", ["price_sqm_t1", "price_sqm_t2", "price_sqm_t3", "price_sqm_t4"]),
    ("Surface mediane - appartements (France)", ["surface_median_t1", "surface_median_t2", "surface_median_t3", "surface_median_t4"]),
    ("Volume & marche (France)", ["transaction_volume", "vefa_share", "land_price_sqm"]),
]

def show_charts(all_results: dict) -> None:
    console.rule("[bold cyan]Trend charts — national[/]")
    console.print()

    for title, series_names in CHART_GROUPS:
        data: dict[str, list] = {}
        labels: list[str] = []

        for name in series_names:
            nat = _national(all_results.get((name, "country"), []))
            if nat:
                data[name] = [v for _, v in nat]
                if not labels:
                    labels = [_quarter_label(p) for p, _ in nat]

        if not data:
            continue

        plt.clf()
        plt.title(title)
        plt.theme("dark")
        plt.plot_size(100, 20)

        for name, ys in data.items():
            plt.plot(list(range(len(ys))), ys, label=name)

        step = max(1, len(labels) // 8)
        ticks = list(range(0, len(labels), step))
        plt.xticks(ticks, [labels[i] for i in ticks])
        plt.show()
        console.print()


# ── 3. Coverage matrix ────────────────────────────────────────────────────────

EXPECTED_GEO_COUNT = {"department": 96, "region": 18, "country": 1, "city": 36_000}


def show_coverage(all_results: dict, geo_level: str) -> None:
    expected = EXPECTED_GEO_COUNT.get(geo_level, 1)
    periods = _all_periods(all_results)
    if not periods:
        return

    table = Table(
        title=f"  Coverage — % {geo_level}s with data  ",
        box=box.SIMPLE_HEAD,
        header_style="bold cyan",
        show_lines=False,
    )
    table.add_column("Serie", style="bold white", min_width=22)
    for p in periods:
        table.add_column(_quarter_label(p), justify="right", min_width=7)

    has_data = False
    for s in SERIES:
        rows = all_results.get((s["name"], geo_level), [])
        if not rows:
            continue
        has_data = True

        by_p = _by_period(rows)
        cells = [s["name"]]
        for p in periods:
            n = len(by_p.get(p, []))
            pct = n / expected * 100
            if pct >= 85:
                cells.append(f"[green]{pct:.0f}%[/]")
            elif pct >= 50:
                cells.append(f"[yellow]{pct:.0f}%[/]")
            elif pct > 0:
                cells.append(f"[red]{pct:.0f}%[/]")
            else:
                cells.append("[dim]—[/]")
        table.add_row(*cells)

    if has_data:
        console.print(table)
        console.print()


# ── 4. Anomaly detection ──────────────────────────────────────────────────────

def show_anomalies(all_results: dict, qoq_threshold: float) -> None:
    console.rule("[bold red]Anomaly detection[/]")
    console.print()

    # Minimum meaningful value per serie type (skip near-zero baselines = sparse noise)
    MIN_BASELINE = {"€/m²": 100, "m²": 5, "transactions": 10, "%": 0.5, "": 1}

    # QoQ jumps
    anomalies = []
    for (serie_name, geo_level), rows in all_results.items():
        if geo_level == "country":
            continue
        serie_def = next((s for s in SERIES if s["name"] == serie_name), {})
        min_val = MIN_BASELINE.get(serie_def.get("unit", ""), 1)

        for geo_code, ts in _by_geo(rows).items():
            for i in range(1, len(ts)):
                prev_p, prev_v = ts[i - 1]
                curr_p, curr_v = ts[i]
                # Skip if baseline is noise (sparse dept/quarter with too few transactions)
                if not prev_v or prev_v < min_val:
                    continue
                change = (curr_v - prev_v) / prev_v
                if abs(change) > qoq_threshold:
                    anomalies.append({
                        "serie": serie_name,
                        "level": geo_level,
                        "geo": geo_code,
                        "from": _quarter_label(prev_p),
                        "to": _quarter_label(curr_p),
                        "pct": change * 100,
                        "before": prev_v,
                        "after": curr_v,
                    })

    anomalies.sort(key=lambda x: abs(x["pct"]), reverse=True)

    if anomalies:
        table = Table(
            title=f"  QoQ jumps > {qoq_threshold*100:.0f}% ({len(anomalies)} total, top 40 shown)  ",
            box=box.ROUNDED,
            header_style="bold red",
        )
        table.add_column("Serie", style="bold", min_width=20)
        table.add_column("Level")
        table.add_column("Geo")
        table.add_column("From")
        table.add_column("To")
        table.add_column("Change", justify="right")
        table.add_column("Before", justify="right")
        table.add_column("After", justify="right")

        for a in anomalies[:40]:
            color = "green" if a["pct"] > 0 else "red"
            table.add_row(
                a["serie"], a["level"], a["geo"],
                a["from"], a["to"],
                f"[{color}]{a['pct']:+.1f}%[/]",
                f"{a['before']:,.0f}",
                f"{a['after']:,.0f}",
            )
        console.print(table)
    else:
        console.print(Panel("[green]No QoQ anomalies detected[/]", box=box.ROUNDED))

    console.print()

    # Cross-series ordering checks (national averages):
    #   surface_median: T1 < T2 < T3 < T4  (larger apartments)
    #   price_sqm:      T1 > T2 > T3 > T4  (studios cost more per m² in France)
    checks = [
        ("surface_median", ["surface_median_t1","surface_median_t2","surface_median_t3","surface_median_t4"], "asc"),
        ("price_sqm",      ["price_sqm_t1","price_sqm_t2","price_sqm_t3","price_sqm_t4"],                    "desc"),
    ]
    issues = []
    ok_lines = []
    for label, group, direction in checks:
        avgs = {}
        for name in group:
            nat = _national(all_results.get((name, "country"), []))
            if nat:
                avgs[name] = sum(v for _, v in nat) / len(nat)
        for i in range(len(group) - 1):
            a, b = group[i], group[i + 1]
            if a not in avgs or b not in avgs:
                continue
            violated = (avgs[a] >= avgs[b]) if direction == "asc" else (avgs[a] <= avgs[b])
            if violated:
                op = ">=" if direction == "asc" else "<="
                issues.append(f"{a} ({avgs[a]:.0f}) {op} {b} ({avgs[b]:.0f})")
        if not any(a in " ".join(issues) for a in group):
            arrow = "T1 < T2 < T3 < T4" if direction == "asc" else "T1 > T2 > T3 > T4"
            ok_lines.append(f"[green]OK  {label}: {arrow}[/]")

    if issues:
        body = "\n".join(f"[red]x {i}[/]" for i in issues)
        if ok_lines:
            body = "\n".join(ok_lines) + "\n" + body
        console.print(Panel(body, title="Cross-series ordering", box=box.ROUNDED))
    else:
        console.print(Panel(
            "\n".join(ok_lines),
            title="Cross-series ordering",
            box=box.ROUNDED,
        ))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Force UTF-8 stdout on Windows (plotext + rich use Unicode chars)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("--geo", default="department,region,country")
    parser.add_argument("--no-chart", action="store_true", help="Skip plotext charts")
    parser.add_argument("--qoq", type=float, default=0.30, help="QoQ anomaly threshold (default 0.30)")
    args = parser.parse_args()

    geo_levels = [g.strip() for g in args.geo.split(",")]

    console.print(Panel(
        f"[bold cyan]DVF Data Quality Audit[/]\n[dim]{datetime.now().strftime('%Y-%m-%d %H:%M')}  |  geo: {', '.join(geo_levels)}[/]",
        box=box.DOUBLE,
        expand=False,
    ))
    console.print()

    all_results = compute_all(geo_levels)  # type: ignore[arg-type]

    for geo in geo_levels:
        console.rule(f"[bold white]{geo.upper()}[/]")
        console.print()
        show_summary(all_results, geo)
        show_coverage(all_results, geo)

    if not args.no_chart:
        show_charts(all_results)

    show_anomalies(all_results, args.qoq)


if __name__ == "__main__":
    main()
