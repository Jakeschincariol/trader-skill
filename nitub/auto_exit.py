"""Paper auto-exit: take-profit / stop-loss / drawdown (симуляция only).

Стоп кнопки дашборда сюда не входит — он только гасит runner, позиции не трогает.
"""

from __future__ import annotations

from typing import Any

# Разумные дефолты (тюнятся в trader/config.json → auto_exit или runner.json)
DEFAULT_AUTO_EXIT: dict[str, Any] = {
    "enabled": True,
    "take_profit_pct": 4.0,  # +4% по позиции
    "stop_loss_pct": -2.5,  # −2.5% по позиции
    "portfolio_take_profit_pct": 5.0,  # +5% по портфелю → закрыть всё
    "max_drawdown_pct": 4.0,  # просадка от peak equity → закрыть всё
    "min_position_pnl_usd": 5.0,  # шум: не TP на копейках (~$10k paper)
    "exit_rebuy_cooldown_cycles": 2,  # не сразу выкупать тот же тикер
}

REASON_TP = "take_profit"
REASON_SL = "stop_loss"
REASON_PORTFOLIO_TP = "portfolio_take_profit"
REASON_DRAWDOWN = "max_drawdown"

REASON_RU = {
    REASON_TP: "тейк-профит",
    REASON_SL: "стоп-лосс",
    REASON_PORTFOLIO_TP: "цель по портфелю",
    REASON_DRAWDOWN: "лимит просадки",
}


def merge_auto_exit_config(*layers: dict | None) -> dict[str, Any]:
    """Merge DEFAULT ← trader/config ← runner.json patches."""
    out = dict(DEFAULT_AUTO_EXIT)
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        nested = layer.get("auto_exit") if "auto_exit" in layer else layer
        if not isinstance(nested, dict):
            continue
        for k, v in nested.items():
            if k in DEFAULT_AUTO_EXIT or k in ("enabled",):
                out[k] = v
    return out


def position_pnl_pct(pos: dict) -> float:
    cost = float(pos.get("cost_basis") or 0)
    if cost <= 0:
        return 0.0
    upnl = pos.get("unrealized_pnl")
    if upnl is None:
        mv = float(pos.get("market_value") or 0)
        upnl = mv - cost
    return float(upnl) / cost * 100.0


def portfolio_drawdown_pct(pf: dict) -> float:
    """Drawdown % from peak_equity (0 if no peak / flat)."""
    equity = float(pf.get("equity") or 0)
    peak = float(pf.get("peak_equity") or equity or 0)
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak * 100.0)


def decide_portfolio_exit(pf: dict, cfg: dict) -> str | None:
    """Portfolio-level force-flat reason, or None."""
    if not cfg.get("enabled", True):
        return None
    start = float(pf.get("starting_cash") or 0) or 100.0
    equity = float(pf.get("equity") or 0)
    total_pct = (equity - start) / start * 100.0 if start else 0.0
    port_tp = float(cfg.get("portfolio_take_profit_pct") or 0)
    if port_tp > 0 and total_pct >= port_tp:
        return REASON_PORTFOLIO_TP
    max_dd = float(cfg.get("max_drawdown_pct") or 0)
    dd = portfolio_drawdown_pct(pf)
    if max_dd > 0 and dd >= max_dd:
        return REASON_DRAWDOWN
    return None


def decide_position_exit(pos: dict, cfg: dict) -> str | None:
    """Per-position TP/SL reason, or None."""
    if not cfg.get("enabled", True):
        return None
    shares = float(pos.get("shares") or 0)
    cost = float(pos.get("cost_basis") or 0)
    if shares <= 1e-9 or cost <= 0:
        return None
    pct = position_pnl_pct(pos)
    upnl = float(pos.get("unrealized_pnl") or (pct / 100.0 * cost))
    tp = float(cfg.get("take_profit_pct") or 0)
    sl = float(cfg.get("stop_loss_pct") or 0)
    min_usd = float(cfg.get("min_position_pnl_usd") or 0)

    if tp > 0 and pct >= tp and upnl >= min_usd:
        return REASON_TP
    if sl < 0 and pct <= sl:
        return REASON_SL
    return None


def plan_exits(pf: dict, cfg: dict) -> list[tuple[str, str]]:
    """Return list of (ticker, reason_code) to fully close."""
    positions = list(pf.get("positions") or [])
    if not positions:
        return []
    port_reason = decide_portfolio_exit(pf, cfg)
    if port_reason:
        return [
            (str(p.get("ticker") or "").upper(), port_reason)
            for p in positions
            if p.get("ticker") and float(p.get("shares") or 0) > 1e-9
        ]
    out: list[tuple[str, str]] = []
    for p in positions:
        sym = str(p.get("ticker") or "").upper()
        if not sym:
            continue
        reason = decide_position_exit(p, cfg)
        if reason:
            out.append((sym, reason))
    return out


def apply_planned_closes(
    pf: dict,
    prices: dict[str, float],
    planned: list[tuple[str, str]],
    *,
    fill_prefix: str = "paper-ax",
) -> tuple[dict, list[dict]]:
    """Execute full paper sells for planned exits. Returns (portfolio, closed_rows)."""
    if not planned:
        return pf, []

    cash = float(pf.get("cash") or 0)
    fills = list(pf.get("fills") or [])
    realized = float(pf.get("realized_pnl") or 0)
    positions = {
        str(p["ticker"]).upper(): dict(p)
        for p in (pf.get("positions") or [])
        if p.get("ticker")
    }
    fill_i = len(fills)
    closed_rows: list[dict] = []
    reason_by_sym = {s: r for s, r in planned}

    for sym, reason in planned:
        pos = positions.get(sym)
        if not pos:
            continue
        shares = float(pos.get("shares") or 0)
        if shares <= 1e-9:
            continue
        px = float(prices.get(sym) or pos.get("mark") or 0)
        if px <= 0:
            continue
        cost = float(pos.get("cost_basis") or 0)
        notional = round(shares * px, 2)
        pnl = round(notional - cost, 2)
        pct = round((pnl / cost * 100) if cost else 0.0, 4)
        cash = round(cash + notional, 2)
        realized = round(realized + pnl, 2)
        fill_i += 1
        fill = {
            "id": f"{fill_prefix}-{fill_i}",
            "symbol": sym,
            "side": "sell",
            "notional": notional,
            "qty": shares,
            "price": round(px, 4),
            "status": "paper_simulated",
            "reason": reason,
            "reason_ru": REASON_RU.get(reason, reason),
            "realized_pnl": pnl,
            "ts": pf.get("as_of"),
        }
        fills.append(fill)
        closed_rows.append(
            {
                "ticker": sym,
                "reason": reason,
                "reason_ru": REASON_RU.get(reason, reason),
                "notional": notional,
                "qty": shares,
                "price": round(px, 4),
                "realized_pnl": pnl,
                "pnl_pct": pct,
            }
        )
        del positions[sym]

    pos_list = [p for p in positions.values() if float(p.get("shares") or 0) > 1e-9]
    mtm = sum(float(p.get("market_value") or 0) for p in pos_list)
    cost_total = sum(float(p.get("cost_basis") or 0) for p in pos_list)
    start = float(pf.get("starting_cash") or 100)
    equity = round(cash + mtm, 2)
    total_pnl = round(equity - start, 2)
    peak = float(pf.get("peak_equity") or equity)
    peak = max(peak, equity)

    # Cooldown bookkeeping
    cooldown = list(pf.get("exit_cooldown") or [])
    cycles = int(
        (pf.get("auto_exit_config") or {}).get("exit_rebuy_cooldown_cycles")
        or DEFAULT_AUTO_EXIT["exit_rebuy_cooldown_cycles"]
    )
    for row in closed_rows:
        cooldown.append({"ticker": row["ticker"], "cycles_left": cycles})

    out = dict(pf)
    out.update(
        {
            "cash": cash,
            "positions": pos_list,
            "fills": fills,
            "invested_cost": round(cost_total, 2),
            "positions_market_value": round(mtm, 2),
            "equity": equity,
            "unrealized_pnl": round(mtm - cost_total, 2),
            "total_pnl_vs_start": total_pnl,
            "total_pnl_pct": round((total_pnl / start * 100) if start else 0.0, 4),
            "realized_pnl": realized,
            "peak_equity": peak,
            "exit_cooldown": cooldown,
            "last_auto_exits": closed_rows,
            "mode": "paper",
        }
    )
    # stash reasons used (for callers)
    out["_closed"] = closed_rows
    out["_reason_by_sym"] = reason_by_sym
    return out, closed_rows


def tick_exit_cooldown(pf: dict) -> dict:
    """Decrement rebuy cooldowns by one cycle."""
    rows = []
    for row in pf.get("exit_cooldown") or []:
        left = int(row.get("cycles_left") or 0) - 1
        if left > 0:
            rows.append({"ticker": str(row.get("ticker") or "").upper(), "cycles_left": left})
    out = dict(pf)
    out["exit_cooldown"] = rows
    return out


def cooldown_tickers(pf: dict) -> set[str]:
    return {
        str(r.get("ticker") or "").upper()
        for r in (pf.get("exit_cooldown") or [])
        if r.get("ticker") and int(r.get("cycles_left") or 0) > 0
    }


def format_exit_summary_ru(closed: list[dict]) -> str:
    if not closed:
        return "Авто-выход: срабатываний нет"
    parts = []
    for c in closed:
        parts.append(
            f"{c['ticker']} ({c.get('reason_ru')}, P&L ${float(c.get('realized_pnl') or 0):+.2f})"
        )
    return "Авто-выход: закрыто " + "; ".join(parts)
