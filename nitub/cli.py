#!/usr/bin/env python3
"""NITUB CLI — единая точка входа paper-first платформы."""

from __future__ import annotations

import argparse
import json
import sys


def cmd_run(args: argparse.Namespace) -> int:
    mode = args.mode
    if mode == "live":
        from . import live

        print(live.refuse_live(explicit_flag=bool(args.i_really_want_live)), file=sys.stderr)
        return 2

    if mode != "paper":
        print(f"Неизвестный режим: {mode}. Доступно: paper | live(stub)", file=sys.stderr)
        return 2

    from . import paper

    portfolio = paper.run_paper(
        session=args.session,
        budget=args.budget,
        refresh_copycat=not args.reuse_targets and not args.use_research_strategy,
        cik=args.cik,
        top=args.top,
        skip_news=args.skip_news,
        use_research_strategy=bool(args.use_research_strategy),
    )
    print(
        json.dumps(
            {
                "ok": True,
                "session": portfolio.get("session"),
                "equity": portfolio.get("equity"),
                "cash": portfolio.get("cash"),
                "pnl": portfolio.get("total_pnl_vs_start"),
                "fills": len(portfolio.get("fills") or []),
                "positions": [
                    {"ticker": p["ticker"], "shares": p["shares"], "mtm": p["market_value"]}
                    for p in (portfolio.get("positions") or [])
                ],
                "state": "nitub/state/",
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    from . import paper

    print(paper.status_text())
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from . import backtest

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    result = backtest.run_backtest(
        symbols=symbols,
        cash=args.cash,
        fromdate=args.from_date,
        todate=args.to_date,
        fast=args.fast,
        slow=args.slow,
    )
    print(backtest.format_backtest(result))
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    from . import research

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    train = research.Window("train", args.train_from, args.train_to)
    test = research.Window("test", args.test_from, args.test_to)
    if args.refine and args.family:
        payload = research.refine_around_winner(
            base_family=args.family,
            base_params=json.loads(args.params or "{}"),
            symbols=symbols,
            cash=args.cash,
            data_start=args.data_start,
            train=train,
            test=test,
            cost_bps=args.cost_bps,
        )
    else:
        payload = research.run_research(
            symbols=symbols,
            cash=args.cash,
            data_start=args.data_start,
            train=train,
            test=test,
            cost_bps=args.cost_bps,
        )
    print(research.format_research_summary(payload))
    if args.json:
        # compact: selection + top ranked only
        compact = {
            "run_id": payload.get("run_id"),
            "verdict": (payload.get("selection") or {}).get("verdict"),
            "winner": (payload.get("selection") or {}).get("winner"),
            "ranked": (payload.get("selection") or {}).get("ranked"),
            "benchmarks_oos": (payload.get("selection") or {}).get("benchmarks_oos"),
        }
        print(json.dumps(compact, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    from . import store

    rows = store.read_events(limit=args.limit)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        for e in rows:
            skill = f" [{e['skill']}]" if e.get("skill") else ""
            print(f"{e.get('ts')}  {e.get('kind')}{skill}: {e.get('summary')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nitub",
        description="NITUB — paper-first торговая платформа (research → risk → paper fills).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Запуск цикла (paper по умолчанию; live — заглушка)")
    run.add_argument("mode", nargs="?", default="paper", choices=["paper", "live"])
    run.add_argument("--session", default="session-2", help="Имя сессии (архив paper-trading/)")
    run.add_argument("--budget", type=float, default=None, help="Бюджет USD (пишет в trader/config.json)")
    run.add_argument("--cik", default="1067983", help="CIK для copycat (Berkshire по умолчанию)")
    run.add_argument("--top", type=int, default=3, help="Top-N позиций из 13F")
    run.add_argument("--reuse-targets", action="store_true", help="Не дергать SEC, взять targets.json")
    run.add_argument(
        "--use-research-strategy",
        action="store_true",
        help="Взять sleeve из nitub/state/backtests/winner.json (после nitub research)",
    )
    run.add_argument("--skip-news", action="store_true")
    run.add_argument(
        "--i-really-want-live",
        action="store_true",
        help="Явный флаг для live-заглушки (всё равно отказ без ключей)",
    )
    run.set_defaults(func=cmd_run)

    st = sub.add_parser("status", help="Equity / positions / P&L из nitub/state")
    st.set_defaults(func=cmd_status)

    bt = sub.add_parser("backtest", help="SMA crossover smoke через backtrader + yfinance")
    bt.add_argument("--symbols", default="AAPL,GOOGL", help="Тикеры через запятую")
    bt.add_argument("--cash", type=float, default=100000.0)
    bt.add_argument("--from-date", default="2024-01-01")
    bt.add_argument("--to-date", default=None)
    bt.add_argument("--fast", type=int, default=10)
    bt.add_argument("--slow", type=int, default=30)
    bt.add_argument("--json", action="store_true")
    bt.set_defaults(func=cmd_backtest)

    rs = sub.add_parser(
        "research",
        help="Сетка стратегий + train/test vs buy&hold (честный OOS)",
    )
    rs.add_argument(
        "--symbols",
        default="AAPL,MSFT,GOOGL,AMZN,SPY",
        help="Тикеры через запятую",
    )
    rs.add_argument("--cash", type=float, default=100000.0)
    rs.add_argument("--data-start", default="2020-01-01")
    rs.add_argument("--train-from", default="2021-01-01")
    rs.add_argument("--train-to", default="2023-12-31")
    rs.add_argument("--test-from", default="2024-01-01")
    rs.add_argument("--test-to", default=None)
    rs.add_argument("--cost-bps", type=float, default=10.0)
    rs.add_argument("--refine", action="store_true", help="Локальная сетка вокруг family")
    rs.add_argument("--family", default=None, help="Для --refine: momentum|trend_sma|…")
    rs.add_argument("--params", default="{}", help="JSON params для --refine")
    rs.add_argument("--json", action="store_true")
    rs.set_defaults(func=cmd_research)

    ev = sub.add_parser("events", help="Показать events.jsonl")
    ev.add_argument("--limit", type=int, default=50)
    ev.add_argument("--json", action="store_true")
    ev.set_defaults(func=cmd_events)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nпрервано", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
