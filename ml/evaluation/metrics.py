"""Shared regression metrics — MAE, RMSE, R², pinball loss."""

from __future__ import annotations

import numpy as np


def mae(y_true, y_pred) -> float:
    y_true, y_pred = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    return float(np.mean(np.abs(y_true - y_pred)))


def rmse(y_true, y_pred) -> float:
    y_true, y_pred = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def r2(y_true, y_pred) -> float:
    y_true, y_pred = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - y_true.mean()) ** 2)
    if ss_tot == 0:
        return float("nan")
    return float(1 - ss_res / ss_tot)


def pinball_loss(y_true, y_pred, tau: float) -> float:
    """L_tau(y, yhat) = tau*max(0,y-yhat) + (1-tau)*max(0,yhat-y), averaged."""
    y_true, y_pred = np.asarray(y_true, dtype=float), np.asarray(y_pred, dtype=float)
    diff = y_true - y_pred
    loss = np.where(diff >= 0, tau * diff, (tau - 1) * diff)
    return float(np.mean(loss))


def regression_report(y_true, y_pred) -> dict:
    return {"mae": mae(y_true, y_pred), "rmse": rmse(y_true, y_pred), "r2": r2(y_true, y_pred)}


def print_comparison(label: str, baseline: dict, model: dict) -> None:
    print(f"\n{label}")
    print(f"  {'':12s} {'baseline':>12s} {'model':>12s}")
    for k in ("mae", "rmse", "r2"):
        b, m = baseline.get(k), model.get(k)
        print(f"  {k:12s} {b:12.4f} {m:12.4f}")
