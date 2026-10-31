"""Paper trading cycle: research → risk → paper fills → ledger."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from . import orchestrator, skills, store
from .paths import CORE_SKILL_NAMES, STATE_DIR, TRADER_DIR, ensure_dirs, mode
from .store import utc_now


DISCLAIMER = (
    "Education/tooling only. Paper simulation. Not financial advice. No live broker orders."
)


def _load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _prices() -> dict[str, float]:
    data = _load_json(TRADER_DIR / "prices.json", {}) or {}
    prices = data.get("prices") or {}
    return {k: float(v) for k, v in prices.items()}


def _config() -> dict:
    return _load_json(TRADER_DIR / "config.json", {}) or {}


def _orders() -> list[dict]:
    data = _load_json(TRADER_DIR / "orders.json", {}) or {}
    if isinstance(data, list):
        return data
    return data.get("orders") or data.get("proposed") or []


def _risk_verdicts() -> dict:
    return _load_json(TRADER_DIR / "state" / "risk.json", {}) or {}


def _parse_order(order: dict) -> dict | None:
    symbol = (order.get("symbol") or order.get("ticker") or "").upper()
    if not symbol:
        return None
    side = (order.get("side") or "buy").lower()
    notional = order.get("notional")
    qty = order.get("qty")
    if notional is not None:
        notional = float(notional)
    if qty is not None:
        qty = float(qty)
    return {"symbol": symbol, "side": side, "notional": notional, "qty": qty, "raw": order}


def paper_fill_orders(
    orders: list[dict],
    prices: dict[str, float],
    *,
    starting_cash: float,
    session: str,
    thesis: dict | None = None,
    config_snapshot: dict | None = None,
) -> dict:
    cash = float(starting_cash)
    positions: dict[str, dict] = {}
    fills: list[dict] = []
    fill_i = 0

    for order in orders:
        parsed = _parse_order(order)
        if not parsed:
            continue
        sym = parsed["symbol"]
        px = prices.get(sym)
        if px is None or px <= 0:
            store.append_event(
                "fill_skip",
                f"Нет цены для {sym}",
                data={"symbol": sym},
            )
            continue

        if parsed["notional"] is not None:
            notional = round(float(parsed["notional"]), 2)
            qty = round(notional / px, 6)
        elif parsed["qty"] is not None:
            qty = float(parsed["qty"])
            notional = round(qty * px, 2)
        else:
            continue

        if parsed["side"] == "buy":
            if notional > cash + 1e-9:
                store.append_event(
                    "fill_skip",
                    f"Недостаточно cash для {sym} (${notional:.2f} > ${cash:.2f})",
                    data={"symbol": sym, "notional": notional, "cash": cash},
                )
                continue
            cash = round(cash - notional, 2)
            pos = positions.get(sym) or {
                "ticker": sym,
                "shares": 0.0,
                "cost_basis": 0.0,
                "lots": [],
            }
            pos["shares"] = round(pos["shares"] + qty, 6)
            pos["cost_basis"] = round(pos["cost_basis"] + notional, 2)
            lot_id = f"{sym}-{len(pos['lots']) + 1}"
            pos["lots"].append(
                {
                    "id": lot_id,
                    "date": utc_now()[:10],
                    "shares": qty,
                    "cost": notional,
                }
            )
            positions[sym] = pos
        else:
            # sell — simple FIFO reduction
            pos = positions.get(sym)
            if not pos or pos["shares"] < qty - 1e-9:
                store.append_event("fill_skip", f"Недостаточно shares для продажи {sym}")
                continue
            proceeds = notional
            cash = round(cash + proceeds, 2)
            # proportional cost reduce
            avg = pos["cost_basis"] / pos["shares"] if pos["shares"] else 0
            pos["shares"] = round(pos["shares"] - qty, 6)
            pos["cost_basis"] = round(max(0.0, pos["cost_basis"] - avg * qty), 2)
            if pos["shares"] <= 1e-9:
                del positions[sym]
            else:
                positions[sym] = pos

        fill_i += 1
        fill = {
            "id": f"PAPER-SIM-{sym}-{fill_i}",
            "symbol": sym,
            "side": parsed["side"],
            "notional": notional,
            "qty": qty,
            "price": px,
            "status": "paper_simulated",
            "ts": utc_now(),
        }
        fills.append(fill)
        store.append_event(
            "fill",
            f"Paper fill {parsed['side']} {sym} qty={qty} @ {px}",
            data=fill,
        )

    pos_list = []
    mtm_total = 0.0
    cost_total = 0.0
    upnl_total = 0.0
    for sym, pos in positions.items():
        mark = float(prices.get(sym) or 0)
        shares = float(pos["shares"])
        cost = float(pos["cost_basis"])
        mv = round(shares * mark, 2)
        upnl = round(mv - cost, 2)
        mtm_total += mv
        cost_total += cost
        upnl_total += upnl
        avg = (cost / shares) if shares else 0.0
        pos_list.append(
            {
                "ticker": sym,
                "shares": shares,
                "cost_basis": cost,
                "avg_cost_per_share": round(avg, 4),
                "mark": mark,
                "market_value": mv,
                "unrealized_pnl": upnl,
                "unrealized_pnl_pct": round((upnl / cost * 100) if cost else 0.0, 4),
                "lots": pos.get("lots") or [],
            }
        )

    equity = round(cash + mtm_total, 2)
    start = float(starting_cash)
    total_pnl = round(equity - start, 2)
    portfolio = {
        "session": session,
        "mode": "paper",
        "currency": "USD",
        "started_at": utc_now()[:10],
        "as_of": utc_now(),
        "price_source": "yahoo",
        "price_note": (
            "Delayed quotes for alerts and sizing checks. "
            "Paper fills use the same marks (ideal execution)."
        ),
        "thesis": thesis or {},
        "starting_cash": start,
        "cash": cash,
        "invested_cost": round(cost_total, 2),
        "positions_market_value": round(mtm_total, 2),
        "equity": equity,
        "unrealized_pnl": round(upnl_total, 2),
        "total_pnl_vs_start": total_pnl,
        "total_pnl_pct": round((total_pnl / start * 100) if start else 0.0, 4),
        "realized_pnl": 0.0,
        "cash_buffer_target": round(start * float((_config().get("cash_buffer_pct") or 10)) / 100, 2),
        "config_snapshot": config_snapshot or {
            k: _config().get(k)
            for k in (
                "budget",
                "cash_buffer_pct",
                "max_position_pct",
                "max_order_usd",
                "min_order_usd",
                "fractional",
                "paper",
                "allow_options",
            )
        },
        "positions": pos_list,
        "fills": fills,
        "disclaimer": DISCLAIMER,
    }
    return portfolio


def _build_report(portfolio: dict, skill_rows: list[dict], extras: dict) -> str:
    lines = [
        f"# Paper-сессия {portfolio.get('session')} — отчёт",
        "",
        f"**Дата:** {portfolio.get('started_at')} · **Счёт:** ${portfolio.get('starting_cash'):.2f} USD · **Режим:** paper only",
        f"**Тезис:** {(portfolio.get('thesis') or {}).get('name', '—')}",
        "",
        "Это симуляция и tooling, не финсовет. Реальных ордеров и брокеров не было.",
        "",
        "## Итог P&L",
        "",
        "| | USD |",
        "| --- | ---: |",
        f"| Старт | {portfolio.get('starting_cash', 0):.2f} |",
        f"| Cash | {portfolio.get('cash', 0):.2f} |",
        f"| Позиции (MTM) | {portfolio.get('positions_market_value', 0):.2f} |",
        f"| Equity | {portfolio.get('equity', 0):.2f} |",
        f"| Unrealized P&L | {portfolio.get('unrealized_pnl', 0):.2f} |",
        f"| Realized P&L | {portfolio.get('realized_pnl', 0):.2f} |",
        f"| P&L к старту | {portfolio.get('total_pnl_vs_start', 0):.2f} |",
        "",
        "## Позиции",
        "",
        "| тикер | shares | cost | mark | MTM | uP&L |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for p in portfolio.get("positions") or []:
        lines.append(
            f"| {p['ticker']} | {p['shares']} | {p['cost_basis']:.2f} | "
            f"{p['mark']:.2f} | {p['market_value']:.2f} | {p['unrealized_pnl']:.2f} |"
        )
    thesis = portfolio.get("thesis") or {}
    signal_rows = thesis.get("signals") or []
    if signal_rows:
        lines += [
            "",
            "## Почему каждый тикер (сигналы skills)",
            "",
            "| тикер | источники | причины |",
            "| --- | --- | --- |",
        ]
        for s in signal_rows:
            src = ", ".join(s.get("sources") or [])
            why = "; ".join(s.get("reasons") or [])
            lines.append(f"| {s.get('ticker')} | {src} | {why} |")
    lines += [
        "",
        "## Что сделал каждый skill",
        "",
        "| skill | результат |",
        "| --- | --- |",
    ]
    for row in skill_rows:
        lines.append(f"| **{row['name']}** | {row['result']} |")
    lines += [
        "",
        "## Риски",
        "",
        "- Paper fills по delayed Yahoo — не реальное исполнение.",
        "- 13F / thesis могут быть устаревшими.",
        "- Крошечный счёт: в live комиссии съели бы долю.",
        "",
        DISCLAIMER,
        "",
    ]
    if extras.get("notes"):
        lines.insert(-2, f"\n## Заметки\n\n{extras['notes']}\n")
    return "\n".join(lines)


def run_paper(
    *,
    session: str = "session-2",
    budget: float | None = None,
    refresh_copycat: bool = True,
    cik: str = "1067983",
    top: int = 3,
    skip_news: bool = False,
    use_research_strategy: bool = False,
) -> dict:
    """Full paper cycle. Never places live orders."""
    ensure_dirs()
    if mode() == "live":
        raise RuntimeError(
            "NITUB_MODE=live запрещён для run paper. Сбросьте режим в paper."
        )

    # Fresh events for this run (state becomes this session)
    store.clear_events()
    store.append_event(
        "session_start",
        f"Старт paper-цикла {session}",
        data={
            "session": session,
            "budget": budget,
            "use_research_strategy": use_research_strategy,
        },
    )

    cfg = _config()
    if budget is not None:
        cfg = dict(cfg)
        cfg["budget"] = budget
        # persist budget for size.py
        path = TRADER_DIR / "config.json"
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["budget"] = budget
        raw["paper"] = True
        store.write_json(path, raw)
        cfg = raw

    starting = float(cfg.get("budget") or 100)
    skill_rows: list[dict] = []

    # Fresh paper sleeve: cash = budget, no prior positions (risk needs this).
    # Preserve SEC UA / other config; only reset account state for this cycle.
    store.write_json(
        TRADER_DIR / "portfolio.json",
        {"cash": starting, "positions": [], "mode": "paper", "session": session},
    )
    # Clear today's broker decision log so risk does not BLOCK as "already placed".
    broker_path = TRADER_DIR / "state" / "broker.json"
    if broker_path.exists():
        archive_broker = STATE_DIR / f"broker-archive-{session}.json"
        try:
            archive_broker.write_text(broker_path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    store.write_json(broker_path, {"orders": [], "note": f"reset for {session} paper cycle"})
    store.append_event(
        "portfolio_reset",
        f"Сброс portfolio+broker → cash=${starting:.2f} для {session}",
    )

    # Targets: signal sleeve (default) OR optional research winner.
    # Mega-cap hardcode (AAPL/MSFT/…) запрещён как единственный путь.
    from . import signals

    thesis: dict[str, Any]
    copycat_out = TRADER_DIR / "state" / "copycat-ref.json"
    do_refresh_copycat = refresh_copycat
    discovery_top = max(int(top or 3), 12)

    if use_research_strategy:
        from . import research

        targets = research.apply_winner_targets(write=True)
        winner = research.load_winner()
        te = winner.get("test_metrics") or {}
        thesis = {
            "name": f"Research winner: {winner.get('strategy_id')} ({winner.get('family')})",
            "source": targets.get("source"),
            "strategy_id": winner.get("strategy_id"),
            "family": winner.get("family"),
            "params": winner.get("params"),
            "verdict": winner.get("verdict"),
            "beats_buyhold_oos": winner.get("beats_buyhold_oos"),
            "oos": {
                "total_return_pct": te.get("total_return_pct"),
                "sharpe": te.get("sharpe"),
                "max_drawdown_pct": te.get("max_drawdown_pct"),
                "vs_spy_ret_oos": winner.get("vs_spy_ret_oos"),
                "vs_equal_ret_oos": winner.get("vs_equal_ret_oos"),
            },
            "rationale_ru": (
                f"Paper-сессия {session}: sleeve из nitub research "
                f"({winner.get('strategy_id')}), отбор только на train; "
                f"OOS verdict={winner.get('verdict')}. Fills только paper. "
                f"Полный pipeline: {len(CORE_SKILL_NAMES)} core skills."
            ),
        }
        skill_rows.append(
            {
                "name": "research",
                "result": (
                    f"winner {winner.get('strategy_id')} → targets "
                    f"({', '.join(w['ticker'] for w in targets.get('weights') or [])})"
                ),
                "status": "ok",
                "group": "meta",
            }
        )
        do_refresh_copycat = True
        tickers = [
            w["ticker"]
            for w in ((_load_json(TRADER_DIR / "targets.json", {}) or {}).get("weights") or [])
            if w.get("ticker")
        ]
        pipeline_rows = orchestrator.run_full_pipeline(
            tickers=tickers or None,
            cik=cik,
            top=top,
            refresh_copycat=do_refresh_copycat,
            copycat_out=copycat_out,
            skip_news=skip_news,
            force_heavy=True,
            cycle=1,
            run_budget_risk=True,
            broker_mode="tickets",
            include_vendor=True,
        )
        skill_rows = orchestrator.merge_skill_rows(skill_rows, pipeline_rows)
    else:
        # --- Signal-driven path ---
        # Wipe prior mega/research targets so discovery не подмешивает старый sleeve.
        store.write_json(
            TRADER_DIR / "targets.json",
            {
                "source": "pending signal sleeve",
                "as_of": store.utc_now(),
                "method": "cleared before skills discovery",
                "weights": [],
            },
        )
        # 1) Discovery: broad 13F → side-file (не пишем сразу в targets как sleeve)
        store.append_event(
            "signal_path",
            f"Signal sleeve: discovery copycat top-{discovery_top} → filters → targets",
            data={"cik": cik, "top": discovery_top},
        )
        if do_refresh_copycat:
            cc = skills.copycat_top(cik=cik, top=discovery_top, out=copycat_out)
            skill_rows.append(
                {
                    "name": "copycat",
                    "result": (
                        f"13F CIK {cik} top-{discovery_top} → {copycat_out.name}"
                        if cc.get("ok")
                        else f"copycat soft ({cc.get('error') or cc.get('returncode')})"
                    ),
                    "status": "ok" if cc.get("ok") else "soft",
                    "group": "core",
                }
            )
        else:
            skill_rows.append(
                {
                    "name": "copycat",
                    "result": f"reuse {copycat_out.name}",
                    "status": "ok",
                    "group": "core",
                }
            )

        discovery = signals.discovery_universe(copycat_path=copycat_out, max_n=16)
        if not discovery:
            store.append_event(
                "signal_path",
                "Discovery пуст — нет кандидатов из skills (не подставляем mega-cap)",
            )

        # 2) Core discovery skills on candidates (без budget/risk — targets ещё нет)
        disc_rows = orchestrator.run_core_skills(
            tickers=discovery,
            cik=cik,
            top=discovery_top,
            refresh_copycat=False,  # уже сделали выше в side-file
            copycat_out=copycat_out,
            skip_news=skip_news,
            force_heavy=True,
            cycle=1,
            run_budget_risk=False,
            broker_mode="tickets",
        )
        skill_rows = orchestrator.merge_skill_rows(skill_rows, disc_rows)

        # 3) Vendor filters (earnings / drawdown / market / valuation)
        vendor_rows = orchestrator.run_vendor_skills(discovery)
        skill_rows = orchestrator.merge_skill_rows(skill_rows, vendor_rows)

        # 4) Build sleeve ONLY from screener + skill filters
        targets = signals.build_signal_targets(
            write=True,
            copycat_path=copycat_out,
            max_positions=8,
            min_signals=1,
        )
        signal_list = targets.get("signals") or []
        thesis = {
            "name": "Signal-driven sleeve (skills pipeline)",
            "source": targets.get("source"),
            "strategy_id": "signal_skills",
            "family": "signal_driven",
            "method": targets.get("method"),
            "signals": signal_list,
            "rejected": targets.get("rejected") or [],
            "regime": targets.get("regime") or {},
            "as_of_holdings": (_load_json(copycat_out, {}) or {}).get("as_of"),
            "filed": (_load_json(copycat_out, {}) or {}).get("filed"),
            "rationale_ru": (
                f"Paper-сессия {session}: тикеры только из pipeline "
                f"(screener shortlist + analyst/copycat/pelosi/news + vendor "
                f"earnings/valuation/market-environment/drawdown). "
                f"Позиций: {len(targets.get('weights') or [])}. "
                f"Без hardcode mega-cap как единственного пути. Fills только paper."
            ),
        }
        skill_rows.append(
            {
                "name": "signals",
                "result": (
                    "sleeve "
                    + (
                        ", ".join(
                            f"{s['ticker']}←{'+'.join(s.get('sources') or [])}"
                            for s in signal_list
                        )
                        or "(пусто — нет PASS)"
                    )
                ),
                "status": "ok" if signal_list else "soft",
                "group": "meta",
            }
        )

        # 5) Size + risk + broker tickets на выбранном sleeve
        sized = orchestrator.run_core_skills(
            tickers=[w["ticker"] for w in (targets.get("weights") or []) if w.get("ticker")],
            cik=cik,
            top=discovery_top,
            refresh_copycat=False,
            copycat_out=copycat_out,
            skip_news=True,  # уже гоняли
            force_heavy=False,
            cycle=2,
            run_budget_risk=True,
            broker_mode="tickets",
        )
        # Keep discovery results for heavy skills; only take sizing/risk/broker/tax/rebalance
        keep = {"budget", "risk", "broker", "tax", "rebalance", "alerts", "screener"}
        skill_rows = orchestrator.merge_skill_rows(
            skill_rows,
            [r for r in sized if r.get("name") in keep],
        )
        orchestrator.publish_catalog(skill_rows)

    tickers = [
        w["ticker"]
        for w in ((_load_json(TRADER_DIR / "targets.json", {}) or {}).get("weights") or [])
        if w.get("ticker")
    ]

    news_items: list = []
    news_state = _load_json(TRADER_DIR / "state" / "news.json", {})
    if isinstance(news_state, dict) and news_state.get("items"):
        news_items = news_state["items"]
    elif isinstance(news_state, list):
        news_items = news_state

    orders = _orders()
    # Refresh budget/risk result lines from files (pipeline already ran them)
    risk = _risk_verdicts()
    verdicts = risk.get("results") or risk.get("orders") or risk.get("verdicts") or []
    passed: list[dict] = []
    if verdicts:
        for v in verdicts:
            status = (v.get("status") or v.get("verdict") or "").upper()
            if status != "PASS":
                continue
            idx = v.get("index")
            matched = None
            if isinstance(idx, int) and 1 <= idx <= len(orders):
                matched = dict(orders[idx - 1])
            else:
                matched = {
                    "symbol": v.get("symbol"),
                    "side": v.get("side") or "buy",
                    "notional": v.get("value_usd") or v.get("notional"),
                    "qty": v.get("qty"),
                }
            if matched.get("notional") is not None:
                try:
                    matched["notional"] = float(matched["notional"])
                except (TypeError, ValueError):
                    pass
            passed.append(matched)
    else:
        store.append_event(
            "risk_fallback",
            "risk.json пуст — soft-check: берём все proposed orders",
            skill="risk",
        )
        passed = [
            {
                **o,
                "notional": float(o["notional"]) if o.get("notional") is not None else None,
            }
            for o in orders
        ]

    # Paper fills (never live broker). Override broker row with fill count.
    store.append_event(
        "broker",
        "Paper simulator only — live broker отключён",
        skill="broker",
    )
    prices = _prices()
    if not prices and tickers:
        skills.quote_tickers(tickers, write=True)
        prices = _prices()

    portfolio = paper_fill_orders(
        passed,
        prices,
        starting_cash=starting,
        session=session,
        thesis=thesis,
        config_snapshot={
            k: cfg.get(k)
            for k in (
                "budget",
                "cash_buffer_pct",
                "max_position_pct",
                "max_order_usd",
                "min_order_usd",
                "fractional",
                "paper",
                "allow_options",
            )
        },
    )
    skill_rows = orchestrator.merge_skill_rows(
        skill_rows,
        [
            {
                "name": "broker",
                "result": f"paper fills ×{len(portfolio.get('fills') or [])} (без живого брокера)",
                "status": "ok",
                "group": "core",
            },
            {
                "name": "budget",
                "result": f"{len(orders)} proposed orders, budget ${starting:.0f}",
                "status": "ok",
                "group": "core",
            },
            {
                "name": "risk",
                "result": f"PASS ×{len(passed)}" if passed else "нет PASS",
                "status": "ok" if passed else "soft",
                "group": "core",
            },
        ],
    )

    # Sync trader/portfolio.json for downstream skills (tax already ran once;
    # re-run tax + rebalance against filled sleeve so artifacts match fills)
    trader_pf = {
        "cash": portfolio["cash"],
        "positions": [
            {
                "symbol": p["ticker"],
                "qty": p["shares"],
                "lots": p.get("lots") or [],
            }
            for p in portfolio.get("positions") or []
        ],
    }
    store.write_json(TRADER_DIR / "portfolio.json", trader_pf)

    tx = skills.tax_report(soft=True)
    rb = skills.rebalance_check(write=False, soft=True)
    if rb.get("ok") and rb.get("no_orders"):
        rb_msg = "в пределах band — ордеров нет (exit 1 = ok)"
    elif rb.get("ok"):
        rb_msg = "drift after fills (no write)"
    else:
        rb_msg = "rebalance soft/skip"
    skill_rows = orchestrator.merge_skill_rows(
        skill_rows,
        [
            {
                "name": "tax",
                "result": "lots after fills" if tx.get("ok") else "tax soft/skip",
                "status": "ok" if tx.get("ok") else "soft",
                "group": "core",
            },
            {
                "name": "rebalance",
                "result": rb_msg,
                "status": "ok" if rb.get("ok") else "soft",
                "group": "core",
            },
        ],
    )
    orchestrator.publish_catalog(skill_rows)

    shortlist = _load_json(TRADER_DIR / "shortlist.json", {}) or {}
    targets_now = _load_json(TRADER_DIR / "targets.json", {}) or {}
    analysis = {
        "thesis": thesis,
        "orders_proposed": len(orders),
        "orders_passed": len(passed),
        "fills": len(portfolio.get("fills") or []),
        "equity": portfolio.get("equity"),
        "pnl": portfolio.get("total_pnl_vs_start"),
        "skills_catalog": orchestrator.catalog(),
        "skills_completed": [r["name"] for r in skill_rows],
        "screener": (
            f"shortlist {len((shortlist.get('passed') or shortlist.get('shortlist') or []))}"
            if shortlist
            else None
        ),
        "analyst": next(
            (r["result"] for r in skill_rows if r["name"] == "analyst"),
            None,
        ),
        "signals": targets_now.get("signals")
        or thesis.get("signals")
        or [
            {
                "ticker": w.get("ticker"),
                "sources": w.get("sources") or [],
                "reasons": w.get("reasons") or [],
            }
            for w in (targets_now.get("weights") or [])
            if w.get("ticker")
        ],
        "positions_why": {
            (p.get("ticker") or p.get("symbol")): next(
                (
                    {
                        "sources": w.get("sources"),
                        "reasons": w.get("reasons"),
                    }
                    for w in (targets_now.get("weights") or [])
                    if w.get("ticker") == (p.get("ticker") or p.get("symbol"))
                ),
                None,
            )
            for p in (portfolio.get("positions") or [])
        },
    }
    report = _build_report(portfolio, skill_rows, {})
    store.publish_state(
        portfolio,
        report=report,
        news=news_items,
        skills=skill_rows,
        analysis=analysis,
        archive=True,
    )
    store.append_event(
        "session_end",
        f"Готово: equity=${portfolio['equity']:.2f} pnl={portfolio['total_pnl_vs_start']:.2f}",
        data={
            "equity": portfolio["equity"],
            "pnl": portfolio["total_pnl_vs_start"],
            "fills": len(portfolio.get("fills") or []),
            "state": str(STATE_DIR),
        },
    )
    return portfolio


def status_text() -> str:
    pf = store.load_portfolio()
    if not pf:
        return "Нет активного portfolio.json в nitub/state/. Запустите: nitub run paper"
    lines = [
        f"Сессия: {pf.get('session')} · режим: {pf.get('mode')}",
        f"Equity: ${pf.get('equity', 0):.2f}",
        f"Cash:   ${pf.get('cash', 0):.2f}",
        f"MTM:    ${pf.get('positions_market_value', 0):.2f}",
        f"uP&L:   ${pf.get('unrealized_pnl', 0):.2f}",
        f"P&L:    ${pf.get('total_pnl_vs_start', 0):.2f} ({pf.get('total_pnl_pct', 0):.2f}%)",
        f"as_of:  {pf.get('as_of')}",
        "",
        "Позиции:",
    ]
    for p in pf.get("positions") or []:
        lines.append(
            f"  {p['ticker']:6} shares={p['shares']} mark={p['mark']:.2f} "
            f"MTM={p['market_value']:.2f} uP&L={p['unrealized_pnl']:.2f}"
        )
    if not pf.get("positions"):
        lines.append("  (пусто)")
    events = store.read_events(limit=8)
    if events:
        lines.append("")
        lines.append("Последние события:")
        for e in events[-8:]:
            lines.append(f"  [{e.get('ts')}] {e.get('kind')}: {e.get('summary')}")
    return "\n".join(lines)
