"""Paper worker loop for dashboard Start/Stop (симуляция only)."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .paths import ROOT, STATE_DIR, TRADER_DIR, ensure_dirs
from . import auto_exit, store
from .store import utc_now

RUNNER_FILE = "runner.json"
STOP_FLAG = "runner.stop"
DEFAULT_INTERVAL_S = 45
REBALANCE_EVERY = 8  # cycles between light rebalance attempts
SKILLS_EVERY = 4  # cycles between full skills refresh (heavy SEC/news/pelosi interval inside)
GOAL_RU = "Попытаться вырастить бумажный счёт ~$100 (симуляция, без гарантий прибыли)"

_stop = False


def _runner_path() -> Path:
    return STATE_DIR / RUNNER_FILE


def _stop_path() -> Path:
    return STATE_DIR / STOP_FLAG


def read_runner() -> dict[str, Any]:
    ensure_dirs()
    data = store.read_json(_runner_path(), default=None)
    if isinstance(data, dict):
        return data
    return {
        "running": False,
        "started_at": None,
        "pid": None,
        "goal": GOAL_RU,
        "last_action": None,
        "message": "Остановлен",
        "stopped_at": None,
        "auto_exit": dict(auto_exit.DEFAULT_AUTO_EXIT),
    }


def load_auto_exit_cfg() -> dict[str, Any]:
    """DEFAULT ← trader/config.json ← runner.json (тюнинг без правки кода)."""
    trader_cfg = store.read_json(TRADER_DIR / "config.json", default={}) or {}
    runner_cfg = read_runner()
    return auto_exit.merge_auto_exit_config(trader_cfg, runner_cfg)


def write_runner(patch: dict[str, Any]) -> dict[str, Any]:
    ensure_dirs()
    cur = read_runner()
    cur.update(patch)
    cur["updated_at"] = utc_now()
    store.write_json(_runner_path(), cur)
    return cur


def request_stop() -> None:
    """Только выключить runner. Позиции намеренно не закрываем."""
    ensure_dirs()
    _stop_path().write_text("1\n", encoding="utf-8")
    write_runner(
        {
            "last_action": "stop_requested",
            "message": "Запрошена остановка… позиции не закрываем",
        }
    )


def clear_stop_flag() -> None:
    p = _stop_path()
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def stop_requested() -> bool:
    if _stop:
        return True
    return _stop_path().exists()


def _on_signal(_signum: int, _frame: Any) -> None:
    global _stop
    _stop = True


def _fetch_prices(tickers: list[str]) -> dict[str, float]:
    """Latest Yahoo marks via yfinance (paper only)."""
    import yfinance as yf

    out: dict[str, float] = {}
    if not tickers:
        return out
    uniq = sorted({t.upper() for t in tickers if t})
    try:
        data = yf.download(
            " ".join(uniq),
            period="5d",
            interval="1d",
            progress=False,
            threads=False,
            auto_adjust=True,
        )
    except Exception:
        data = None

    if data is not None and not getattr(data, "empty", True):
        try:
            close = data["Close"] if "Close" in data.columns else data
            if hasattr(close, "columns"):
                for t in uniq:
                    if t in close.columns:
                        series = close[t].dropna()
                        if len(series):
                            out[t] = float(series.iloc[-1])
            else:
                series = close.dropna()
                if len(uniq) == 1 and len(series):
                    out[uniq[0]] = float(series.iloc[-1])
        except Exception:
            pass

    # Fallback: one ticker at a time
    missing = [t for t in uniq if t not in out]
    for t in missing:
        try:
            hist = yf.Ticker(t).history(period="5d")
            if hist is not None and not hist.empty:
                out[t] = float(hist["Close"].iloc[-1])
        except Exception:
            continue
    return out


def mark_portfolio(prices: dict[str, float] | None = None) -> dict | None:
    """Revalue open positions; write portfolio + ledger + event."""
    pf = store.load_portfolio()
    if not pf:
        return None

    positions = list(pf.get("positions") or [])
    tickers = [p.get("ticker") for p in positions if p.get("ticker")]
    if prices is None:
        prices = _fetch_prices([str(t) for t in tickers])

    mtm_total = 0.0
    upnl_total = 0.0
    cost_total = 0.0
    updated: list[dict] = []
    for p in positions:
        sym = str(p.get("ticker") or "").upper()
        shares = float(p.get("shares") or 0)
        cost = float(p.get("cost_basis") or 0)
        mark = float(prices.get(sym) or p.get("mark") or 0)
        mv = round(shares * mark, 2) if mark else float(p.get("market_value") or 0)
        upnl = round(mv - cost, 2)
        row = dict(p)
        row["mark"] = round(mark, 4) if mark else row.get("mark")
        row["market_value"] = mv
        row["unrealized_pnl"] = upnl
        row["unrealized_pnl_pct"] = (
            round(upnl / cost * 100, 4) if cost else 0.0
        )
        if shares:
            row["avg_cost_per_share"] = round(cost / shares, 4)
        updated.append(row)
        mtm_total += mv
        upnl_total += upnl
        cost_total += cost

    cash = float(pf.get("cash") or 0)
    start = float(pf.get("starting_cash") or 100)
    equity = round(cash + mtm_total, 2)
    total_pnl = round(equity - start, 2)
    peak = float(pf.get("peak_equity") or start)
    peak = max(peak, equity)

    pf["positions"] = updated
    pf["as_of"] = utc_now()
    pf["price_source"] = "yahoo"
    pf["invested_cost"] = round(cost_total, 2)
    pf["positions_market_value"] = round(mtm_total, 2)
    pf["equity"] = equity
    pf["peak_equity"] = peak
    pf["drawdown_pct"] = round(auto_exit.portfolio_drawdown_pct(pf), 4)
    pf["unrealized_pnl"] = round(upnl_total, 2)
    pf["total_pnl_vs_start"] = total_pnl
    pf["total_pnl_pct"] = round((total_pnl / start * 100) if start else 0.0, 4)
    pf["mode"] = "paper"

    store.write_json(STATE_DIR / store.PORTFOLIO, pf)
    store.write_ledger(pf)
    store.append_event(
        "mark_to_market",
        f"Переоценка: equity=${equity:.2f}, P&L=${total_pnl:.2f} ({pf['total_pnl_pct']:.2f}%)",
        data={"equity": equity, "pnl": total_pnl, "marks": {k: prices.get(k) for k in tickers}},
        skill="runner",
    )
    meta = store.read_json(STATE_DIR / store.META, default={}) or {}
    meta.update(
        {
            "session": pf.get("session"),
            "updated_at": utc_now(),
            "mode": "paper",
            "state_dir": str(STATE_DIR),
        }
    )
    store.write_json(STATE_DIR / store.META, meta)
    return pf


def _target_weights() -> dict[str, float]:
    strategy = store.read_json(STATE_DIR / "strategy.json", default={}) or {}
    params = strategy.get("params") or {}
    tickers = params.get("tickers") or []
    weights = params.get("weights") or []
    if tickers and weights and len(tickers) == len(weights):
        return {str(t).upper(): float(w) for t, w in zip(tickers, weights)}

    winner = store.read_json(STATE_DIR / "backtests" / "winner.json", default={}) or {}
    params = winner.get("params") or {}
    tickers = params.get("tickers") or []
    weights = params.get("weights") or []
    if tickers and weights and len(tickers) == len(weights):
        return {str(t).upper(): float(w) for t, w in zip(tickers, weights)}

    targets = store.read_json(TRADER_DIR / "targets.json", default={}) or {}
    rows = targets.get("weights") or []
    out: dict[str, float] = {}
    for row in rows:
        t = (row.get("ticker") or "").upper()
        w = row.get("weight")
        if t and w is not None:
            out[t] = float(w)
    return out


def light_rebalance(
    pf: dict,
    prices: dict[str, float],
    *,
    exclude_tickers: set[str] | None = None,
    buy_only: bool = False,
) -> dict:
    """Nudge paper sleeve toward research target weights (small paper fills)."""
    targets = _target_weights()
    if not targets or not prices:
        return pf
    blocked = {t.upper() for t in (exclude_tickers or set())}

    cash = float(pf.get("cash") or 0)
    positions = {str(p["ticker"]).upper(): dict(p) for p in (pf.get("positions") or [])}
    equity = float(pf.get("equity") or (cash + sum(float(p.get("market_value") or 0) for p in positions.values())))
    if equity <= 0:
        return pf

    fills = list(pf.get("fills") or [])
    realized = float(pf.get("realized_pnl") or 0)
    buffer_pct = float((pf.get("config_snapshot") or {}).get("cash_buffer_pct") or 10) / 100.0
    investable = equity * (1.0 - buffer_pct)
    changed = False
    fill_i = len(fills)

    # Sell overweight first to free cash (skip on buy_only reopen path)
    if not buy_only:
        for sym, weight in targets.items():
            px = prices.get(sym)
            if not px or px <= 0:
                continue
            pos = positions.get(sym)
            cur_mv = float(pos.get("market_value") or 0) if pos else 0.0
            target_mv = investable * weight
            drift = cur_mv - target_mv
            # Only act on meaningful drift (>8% of target or >$2)
            if drift <= max(2.0, target_mv * 0.08):
                continue
            if not pos or float(pos.get("shares") or 0) <= 0:
                continue
            sell_notional = min(drift, cur_mv)
            qty = round(sell_notional / px, 6)
            shares = float(pos["shares"])
            if qty <= 0 or qty > shares:
                qty = shares
                sell_notional = round(qty * px, 2)
            else:
                sell_notional = round(qty * px, 2)
            cost = float(pos.get("cost_basis") or 0)
            cost_sold = round(cost * (qty / shares), 2) if shares else sell_notional
            pnl_sold = round(sell_notional - cost_sold, 2)
            pos["shares"] = round(shares - qty, 6)
            pos["cost_basis"] = round(max(0.0, cost - cost_sold), 2)
            pos["mark"] = round(px, 4)
            pos["market_value"] = round(pos["shares"] * px, 2)
            pos["unrealized_pnl"] = round(pos["market_value"] - pos["cost_basis"], 2)
            cash = round(cash + sell_notional, 2)
            realized = round(realized + pnl_sold, 2)
            fill_i += 1
            fills.append(
                {
                    "id": f"paper-rb-{fill_i}",
                    "symbol": sym,
                    "side": "sell",
                    "notional": sell_notional,
                    "qty": qty,
                    "price": round(px, 4),
                    "status": "paper_simulated",
                    "ts": utc_now(),
                }
            )
            positions[sym] = pos
            changed = True
            store.append_event(
                "fill",
                f"Бумажная продажа {sym}: ${sell_notional:.2f} (ребаланс к цели)",
                data={"symbol": sym, "side": "sell", "notional": sell_notional},
                skill="runner",
            )

    # Buy underweight with available cash (skip cooldown tickers after auto-exit)
    for sym, weight in targets.items():
        if sym in blocked:
            continue
        px = prices.get(sym)
        if not px or px <= 0:
            continue
        pos = positions.get(sym) or {
            "ticker": sym,
            "shares": 0.0,
            "cost_basis": 0.0,
            "lots": [],
        }
        cur_mv = float(pos.get("market_value") or (float(pos.get("shares") or 0) * px))
        target_mv = investable * weight
        need = target_mv - cur_mv
        if need <= max(2.0, target_mv * 0.08):
            continue
        buy_notional = min(need, cash)
        if buy_notional < 2.0:
            continue
        qty = round(buy_notional / px, 6)
        buy_notional = round(qty * px, 2)
        if buy_notional > cash + 1e-9:
            continue
        pos["shares"] = round(float(pos.get("shares") or 0) + qty, 6)
        pos["cost_basis"] = round(float(pos.get("cost_basis") or 0) + buy_notional, 2)
        pos["mark"] = round(px, 4)
        pos["market_value"] = round(pos["shares"] * px, 2)
        pos["unrealized_pnl"] = round(pos["market_value"] - pos["cost_basis"], 2)
        pos["avg_cost_per_share"] = (
            round(pos["cost_basis"] / pos["shares"], 4) if pos["shares"] else 0.0
        )
        lots = list(pos.get("lots") or [])
        lots.append(
            {
                "id": f"{sym}-{len(lots) + 1}",
                "date": utc_now()[:10],
                "shares": qty,
                "cost": buy_notional,
            }
        )
        pos["lots"] = lots
        cash = round(cash - buy_notional, 2)
        fill_i += 1
        fills.append(
            {
                "id": f"paper-rb-{fill_i}",
                "symbol": sym,
                "side": "buy",
                "notional": buy_notional,
                "qty": qty,
                "price": round(px, 4),
                "status": "paper_simulated",
                "ts": utc_now(),
            }
        )
        positions[sym] = pos
        changed = True
        store.append_event(
            "fill",
            f"Бумажная покупка {sym}: ${buy_notional:.2f} (ребаланс к цели)",
            data={"symbol": sym, "side": "buy", "notional": buy_notional},
            skill="runner",
        )

    if not changed:
        return pf

    pos_list = [p for p in positions.values() if float(p.get("shares") or 0) > 1e-9]
    mtm = sum(float(p.get("market_value") or 0) for p in pos_list)
    cost = sum(float(p.get("cost_basis") or 0) for p in pos_list)
    start = float(pf.get("starting_cash") or 100)
    equity = round(cash + mtm, 2)
    total_pnl = round(equity - start, 2)
    peak = max(float(pf.get("peak_equity") or equity), equity)

    pf = dict(pf)
    pf.update(
        {
            "cash": cash,
            "positions": pos_list,
            "fills": fills,
            "invested_cost": round(cost, 2),
            "positions_market_value": round(mtm, 2),
            "equity": equity,
            "peak_equity": peak,
            "unrealized_pnl": round(mtm - cost, 2),
            "total_pnl_vs_start": total_pnl,
            "total_pnl_pct": round((total_pnl / start * 100) if start else 0.0, 4),
            "realized_pnl": realized,
            "as_of": utc_now(),
            "mode": "paper",
        }
    )
    store.write_json(STATE_DIR / store.PORTFOLIO, pf)
    store.write_ledger(pf)
    store.append_event(
        "rebalance",
        f"Лёгкий бумажный ребаланс к research-sleeve · equity=${equity:.2f}",
        data={"equity": equity, "targets": targets},
        skill="runner",
    )
    return pf


def _persist_portfolio(pf: dict) -> dict:
    pf = dict(pf)
    pf.pop("_closed", None)
    pf.pop("_reason_by_sym", None)
    pf["as_of"] = utc_now()
    store.write_json(STATE_DIR / store.PORTFOLIO, pf)
    store.write_ledger(pf)
    return pf


def apply_auto_exits(
    pf: dict,
    prices: dict[str, float],
    *,
    cfg: dict | None = None,
) -> tuple[dict, list[dict]]:
    """Mark уже в pf: закрыть по TP/SL/DD, записать sell fills + RU events."""
    cfg = cfg or load_auto_exit_cfg()
    pf = dict(pf)
    pf["auto_exit_config"] = cfg
    if not cfg.get("enabled", True):
        return pf, []

    planned = auto_exit.plan_exits(pf, cfg)
    if not planned:
        return pf, []

    write_runner(
        {
            "last_action": "auto_exit",
            "message": "Работает… авто-выход по риску/прибыли",
        }
    )
    pf, closed = auto_exit.apply_planned_closes(pf, prices, planned)
    pf = _persist_portfolio(pf)
    for row in closed:
        store.append_event(
            "auto_exit",
            (
                f"Авто-выход {row['ticker']}: {row.get('reason_ru')} · "
                f"продажа ${float(row['notional']):.2f} · "
                f"P&L ${float(row['realized_pnl']):+.2f} ({float(row['pnl_pct']):+.2f}%)"
            ),
            data=row,
            skill="runner",
        )
        store.append_event(
            "fill",
            f"Бумажная продажа {row['ticker']}: ${float(row['notional']):.2f} ({row.get('reason_ru')})",
            data={
                "symbol": row["ticker"],
                "side": "sell",
                "notional": row["notional"],
                "reason": row["reason"],
            },
            skill="runner",
        )
    store.append_event(
        "auto_exit",
        auto_exit.format_exit_summary_ru(closed),
        data={"closed": closed, "cfg": cfg},
        skill="runner",
    )
    return pf, closed


def reopen_after_exits(
    pf: dict,
    prices: dict[str, float],
    *,
    budget: float = 100.0,
    closed: list[dict] | None = None,
) -> dict:
    """После выхода — research sleeve / buy underweight (без сброса realized)."""
    closed = closed or []
    if not closed:
        return pf

    write_runner(
        {
            "last_action": "reopen",
            "message": "Работает… ищем новый вариант после выхода",
        }
    )
    store.append_event(
        "reopen_search",
        "После авто-выхода ищем новый paper-вариант в рамках бюджета",
        data={
            "closed": [c.get("ticker") for c in closed],
            "cash": pf.get("cash"),
            "budget": budget,
        },
        skill="runner",
    )

    # Skip immediate rebuy of just-exited names (cooldown)
    blocked = auto_exit.cooldown_tickers(pf) | {
        str(c.get("ticker") or "").upper() for c in closed if c.get("ticker")
    }
    # If all research names blocked and flat, allow reopen into sleeve anyway
    targets = _target_weights()
    if targets and blocked.issuperset(targets.keys()) and not (pf.get("positions") or []):
        blocked = set()
        store.append_event(
            "reopen_search",
            "Cooldown на весь sleeve — открываем research-цели снова (бюджет $100)",
            skill="runner",
        )

    before_fills = len(pf.get("fills") or [])
    # Не вызываем run_paper здесь — он чистит events; только paper-buy к research sleeve.
    pf = light_rebalance(pf, prices, exclude_tickers=blocked, buy_only=True)
    # If still flat (все цели в cooldown) — один раз разрешаем тот же sleeve.
    if not (pf.get("positions") or []) and float(pf.get("cash") or 0) >= 5 and blocked:
        store.append_event(
            "reopen_search",
            "Sleeve пуст при cooldown — открываем research-цели без исключения",
            skill="runner",
        )
        pf = light_rebalance(pf, prices, exclude_tickers=set(), buy_only=True)

    opened = len(pf.get("fills") or []) - before_fills
    if opened:
        store.append_event(
            "reopen",
            f"Открыты новые paper-позиции после выхода ({opened} fill) · бюджет ${budget:.0f}",
            data={"fills_added": opened, "excluded": sorted(blocked)},
            skill="runner",
        )
    else:
        store.append_event(
            "reopen_search",
            "Свободного cash мало или нет цен/целей — ждём следующий цикл",
            data={"cash": pf.get("cash"), "blocked": sorted(blocked)},
            skill="runner",
        )
    return pf


def run_risk_cycle(
    pf: dict,
    prices: dict[str, float] | None = None,
    *,
    budget: float = 100.0,
    cfg: dict | None = None,
) -> dict:
    """MTM уже сделан → auto-exit → reopen. Для loop и smoke."""
    cfg = cfg or load_auto_exit_cfg()
    pf = auto_exit.tick_exit_cooldown(pf)
    tickers = [p.get("ticker") for p in (pf.get("positions") or [])]
    targets = _target_weights()
    all_t = sorted({*(str(t) for t in (tickers or []) if t), *targets.keys()})
    if prices is None:
        prices = _fetch_prices(all_t)
    # Ensure marks for exit math
    for p in pf.get("positions") or []:
        sym = str(p.get("ticker") or "").upper()
        if sym in prices and prices[sym] > 0:
            shares = float(p.get("shares") or 0)
            cost = float(p.get("cost_basis") or 0)
            mark = float(prices[sym])
            p["mark"] = round(mark, 4)
            p["market_value"] = round(shares * mark, 2)
            p["unrealized_pnl"] = round(p["market_value"] - cost, 2)
            p["unrealized_pnl_pct"] = round((p["unrealized_pnl"] / cost * 100) if cost else 0.0, 4)
    cash = float(pf.get("cash") or 0)
    mtm = sum(float(p.get("market_value") or 0) for p in (pf.get("positions") or []))
    pf["equity"] = round(cash + mtm, 2)
    start = float(pf.get("starting_cash") or budget)
    pf["peak_equity"] = max(float(pf.get("peak_equity") or start), float(pf["equity"]))
    pf["total_pnl_vs_start"] = round(float(pf["equity"]) - start, 2)
    pf["total_pnl_pct"] = round(
        (pf["total_pnl_vs_start"] / start * 100) if start else 0.0, 4
    )

    pf, closed = apply_auto_exits(pf, prices, cfg=cfg)
    if closed:
        # Refresh marks for remaining + fetch prices for target reopen
        need = sorted({*prices.keys(), *targets.keys()})
        more = {k: v for k, v in prices.items()}
        missing = [t for t in need if t not in more]
        if missing:
            more.update(_fetch_prices(missing))
        pf = reopen_after_exits(pf, more, budget=budget, closed=closed)
        pf = _persist_portfolio(pf)
    else:
        pf = _persist_portfolio(pf)
    return pf


def ensure_seed_portfolio(*, budget: float = 100.0, session: str = "dashboard") -> dict:
    """If no positions, run one research paper cycle with full skills pipeline."""
    pf = store.load_portfolio()
    if pf and (pf.get("positions") or []):
        return pf

    write_runner(
        {
            "last_action": "seed_paper",
            "message": "Первый цикл paper: research + все skills…",
        }
    )
    from . import paper

    return paper.run_paper(
        session=session,
        budget=budget,
        refresh_copycat=True,
        skip_news=False,
        use_research_strategy=True,
    )


def refresh_skills_pass(*, cycle: int = 1) -> list[dict]:
    """Full skills pass without resetting the paper sleeve (for Start loop)."""
    from . import orchestrator

    write_runner(
        {
            "last_action": "skills_pipeline",
            "message": f"Работает… полный pipeline skills #{cycle}",
        }
    )
    rows = orchestrator.run_full_pipeline(
        tickers=None,
        refresh_copycat=False,
        copycat_out=TRADER_DIR / "state" / "copycat-ref.json",
        skip_news=False,
        force_heavy=(cycle <= 1 or cycle % 3 == 1),
        cycle=cycle,
        run_budget_risk=False,
        broker_mode="tickets",
        include_vendor=True,
    )
    # Merge into skills.json without wiping portfolio
    existing = store.read_json(STATE_DIR / store.SKILLS, default=[]) or []
    if not isinstance(existing, list):
        existing = []
    merged = orchestrator.merge_skill_rows(existing, rows)
    store.write_json(STATE_DIR / store.SKILLS, merged)
    orchestrator.publish_catalog(merged)
    analysis = store.read_json(STATE_DIR / store.ANALYSIS, default={}) or {}
    if isinstance(analysis, dict):
        analysis["skills_catalog"] = orchestrator.catalog()
        analysis["skills_completed"] = [r["name"] for r in merged]
        store.write_json(STATE_DIR / store.ANALYSIS, analysis)
    return merged


def run_loop(
    *,
    interval_s: float = DEFAULT_INTERVAL_S,
    budget: float = 100.0,
    session: str = "dashboard",
) -> int:
    """Blocking paper worker. Updates runner.json; only paper."""
    global _stop
    _stop = False
    ensure_dirs()
    clear_stop_flag()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    ax_cfg = load_auto_exit_cfg()
    write_runner(
        {
            "running": True,
            "started_at": utc_now(),
            "pid": os.getpid(),
            "goal": GOAL_RU,
            "last_action": "start",
            "message": "Работает: бумажный воркер запущен",
            "stopped_at": None,
            "interval_s": interval_s,
            "auto_exit": ax_cfg,
            "auto_exit_enabled": bool(ax_cfg.get("enabled", True)),
        }
    )
    store.append_event(
        "runner_start",
        (
            "Старт бумажного воркера (цель: вырастить ~$100 в симуляции). "
            f"Авто-выход: TP +{ax_cfg.get('take_profit_pct')}% / "
            f"SL {ax_cfg.get('stop_loss_pct')}% / DD {ax_cfg.get('max_drawdown_pct')}%"
        ),
        data={"pid": os.getpid(), "goal": GOAL_RU, "auto_exit": ax_cfg},
        skill="runner",
    )

    try:
        ensure_seed_portfolio(budget=budget, session=session)
    except Exception as exc:
        write_runner(
            {
                "last_action": "seed_error",
                "message": f"Ошибка начального paper-цикла: {exc}",
            }
        )
        store.append_event(
            "runner_error",
            f"Ошибка seed paper: {exc}",
            skill="runner",
        )

    cycle = 0
    while not stop_requested():
        cycle += 1
        try:
            write_runner(
                {
                    "running": True,
                    "pid": os.getpid(),
                    "last_action": "mark_to_market",
                    "message": f"Работает… переоценка #{cycle}",
                }
            )
            pf = mark_portfolio()
            if pf is None:
                write_runner(
                    {
                        "last_action": "waiting_portfolio",
                        "message": "Нет portfolio.json — жду seed / nitub run paper",
                    }
                )
            else:
                # Авто-выход (TP/SL/DD) + reopen — каждый цикл после MTM
                tickers = [p.get("ticker") for p in (pf.get("positions") or [])]
                targets = _target_weights()
                all_t = sorted({*(str(t) for t in (tickers or []) if t), *targets.keys()})
                prices = _fetch_prices(all_t)
                ax_cfg = load_auto_exit_cfg()
                pf = run_risk_cycle(pf, prices, budget=budget, cfg=ax_cfg)

                if cycle == 1 or cycle % SKILLS_EVERY == 0:
                    try:
                        refresh_skills_pass(cycle=cycle)
                    except Exception as skills_exc:
                        store.append_event(
                            "runner_error",
                            f"Ошибка skills pipeline: {skills_exc}",
                            skill="runner",
                        )

                if cycle % REBALANCE_EVERY == 0:
                    write_runner(
                        {
                            "last_action": "rebalance",
                            "message": f"Работает… проверка ребаланса #{cycle}",
                        }
                    )
                    # Don't sell names just exited this cycle (cooldown)
                    pf = light_rebalance(
                        pf,
                        prices,
                        exclude_tickers=auto_exit.cooldown_tickers(pf),
                    )
                    mark_portfolio(prices)
                    pf = store.load_portfolio() or pf

                write_runner(
                    {
                        "last_action": "cycle_done",
                        "message": (
                            f"Работает… equity=${float((pf or {}).get('equity') or 0):.2f}, "
                            f"P&L=${float((pf or {}).get('total_pnl_vs_start') or 0):.2f}"
                        ),
                        "auto_exit": ax_cfg,
                        "auto_exit_enabled": bool(ax_cfg.get("enabled", True)),
                    }
                )
        except Exception as exc:
            write_runner(
                {
                    "last_action": "error",
                    "message": f"Ошибка цикла: {exc}",
                }
            )
            store.append_event(
                "runner_error",
                f"Ошибка цикла воркера: {exc}",
                skill="runner",
            )

        # Sleep in chunks so stop is responsive
        slept = 0.0
        while slept < interval_s and not stop_requested():
            time.sleep(min(1.0, interval_s - slept))
            slept += 1.0

    write_runner(
        {
            "running": False,
            "pid": None,
            "last_action": "stopped",
            "message": "Остановлен (позиции не закрывались)",
            "stopped_at": utc_now(),
        }
    )
    store.append_event(
        "runner_stop",
        "Бумажный воркер остановлен — позиции оставлены как есть (Стоп не закрывает сделки)",
        skill="runner",
    )
    clear_stop_flag()
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="NITUB paper worker (dashboard Start)")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    p.add_argument("--budget", type=float, default=100.0)
    p.add_argument("--session", default="dashboard")
    args = p.parse_args(argv)
    os.environ.setdefault("NITUB_MODE", "paper")
    os.environ.setdefault("PYTHONPATH", str(ROOT))
    return run_loop(interval_s=args.interval, budget=args.budget, session=args.session)


if __name__ == "__main__":
    raise SystemExit(main())
