import numpy as np
import pandas as pd


def _make_date_splits_python(
    identity: pd.DataFrame,
    holdout_days: int = 14,
    validation_days: int = 7,
    n_splits: int = 3,
) -> tuple[list[tuple[np.ndarray, np.ndarray]], np.ndarray]:
    dates = pd.Index(sorted(pd.to_datetime(identity["日期"]).unique()))
    required = holdout_days + validation_days * n_splits + 1
    if len(dates) < required:
        raise ValueError(f"至少需要{required}个完整日期，当前只有{len(dates)}个")
    holdout_dates = dates[-holdout_days:]
    development_dates = dates[:-holdout_days]
    folds = []
    for split in range(n_splits, 0, -1):
        valid_end = len(development_dates) - validation_days * (split - 1)
        valid_start = valid_end - validation_days
        train_dates = development_dates[:valid_start]
        valid_dates = development_dates[valid_start:valid_end]
        train_idx = np.flatnonzero(identity["日期"].isin(train_dates).to_numpy())
        valid_idx = np.flatnonzero(identity["日期"].isin(valid_dates).to_numpy())
        folds.append((train_idx, valid_idx))
    holdout_idx = np.flatnonzero(identity["日期"].isin(holdout_dates).to_numpy())
    return folds, holdout_idx


def previous_day_baseline(y: pd.Series) -> pd.Series:
    return _native_baseline(y, 96)


def seven_day_baseline(y: pd.Series) -> pd.Series:
    return _native_baseline(y, 96 * 7)


def _evaluate_predictions_python(
    y_true: np.ndarray | pd.Series,
    y_pred: np.ndarray | pd.Series,
    identity: pd.DataFrame,
) -> tuple[dict[str, float], pd.DataFrame, pd.DataFrame]:
    result = identity[["日期", "时刻"]].reset_index(drop=True).copy()
    result["absolute_error"] = np.abs(np.asarray(y_true) - np.asarray(y_pred))
    metrics = {"mae": float(result["absolute_error"].mean())}
    daily = result.groupby("日期", as_index=False)["absolute_error"].mean().rename(columns={"absolute_error": "mae"})
    by_slot = result.groupby("时刻", as_index=False)["absolute_error"].mean().rename(columns={"absolute_error": "mae"})
    return metrics, daily, by_slot


def make_date_splits(identity, holdout_days=14, validation_days=7, n_splits=3):
    from .data import _invoke_forecast as invoke
    result = invoke("forecast", "splits", {"dates": pd.to_datetime(identity["日期"]).dt.strftime("%Y-%m-%d").tolist(),
        "holdout_days": holdout_days, "validation_days": validation_days, "n_splits": n_splits})
    if result is None:
        return _make_date_splits_python(identity, holdout_days, validation_days, n_splits)
    return [(np.array(a, dtype=np.intp), np.array(b, dtype=np.intp)) for a, b in result["folds"]], np.array(result["holdout"], dtype=np.intp)


def evaluate_predictions(y_true, y_pred, identity):
    from .data import _invoke_forecast as invoke
    def values(a):
        return [None if pd.isna(v) else ("Infinity" if v == np.inf else "-Infinity" if v == -np.inf else float(v)) for v in np.asarray(a)]
    result = invoke("forecast", "evaluate", {"dates": pd.to_datetime(identity["日期"]).dt.strftime("%Y-%m-%d").tolist(),
        "slots": identity["时刻"].tolist(), "true": values(y_true), "pred": values(y_pred)})
    if result is None:
        return _evaluate_predictions_python(y_true, y_pred, identity)
    daily = pd.DataFrame(result["daily"], columns=["日期", "mae"])
    if pd.api.types.is_datetime64_any_dtype(identity["日期"].dtype):
        daily["日期"] = pd.to_datetime(daily["日期"]).astype(identity["日期"].dtype)
    else:
        daily["日期"] = daily["日期"].astype(identity["日期"].dtype)
    by_slot = pd.DataFrame(result["slots"], columns=["时刻", "mae"])
    by_slot["时刻"] = by_slot["时刻"].astype(identity["时刻"].dtype)
    for frame in (daily, by_slot):
        frame["mae"] = frame["mae"].astype(float)
    return {"mae": float(result["mae"]) if result["mae"] is not None else float("nan")}, daily, by_slot


def _native_baseline(y, shift):
    from .data import _invoke_forecast as invoke
    result = invoke("forecast", "baseline", {"values": [None if pd.isna(v) else ("Infinity" if v == np.inf else "-Infinity" if v == -np.inf else float(v)) for v in y], "shift": shift})
    if result is None:
        return y.shift(shift)
    dtype = y.dtype if (pd.api.types.is_float_dtype(y.dtype) or pd.api.types.is_extension_array_dtype(y.dtype)) else "float64"
    return pd.Series(result, index=y.index, name=y.name, dtype=dtype)
