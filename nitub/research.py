"""Multi-strategy research harness with train/test split (honest OOS).

Vectorized daily backtests — faster than Cerebro for parameter grids.
Writes nitub/state/backtests/*.json and promotes a winner config.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import store
from .paths import STATE_DIR, ensure_dirs

DEFAULT_UNIVERSE = ["AAPL", "MSFT", "GOOGL", "AMZN", "SPY"]
DEFAULT_STOCKS = ["AAPL", "MSFT", "GOOGL", "AMZN"]
BACKTESTS_DIR = STATE_DIR / "backtests"


@dataclass
class Window:
    name: str
    start: str
    end: str | None  # None = last available bar


@dataclass
class StratSpec:
    id: str
    family: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunMetrics:
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float | None
    n_days: int
    end_equity: float
    start_equity: float
    exposure_pct: float  # mean invested fraction


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def download_prices(
    symbols: list[str],
    start: str,
    end: str | None = None,
) -> pd.DataFrame:
    """Adjusted close panel, columns = tickers, DatetimeIndex."""
    import yfinance as yf

    raw = yf.download(
        symbols,
        start=start,
        end=end,
        progress=False,
        auto_adjust=True,
        group_by="ticker",
        threads=True,
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"yfinance вернул пусто для {symbols}")

    closes: dict[str, pd.Series] = {}
    if len(symbols) == 1:
        sym = symbols[0]
        col = "Close" if "Close" in raw.columns else "close"
        closes[sym] = raw[col].astype(float)
    else:
        for sym in symbols:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    # yfinance can be (ticker, OHLCV) or (OHLCV, ticker)
                    level0 = raw.columns.get_level_values(0)
                    if sym in level0:
                        sub = raw[sym]
                    else:
                        sub = raw.xs(sym, axis=1, level=1)
                else:
                    sub = raw
                col = "Close" if "Close" in sub.columns else "close"
                s = sub[col].astype(float)
                closes[sym] = s
            except Exception:
                continue

    px = pd.DataFrame(closes).dropna(how="all").sort_index()
    px.index = pd.to_datetime(px.index).tz_localize(None)
    missing = [s for s in symbols if s not in px.columns or px[s].isna().all()]
    if missing:
        raise RuntimeError(f"Не удалось загрузить: {missing}")
    return px[symbols]


def _slice(px: pd.DataFrame, start: str, end: str | None) -> pd.DataFrame:
    out = px.loc[px.index >= pd.Timestamp(start)]
    if end:
        out = out.loc[out.index <= pd.Timestamp(end)]
    return out


def equity_metrics(equity: pd.Series, *, start_cash: float) -> RunMetrics:
    eq = equity.dropna().astype(float)
    if eq.empty:
        return RunMetrics(0.0, 0.0, None, 0, start_cash, start_cash, 0.0)
    rets = eq.pct_change().dropna()
    peak = eq.cummax()
    dd = (eq / peak - 1.0) * 100.0
    max_dd = float(dd.min()) if len(dd) else 0.0
    total_ret = float((eq.iloc[-1] / start_cash - 1.0) * 100.0)
    sharpe = None
    if len(rets) > 5 and rets.std() > 1e-12:
        sharpe = float(rets.mean() / rets.std() * math.sqrt(252))
    # exposure approx: days where equity changed vs cash floor is hard;
    # use fraction of days with |daily return| > tiny or equity > cash*1.001
    return RunMetrics(
        total_return_pct=round(total_ret, 4),
        max_drawdown_pct=round(abs(max_dd), 4),
        sharpe=round(sharpe, 4) if sharpe is not None else None,
        n_days=int(len(eq)),
        end_equity=round(float(eq.iloc[-1]), 2),
        start_equity=float(start_cash),
        exposure_pct=0.0,
    )


def _apply_weights(
    px: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    start_cash: float,
    cost_bps: float = 10.0,
) -> pd.Series:
    """Daily portfolio from target weights (rows sum ~0..1). Costs on turnover."""
    w = weights.reindex(px.index).fillna(0.0)
    # lag signals one day (no look-ahead)
    w = w.shift(1).fillna(0.0)
    rets = px.pct_change().fillna(0.0)
    port_ret = (w * rets).sum(axis=1)
    # transaction cost: cost_bps on L1 turnover / 2 (one-way)
    turn = w.diff().abs().sum(axis=1).fillna(0.0)
    costs = turn * (cost_bps / 10000.0)
    net = port_ret - costs
    equity = (1.0 + net).cumprod() * start_cash
    equity.iloc[0] = start_cash
    return equity


# --- signal builders (return DataFrame of target weights) ---


def _stock_cols(px: pd.DataFrame, exclude_spy: bool = True) -> list[str]:
    cols = list(px.columns)
    if exclude_spy and "SPY" in cols and len(cols) > 1:
        return [c for c in cols if c != "SPY"]
    return cols


def signal_buy_hold_equal(px: pd.DataFrame, *, exclude_spy: bool = True, **_kw: Any) -> pd.DataFrame:
    cols = _stock_cols(px, exclude_spy=exclude_spy)
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    if not cols:
        return w
    for c in cols:
        w[c] = 1.0 / len(cols)
    return w


def signal_buy_hold_spy(px: pd.DataFrame, **_kw: Any) -> pd.DataFrame:
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    if "SPY" in w.columns:
        w["SPY"] = 1.0
    else:
        w.iloc[:, 0] = 1.0
    return w


def signal_sma_cross(
    px: pd.DataFrame,
    *,
    fast: int = 10,
    slow: int = 30,
    exclude_spy: bool = True,
    **_kw: Any,
) -> pd.DataFrame:
    cols = _stock_cols(px, exclude_spy=exclude_spy)
    sub = px[cols]
    fast_ma = sub.rolling(fast).mean()
    slow_ma = sub.rolling(slow).mean()
    long = (fast_ma > slow_ma) & fast_ma.notna() & slow_ma.notna()
    active = long.astype(float)
    row_sum = active.sum(axis=1).replace(0, np.nan)
    w_sub = active.div(row_sum, axis=0).fillna(0.0)
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    w[cols] = w_sub
    return w


def signal_trend_sma(
    px: pd.DataFrame,
    *,
    period: int = 200,
    exclude_spy: bool = True,
    **_kw: Any,
) -> pd.DataFrame:
    cols = _stock_cols(px, exclude_spy=exclude_spy)
    sub = px[cols]
    sma = sub.rolling(period).mean()
    long = (sub > sma) & sma.notna()
    active = long.astype(float)
    row_sum = active.sum(axis=1).replace(0, np.nan)
    w_sub = active.div(row_sum, axis=0).fillna(0.0)
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    w[cols] = w_sub
    return w


def signal_momentum(
    px: pd.DataFrame,
    *,
    lookback: int = 60,
    top_k: int = 2,
    abs_filter: bool = True,
    exclude_spy: bool = True,
    rebalance_every: int = 1,
    **_kw: Any,
) -> pd.DataFrame:
    cols = _stock_cols(px, exclude_spy=exclude_spy)
    sub = px[cols]
    mom = sub / sub.shift(lookback) - 1.0
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    last = None
    for i in range(len(px)):
        if rebalance_every > 1 and last is not None and (i % rebalance_every) != 0:
            w.iloc[i] = last
            continue
        row = mom.iloc[i]
        if row.isna().all():
            last = w.iloc[i]
            continue
        candidates = row.dropna()
        if abs_filter:
            candidates = candidates[candidates > 0]
        if candidates.empty:
            last = w.iloc[i]
            continue
        pick = candidates.nlargest(min(top_k, len(candidates)))
        for t in pick.index:
            w.iloc[i, w.columns.get_loc(t)] = 1.0 / len(pick)
        last = w.iloc[i]
    return w


def signal_copycat_hold(
    px: pd.DataFrame,
    *,
    tickers: list[str] | None = None,
    weights: list[float] | None = None,
    trend_period: int = 0,
    **_kw: Any,
) -> pd.DataFrame:
    """Static sleeve (e.g. Berkshire-like) optionally gated by SPY/own trend."""
    tickers = tickers or ["AAPL", "MSFT", "GOOGL"]
    tickers = [t for t in tickers if t in px.columns]
    if not tickers:
        return pd.DataFrame(0.0, index=px.index, columns=px.columns)
    if weights is None:
        weights = [1.0 / len(tickers)] * len(tickers)
    # normalize to available
    ww = np.array(weights[: len(tickers)], dtype=float)
    ww = ww / ww.sum()
    base = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    for t, wt in zip(tickers, ww):
        base[t] = wt
    if trend_period and trend_period > 0:
        # gate: invest only when SPY > SMA(trend) if SPY present, else equal-weight basket > SMA
        if "SPY" in px.columns:
            sma = px["SPY"].rolling(trend_period).mean()
            on = (px["SPY"] > sma) & sma.notna()
        else:
            basket = (px[tickers] * ww).sum(axis=1)
            sma = basket.rolling(trend_period).mean()
            on = (basket > sma) & sma.notna()
        base = base.mul(on.astype(float), axis=0)
    return base


def signal_dual_momentum(
    px: pd.DataFrame,
    *,
    lookback: int = 126,
    exclude_spy: bool = True,
    **_kw: Any,
) -> pd.DataFrame:
    """Relative + absolute momentum: hold best asset if mom>0 else cash."""
    cols = _stock_cols(px, exclude_spy=exclude_spy)
    mom = px[cols] / px[cols].shift(lookback) - 1.0
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    for i in range(len(px)):
        row = mom.iloc[i].dropna()
        if row.empty:
            continue
        best = row.idxmax()
        if row[best] > 0:
            w.loc[w.index[i], best] = 1.0
    return w


def signal_vol_target_equal(
    px: pd.DataFrame,
    *,
    vol_lookback: int = 20,
    target_vol: float = 0.15,
    exclude_spy: bool = True,
    max_leverage: float = 1.0,
    **_kw: Any,
) -> pd.DataFrame:
    """Equal-weight stocks, scale gross exposure to target annualized vol (cap 1x)."""
    base = signal_buy_hold_equal(px, exclude_spy=exclude_spy)
    rets = px.pct_change()
    # portfolio vol of equal sleeve
    cols = _stock_cols(px, exclude_spy=exclude_spy)
    if not cols:
        return base
    eq_ret = rets[cols].mean(axis=1)
    vol = eq_ret.rolling(vol_lookback).std() * math.sqrt(252)
    scale = (target_vol / vol.replace(0, np.nan)).clip(upper=max_leverage).fillna(0.0)
    return base.mul(scale, axis=0)


def signal_spy_trend_stocks(
    px: pd.DataFrame,
    *,
    period: int = 200,
    **_kw: Any,
) -> pd.DataFrame:
    """Equal-weight stocks only when SPY > SMA(period); else cash."""
    base = signal_buy_hold_equal(px, exclude_spy=True)
    if "SPY" not in px.columns:
        return base
    sma = px["SPY"].rolling(period).mean()
    on = (px["SPY"] > sma) & sma.notna()
    return base.mul(on.astype(float), axis=0)


SIGNAL_FN: dict[str, Callable[..., pd.DataFrame]] = {
    "buy_hold_equal": signal_buy_hold_equal,
    "buy_hold_spy": signal_buy_hold_spy,
    "sma_cross": signal_sma_cross,
    "trend_sma": signal_trend_sma,
    "momentum": signal_momentum,
    "copycat_hold": signal_copycat_hold,
    "dual_momentum": signal_dual_momentum,
    "vol_target_equal": signal_vol_target_equal,
    "spy_trend_stocks": signal_spy_trend_stocks,
}


def build_grid() -> list[StratSpec]:
    specs: list[StratSpec] = [
        StratSpec("bh_equal", "buy_hold_equal", {"exclude_spy": True}),
        StratSpec("bh_spy", "buy_hold_spy", {}),
        StratSpec(
            "copycat_static",
            "copycat_hold",
            {"tickers": ["AAPL", "MSFT", "GOOGL"], "weights": [0.4, 0.3, 0.3], "trend_period": 0},
        ),
        StratSpec(
            "copycat_mega",
            "copycat_hold",
            {
                "tickers": ["AAPL", "MSFT", "GOOGL", "AMZN"],
                "weights": [0.25, 0.25, 0.25, 0.25],
                "trend_period": 0,
            },
        ),
        StratSpec(
            "copycat_trend200",
            "copycat_hold",
            {"tickers": ["AAPL", "MSFT", "GOOGL"], "weights": [0.4, 0.3, 0.3], "trend_period": 200},
        ),
        StratSpec(
            "copycat_berk_trend150",
            "copycat_hold",
            {"tickers": ["AAPL", "MSFT", "GOOGL"], "weights": [0.42, 0.33, 0.25], "trend_period": 150},
        ),
        StratSpec("spy_gate_200", "spy_trend_stocks", {"period": 200}),
        StratSpec("spy_gate_150", "spy_trend_stocks", {"period": 150}),
        StratSpec("spy_gate_100", "spy_trend_stocks", {"period": 100}),
        StratSpec("voltarget_15", "vol_target_equal", {"vol_lookback": 20, "target_vol": 0.15}),
        StratSpec("voltarget_12", "vol_target_equal", {"vol_lookback": 20, "target_vol": 0.12}),
    ]
    for fast, slow in [(5, 20), (10, 30), (20, 50), (50, 200)]:
        specs.append(StratSpec(f"sma_{fast}_{slow}", "sma_cross", {"fast": fast, "slow": slow}))
    for period in [50, 100, 150, 200]:
        specs.append(StratSpec(f"trend_{period}", "trend_sma", {"period": period}))
    for lb, k, reb in [
        (21, 2, 1),
        (42, 2, 1),
        (42, 2, 5),
        (60, 2, 1),
        (60, 2, 21),
        (60, 3, 1),
        (126, 2, 21),
        (126, 1, 21),
        (252, 2, 21),
    ]:
        specs.append(
            StratSpec(
                f"mom_lb{lb}_k{k}_r{reb}",
                "momentum",
                {"lookback": lb, "top_k": k, "abs_filter": True, "rebalance_every": reb},
            )
        )
    for lb in [63, 126, 252]:
        specs.append(StratSpec(f"dualmom_{lb}", "dual_momentum", {"lookback": lb}))
    return specs


def run_one(
    px: pd.DataFrame,
    spec: StratSpec,
    *,
    start_cash: float = 100_000.0,
    cost_bps: float = 10.0,
) -> tuple[pd.Series, RunMetrics]:
    fn = SIGNAL_FN[spec.family]
    weights = fn(px, **spec.params)
    # drop SPY from non-benchmark strategies that shouldn't force SPY weight
    # (weights already respect columns)
    equity = _apply_weights(px, weights, start_cash=start_cash, cost_bps=cost_bps)
    # exposure: mean sum of absolute weights (lagged already inside)
    w = weights.shift(1).fillna(0.0)
    exposure = float(w.sum(axis=1).mean() * 100.0)
    m = equity_metrics(equity, start_cash=start_cash)
    m.exposure_pct = round(exposure, 2)
    return equity, m


def evaluate_window(
    px_full: pd.DataFrame,
    specs: list[StratSpec],
    window: Window,
    *,
    start_cash: float = 100_000.0,
    cost_bps: float = 10.0,
) -> list[dict]:
    px = _slice(px_full, window.start, window.end)
    rows: list[dict] = []
    for spec in specs:
        try:
            _, m = run_one(px, spec, start_cash=start_cash, cost_bps=cost_bps)
        except Exception as e:
            rows.append(
                {
                    "id": spec.id,
                    "family": spec.family,
                    "params": spec.params,
                    "window": window.name,
                    "error": str(e),
                }
            )
            continue
        rows.append(
            {
                "id": spec.id,
                "family": spec.family,
                "params": spec.params,
                "window": window.name,
                "from": str(px.index[0].date()) if len(px) else window.start,
                "to": str(px.index[-1].date()) if len(px) else window.end,
                **asdict(m),
            }
        )
    return rows


def _beats_oos(te: dict, spy: dict, eq: dict) -> dict:
    """Honest OOS beat flags (return + risk-adjusted)."""
    te_sh = te.get("sharpe")
    spy_sh = spy.get("sharpe") or 0.0
    eq_sh = eq.get("sharpe") or 0.0
    beats_ret = te["total_return_pct"] > spy["total_return_pct"] and te["total_return_pct"] > eq[
        "total_return_pct"
    ]
    # risk-adj: Sharpe at least as good as the weaker of the two benches (within 0.05)
    # OR higher return with DD not worse than max(bench DD)+2pp
    beats_sh = te_sh is not None and te_sh >= min(spy_sh, eq_sh) - 0.05
    dd_ok = te.get("max_drawdown_pct", 99) <= max(
        spy.get("max_drawdown_pct", 99), eq.get("max_drawdown_pct", 99)
    ) + 2.0
    beats = bool(beats_ret and (beats_sh or dd_ok))
    return {
        "beats_buyhold_oos": beats,
        "beats_ret_oos": bool(beats_ret),
        "beats_sharpe_or_dd_oos": bool(beats_sh or dd_ok),
        "vs_spy_ret_oos": round(te["total_return_pct"] - spy["total_return_pct"], 4),
        "vs_equal_ret_oos": round(te["total_return_pct"] - eq["total_return_pct"], 4),
        "vs_spy_sharpe_oos": round((te_sh or 0) - spy_sh, 4) if te_sh is not None else None,
    }


def pick_winner(
    train_rows: list[dict],
    test_rows: list[dict],
    *,
    benchmark_ids: tuple[str, ...] = ("bh_spy", "bh_equal"),
) -> dict:
    """Select ONLY on train; evaluate that pick on test (no OOS cherry-pick).

    Also report the best train-ranked candidate that happens to clear OOS
    (labelled as exploratory, with look_index) — not used as primary winner
    unless look_index==0.
    """
    test_by = {r["id"]: r for r in test_rows if "error" not in r}
    benches_test = {b: test_by[b] for b in benchmark_ids if b in test_by}
    if len(benches_test) < 2:
        raise RuntimeError("Нет бенчмарков на test-окне")

    candidates = [
        r
        for r in train_rows
        if "error" not in r and r["id"] not in benchmark_ids and r.get("sharpe") is not None
    ]

    def score(r: dict) -> float:
        sh = float(r.get("sharpe") or 0)
        ret = float(r.get("total_return_pct") or 0)
        dd = float(r.get("max_drawdown_pct") or 0)
        return sh * 1.5 + ret / 50.0 - dd / 40.0

    candidates.sort(key=score, reverse=True)
    spy = benches_test["bh_spy"]
    eq = benches_test["bh_equal"]
    ranked = []
    for look_i, tr in enumerate(candidates):
        te = test_by.get(tr["id"])
        if not te:
            continue
        flags = _beats_oos(te, spy, eq)
        ranked.append(
            {
                "id": tr["id"],
                "family": tr["family"],
                "params": tr["params"],
                "train": tr,
                "test": te,
                "train_score": round(score(tr), 4),
                "train_rank": look_i,
                **flags,
            }
        )

    if not ranked:
        return {"verdict": "no_candidates", "winner": {}, "ranked": [], "benchmarks_oos": benches_test}

    primary = ranked[0]
    if primary["beats_buyhold_oos"]:
        verdict = "beats_buyhold_oos"
        winner = primary
    else:
        # exploratory: first train-ranked that clears OOS (disclose look_index)
        explorer = next((r for r in ranked if r["beats_buyhold_oos"]), None)
        if explorer is not None:
            verdict = "train_pick_misses_oos__explorer_beats"
            winner = primary
            winner = {
                **primary,
                "explorer_oos_beater": {
                    "id": explorer["id"],
                    "family": explorer["family"],
                    "params": explorer["params"],
                    "train_rank": explorer["train_rank"],
                    "test": explorer["test"],
                    **{k: explorer[k] for k in (
                        "beats_buyhold_oos",
                        "vs_spy_ret_oos",
                        "vs_equal_ret_oos",
                        "vs_spy_sharpe_oos",
                    )},
                },
            }
        else:
            verdict = "does_not_beat_buyhold_oos"
            winner = primary

    return {
        "verdict": verdict,
        "winner": winner,
        "ranked": ranked[:20],
        "benchmarks_oos": benches_test,
        "selection_rule": "max train_score (sharpe*1.5 + ret/50 - dd/40); OOS is holdout",
    }


def run_research(
    *,
    symbols: list[str] | None = None,
    cash: float = 100_000.0,
    data_start: str = "2020-01-01",
    train: Window | None = None,
    test: Window | None = None,
    cost_bps: float = 10.0,
    specs: list[StratSpec] | None = None,
) -> dict:
    ensure_dirs()
    BACKTESTS_DIR.mkdir(parents=True, exist_ok=True)
    symbols = symbols or list(DEFAULT_UNIVERSE)
    train = train or Window("train", "2021-01-01", "2023-12-31")
    test = test or Window("test", "2024-01-01", None)
    specs = specs or build_grid()

    store.append_event(
        "research_start",
        f"Research grid ×{len(specs)} on {', '.join(symbols)}",
        data={"symbols": symbols, "train": asdict(train), "test": asdict(test)},
    )

    px = download_prices(symbols, start=data_start, end=None)
    # ensure enough history for 200d SMA on train start
    train_rows = evaluate_window(px, specs, train, start_cash=cash, cost_bps=cost_bps)
    test_rows = evaluate_window(px, specs, test, start_cash=cash, cost_bps=cost_bps)
    selection = pick_winner(train_rows, test_rows)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"grid_{stamp}"
    payload = {
        "ok": True,
        "run_id": run_id,
        "as_of": _utc(),
        "engine": "nitub.research/vectorized",
        "symbols": symbols,
        "cash": cash,
        "cost_bps": cost_bps,
        "data_start": data_start,
        "data_from": str(px.index[0].date()),
        "data_to": str(px.index[-1].date()),
        "train_window": asdict(train),
        "test_window": asdict(test),
        "n_specs": len(specs),
        "train": train_rows,
        "test": test_rows,
        "selection": selection,
        "note": (
            "Параметры выбирались на train; OOS = test. "
            "Комиссия ~10 bps на оборот. Не финсовет."
        ),
    }

    out_path = BACKTESTS_DIR / f"{run_id}.json"
    store.write_json(out_path, payload)
    store.write_json(BACKTESTS_DIR / "latest.json", payload)

    # promote winner config for paper / CLI
    w = selection.get("winner") or {}
    winner_cfg = {
        "as_of": _utc(),
        "run_id": run_id,
        "verdict": selection.get("verdict"),
        "strategy_id": w.get("id"),
        "family": w.get("family"),
        "params": w.get("params") or {},
        "beats_buyhold_oos": w.get("beats_buyhold_oos"),
        "test_metrics": w.get("test"),
        "train_metrics": w.get("train"),
        "vs_spy_ret_oos": w.get("vs_spy_ret_oos"),
        "vs_equal_ret_oos": w.get("vs_equal_ret_oos"),
        "benchmarks_oos": selection.get("benchmarks_oos"),
        "universe": symbols,
        "results_path": str(out_path),
    }
    store.write_json(BACKTESTS_DIR / "winner.json", winner_cfg)
    store.write_json(STATE_DIR / "strategy.json", winner_cfg)
    # compact backtest.json for dashboard smoke
    te = w.get("test") or {}
    store.write_json(
        STATE_DIR / "backtest.json",
        {
            "ok": True,
            "engine": "nitub.research/vectorized",
            "strategy": f"{w.get('family')}:{w.get('id')}",
            "symbols": symbols,
            "from": te.get("from"),
            "to": te.get("to"),
            "start_cash": cash,
            "end_value": te.get("end_equity"),
            "pnl": round(float(te.get("end_equity") or cash) - cash, 2),
            "pnl_pct": te.get("total_return_pct"),
            "sharpe": te.get("sharpe"),
            "max_drawdown_pct": te.get("max_drawdown_pct"),
            "verdict": selection.get("verdict"),
            "beats_buyhold_oos": w.get("beats_buyhold_oos"),
            "vs_spy_ret_oos": w.get("vs_spy_ret_oos"),
            "vs_equal_ret_oos": w.get("vs_equal_ret_oos"),
            "run_id": run_id,
            "note": "OOS metrics only. Not financial advice.",
        },
    )

    store.append_event(
        "research_end",
        (
            f"Winner {w.get('id')} verdict={selection.get('verdict')} "
            f"OOS ret={te.get('total_return_pct')}%"
        ),
        data={
            "run_id": run_id,
            "winner": w.get("id"),
            "verdict": selection.get("verdict"),
            "beats": w.get("beats_buyhold_oos"),
        },
    )
    return payload


def refine_around_winner(
    *,
    base_family: str,
    base_params: dict,
    symbols: list[str] | None = None,
    cash: float = 100_000.0,
    data_start: str = "2020-01-01",
    train: Window | None = None,
    test: Window | None = None,
    cost_bps: float = 10.0,
) -> dict:
    """Second-pass local grid around a promising family (still selected on train only)."""
    specs = [
        StratSpec("bh_equal", "buy_hold_equal", {}),
        StratSpec("bh_spy", "buy_hold_spy", {}),
    ]
    if base_family == "momentum":
        for lb in sorted({base_params.get("lookback", 60) + d for d in (-21, -10, 0, 10, 21, 42)}):
            if lb < 10:
                continue
            for k in (1, 2, 3):
                specs.append(
                    StratSpec(
                        f"mom_lb{lb}_k{k}_r",
                        "momentum",
                        {"lookback": int(lb), "top_k": k, "abs_filter": True},
                    )
                )
                specs.append(
                    StratSpec(
                        f"mom_lb{lb}_k{k}_noabs_r",
                        "momentum",
                        {"lookback": int(lb), "top_k": k, "abs_filter": False},
                    )
                )
    elif base_family == "trend_sma":
        for p in sorted({base_params.get("period", 200) + d for d in (-50, -25, 0, 25, 50, 100)}):
            if p < 20:
                continue
            specs.append(StratSpec(f"trend_{p}_r", "trend_sma", {"period": int(p)}))
    elif base_family == "sma_cross":
        for fast, slow in [
            (base_params.get("fast", 10) + df, base_params.get("slow", 30) + ds)
            for df in (-5, 0, 5, 10)
            for ds in (-10, 0, 10, 20)
        ]:
            if fast < 3 or slow <= fast + 5:
                continue
            specs.append(StratSpec(f"sma_{fast}_{slow}_r", "sma_cross", {"fast": int(fast), "slow": int(slow)}))
    elif base_family == "dual_momentum":
        for lb in sorted({base_params.get("lookback", 126) + d for d in (-42, -21, 0, 21, 42, 63)}):
            if lb < 20:
                continue
            specs.append(StratSpec(f"dualmom_{lb}_r", "dual_momentum", {"lookback": int(lb)}))
    elif base_family == "copycat_hold":
        for tp in (0, 100, 150, 200, 250):
            for tickers, weights, tag in [
                (["AAPL", "MSFT", "GOOGL"], [0.4, 0.3, 0.3], "amg"),
                (["AAPL", "MSFT", "GOOGL", "AMZN"], [0.25, 0.25, 0.25, 0.25], "mega"),
                (["AAPL", "MSFT", "AMZN"], [0.34, 0.33, 0.33], "ama"),
            ]:
                specs.append(
                    StratSpec(
                        f"copycat_{tag}_t{tp}_r",
                        "copycat_hold",
                        {"tickers": tickers, "weights": weights, "trend_period": tp},
                    )
                )
    else:
        specs.extend(build_grid())

    return run_research(
        symbols=symbols,
        cash=cash,
        data_start=data_start,
        train=train,
        test=test,
        cost_bps=cost_bps,
        specs=specs,
    )


def load_winner() -> dict:
    path = BACKTESTS_DIR / "winner.json"
    if not path.exists():
        alt = STATE_DIR / "strategy.json"
        if alt.exists():
            return store.read_json(alt, {}) or {}
        return {}
    return store.read_json(path, {}) or {}


def apply_winner_targets(*, write: bool = True) -> dict:
    """Materialize research winner as trader/targets.json for paper cycle."""
    from .paths import TRADER_DIR

    winner = load_winner()
    if not winner:
        raise RuntimeError("Нет winner.json — сначала: nitub research")
    params = winner.get("params") or {}
    family = winner.get("family") or ""
    tickers = list(params.get("tickers") or [])
    weights = list(params.get("weights") or [])
    if family in ("buy_hold_equal", "vol_target_equal", "spy_trend_stocks", "trend_sma", "sma_cross", "momentum", "dual_momentum"):
        tickers = [t for t in (winner.get("universe") or DEFAULT_STOCKS) if t != "SPY"]
        weights = [1.0 / len(tickers)] * len(tickers) if tickers else []
    if not tickers:
        tickers = ["AAPL", "MSFT", "GOOGL"]
        weights = [0.4, 0.3, 0.3]
    if len(weights) != len(tickers):
        weights = [1.0 / len(tickers)] * len(tickers)
    # scale to percent weights summing ~100
    wsum = float(sum(weights)) or 1.0
    pct = [round(100.0 * float(w) / wsum, 2) for w in weights]
    # fix rounding drift on last
    pct[-1] = round(100.0 - sum(pct[:-1]), 2)
    names = {"AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet", "AMZN": "Amazon", "SPY": "S&P 500 ETF"}
    targets = {
        "source": f"nitub research winner {winner.get('strategy_id')} ({family})",
        "as_of": winner.get("as_of"),
        "method": "research-promoted sleeve weights (paper)",
        "strategy_id": winner.get("strategy_id"),
        "family": family,
        "params": params,
        "verdict": winner.get("verdict"),
        "beats_buyhold_oos": winner.get("beats_buyhold_oos"),
        "weights": [
            {
                "ticker": t,
                "name": names.get(t, t),
                "weight": pct[i],
                "pct_of_13f": pct[i],
            }
            for i, t in enumerate(tickers)
        ],
    }
    if write:
        store.write_json(TRADER_DIR / "targets.json", targets)
        store.append_event(
            "strategy_targets",
            f"Targets from research winner {winner.get('strategy_id')}: {', '.join(tickers)}",
            data={"strategy_id": winner.get("strategy_id"), "tickers": tickers},
        )
    return targets


def format_research_summary(payload: dict) -> str:
    sel = payload.get("selection") or {}
    w = sel.get("winner") or {}
    te = w.get("test") or {}
    benches = sel.get("benchmarks_oos") or {}
    lines = [
        f"Run:      {payload.get('run_id')}",
        f"Universe: {', '.join(payload.get('symbols') or [])}",
        f"Data:     {payload.get('data_from')} → {payload.get('data_to')}",
        f"Train:    {payload.get('train_window')}",
        f"Test:     {payload.get('test_window')}",
        f"Specs:    {payload.get('n_specs')}",
        f"Verdict:  {sel.get('verdict')}",
        f"Winner:   {w.get('id')} ({w.get('family')} {w.get('params')})",
        f"OOS ret:  {te.get('total_return_pct')}%  Sharpe={te.get('sharpe')}  MaxDD={te.get('max_drawdown_pct')}%",
        f"vs SPY:   {w.get('vs_spy_ret_oos')} pp  vs equal: {w.get('vs_equal_ret_oos')} pp",
    ]
    if "bh_spy" in benches:
        b = benches["bh_spy"]
        lines.append(f"SPY OOS:  {b.get('total_return_pct')}%  Sharpe={b.get('sharpe')}  DD={b.get('max_drawdown_pct')}%")
    if "bh_equal" in benches:
        b = benches["bh_equal"]
        lines.append(
            f"EqBH OOS: {b.get('total_return_pct')}%  Sharpe={b.get('sharpe')}  DD={b.get('max_drawdown_pct')}%"
        )
    lines.append(f"Saved:    nitub/state/backtests/{payload.get('run_id')}.json")
    return "\n".join(lines)
