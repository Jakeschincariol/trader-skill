"""Minimal backtest via vendor/backtrader (or pip backtrader) + yfinance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import store
from .paths import ROOT, STATE_DIR, VENDOR_DIR, ensure_dirs


def _ensure_backtrader_path() -> None:
    """Prefer pip-installed backtrader; fall back to vendor clone."""
    try:
        import backtrader  # noqa: F401

        return
    except ImportError:
        pass
    vendor_bt = VENDOR_DIR / "backtrader"
    if vendor_bt.exists():
        p = str(vendor_bt)
        if p not in sys.path:
            sys.path.insert(0, p)


def run_backtest(
    *,
    symbols: list[str] | None = None,
    cash: float = 100_000.0,
    fromdate: str = "2024-01-01",
    todate: str | None = None,
    fast: int = 10,
    slow: int = 30,
) -> dict:
    """SMA crossover smoke backtest. Writes nitub/state/backtest.json."""
    ensure_dirs()
    symbols = symbols or ["AAPL", "GOOGL"]
    store.append_event(
        "backtest_start",
        f"Backtest SMA({fast}/{slow}) on {', '.join(symbols)}",
        data={"symbols": symbols, "cash": cash, "from": fromdate, "to": todate},
    )

    _ensure_backtrader_path()
    try:
        import backtrader as bt
    except ImportError as e:
        raise RuntimeError(
            "backtrader не установлен. Активируйте nitub/.venv и: pip install backtrader"
        ) from e

    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError(
            "yfinance не установлен. pip install yfinance в nitub/.venv"
        ) from e

    import pandas as pd

    class SmaCross(bt.Strategy):
        params = (("fast", fast), ("slow", slow),)

        def __init__(self):
            self.order = None
            self.sma_fast = {}
            self.sma_slow = {}
            self.crossover = {}
            for d in self.datas:
                self.sma_fast[d] = bt.ind.SMA(d.close, period=self.p.fast)
                self.sma_slow[d] = bt.ind.SMA(d.close, period=self.p.slow)
                self.crossover[d] = bt.ind.CrossOver(self.sma_fast[d], self.sma_slow[d])

        def next(self):
            for d in self.datas:
                pos = self.getposition(d)
                if not pos and self.crossover[d] > 0:
                    size = int((self.broker.getcash() * 0.4) / d.close[0])
                    if size > 0:
                        self.buy(data=d, size=size)
                elif pos and self.crossover[d] < 0:
                    self.close(data=d)

    cerebro = bt.Cerebro()
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=0.001)
    loaded = []

    for sym in symbols:
        df = yf.download(
            sym,
            start=fromdate,
            end=todate,
            progress=False,
            auto_adjust=True,
        )
        if df is None or len(df) < slow + 5:
            store.append_event("backtest_skip", f"Мало данных для {sym}")
            continue
        # yfinance multi-index cleanup
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        need = {"open", "high", "low", "close", "volume"}
        if not need.issubset(set(df.columns)):
            store.append_event("backtest_skip", f"Нет OHLCV колонок для {sym}")
            continue
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        data = bt.feeds.PandasData(dataname=df)
        data._name = sym
        cerebro.adddata(data, name=sym)
        loaded.append(sym)

    if not loaded:
        raise RuntimeError("Не удалось загрузить ни один тикер для бэктеста")

    cerebro.addstrategy(SmaCross)
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.0)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    start_val = cerebro.broker.getvalue()
    results = cerebro.run()
    strat = results[0]
    end_val = cerebro.broker.getvalue()

    sharpe = strat.analyzers.sharpe.get_analysis()
    dd = strat.analyzers.dd.get_analysis()
    trades = strat.analyzers.trades.get_analysis()

    total_trades = 0
    won = 0
    lost = 0
    try:
        total_trades = int(trades.get("total", {}).get("total", 0) or 0)
        won = int(trades.get("won", {}).get("total", 0) or 0)
        lost = int(trades.get("lost", {}).get("total", 0) or 0)
    except Exception:
        pass

    out = {
        "ok": True,
        "engine": "backtrader",
        "strategy": f"SMA crossover {fast}/{slow}",
        "symbols": loaded,
        "from": fromdate,
        "to": todate,
        "start_cash": start_val,
        "end_value": round(float(end_val), 2),
        "pnl": round(float(end_val - start_val), 2),
        "pnl_pct": round(float((end_val - start_val) / start_val * 100), 4) if start_val else 0.0,
        "sharpe": sharpe.get("sharperatio") if isinstance(sharpe, dict) else None,
        "max_drawdown_pct": (dd.get("max", {}) or {}).get("drawdown") if isinstance(dd, dict) else None,
        "trades": {"total": total_trades, "won": won, "lost": lost},
        "vendor_path": str(VENDOR_DIR / "backtrader"),
        "note": "Smoke backtest only. Not financial advice.",
    }

    path = STATE_DIR / "backtest.json"
    store.write_json(path, out)
    # also archive under paper-trading if current session exists
    meta = store.read_json(STATE_DIR / "meta.json", {})
    session = (meta or {}).get("session")
    if session:
        dest = ROOT / "paper-trading" / session / "backtest.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        store.write_json(dest, out)

    store.append_event(
        "backtest_end",
        f"Backtest PnL ${out['pnl']:.2f} ({out['pnl_pct']:.2f}%) on {', '.join(loaded)}",
        data=out,
    )
    return out


def format_backtest(result: dict) -> str:
    lines = [
        f"Engine:   {result.get('engine')} · {result.get('strategy')}",
        f"Symbols:  {', '.join(result.get('symbols') or [])}",
        f"Period:   {result.get('from')} → {result.get('to') or 'now'}",
        f"Start:    ${result.get('start_cash', 0):,.2f}",
        f"End:      ${result.get('end_value', 0):,.2f}",
        f"P&L:      ${result.get('pnl', 0):,.2f} ({result.get('pnl_pct', 0):.2f}%)",
        f"Sharpe:   {result.get('sharpe')}",
        f"Max DD%:  {result.get('max_drawdown_pct')}",
        f"Trades:   {result.get('trades')}",
        f"Saved:    nitub/state/backtest.json",
    ]
    return "\n".join(lines)
