"""Smoke: paper auto-exit close + reopen path (fixture prices, без сети)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nitub import auto_exit  # noqa: E402
from nitub import runner  # noqa: E402
from nitub import store  # noqa: E402


def _pos(ticker: str, shares: float, cost: float, mark: float) -> dict:
    mv = round(shares * mark, 2)
    upnl = round(mv - cost, 2)
    return {
        "ticker": ticker,
        "shares": shares,
        "cost_basis": cost,
        "avg_cost_per_share": round(cost / shares, 4) if shares else 0,
        "mark": mark,
        "market_value": mv,
        "unrealized_pnl": upnl,
        "unrealized_pnl_pct": round((upnl / cost * 100) if cost else 0.0, 4),
        "lots": [],
    }


def _pf(**over) -> dict:
    base = {
        "session": "fixture-auto-exit",
        "mode": "paper",
        "starting_cash": 100.0,
        "cash": 10.0,
        "equity": 100.0,
        "peak_equity": 100.0,
        "realized_pnl": 0.0,
        "fills": [],
        "positions": [],
        "config_snapshot": {"cash_buffer_pct": 10},
        "as_of": "2026-10-31T12:00:00Z",
    }
    base.update(over)
    return base


class TestAutoExitDecide(unittest.TestCase):
    def test_defaults(self):
        cfg = auto_exit.merge_auto_exit_config()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["take_profit_pct"], 4.0)
        self.assertEqual(cfg["stop_loss_pct"], -2.5)

    def test_take_profit_position(self):
        cfg = auto_exit.merge_auto_exit_config()
        # 1 share cost $40 → mark $42 = +5% TP
        pos = _pos("AAPL", 1.0, 40.0, 42.0)
        self.assertEqual(auto_exit.decide_position_exit(pos, cfg), auto_exit.REASON_TP)

    def test_stop_loss_position(self):
        cfg = auto_exit.merge_auto_exit_config()
        pos = _pos("MSFT", 1.0, 40.0, 38.8)  # −3%
        self.assertEqual(auto_exit.decide_position_exit(pos, cfg), auto_exit.REASON_SL)

    def test_flat_no_exit(self):
        cfg = auto_exit.merge_auto_exit_config()
        pos = _pos("GOOGL", 1.0, 40.0, 40.2)  # +0.5%
        self.assertIsNone(auto_exit.decide_position_exit(pos, cfg))

    def test_portfolio_drawdown(self):
        cfg = auto_exit.merge_auto_exit_config()
        pf = _pf(equity=95.0, peak_equity=100.0)  # −5% from peak ≥ 4%
        self.assertEqual(auto_exit.decide_portfolio_exit(pf, cfg), auto_exit.REASON_DRAWDOWN)

    def test_portfolio_take_profit(self):
        cfg = auto_exit.merge_auto_exit_config()
        pf = _pf(equity=106.0, peak_equity=106.0, starting_cash=100.0)
        self.assertEqual(
            auto_exit.decide_portfolio_exit(pf, cfg), auto_exit.REASON_PORTFOLIO_TP
        )


class TestCloseAndReopen(unittest.TestCase):
    def test_close_then_reopen_path(self):
        """Simulate TP on one name, then buy research sleeve with freed cash."""
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            state.mkdir()
            trader = Path(tmp) / "trader"
            trader.mkdir()
            (trader / "config.json").write_text(
                '{"budget": 100, "cash_buffer_pct": 10, "auto_exit": {"enabled": true, '
                '"take_profit_pct": 4.0, "stop_loss_pct": -2.5, '
                '"portfolio_take_profit_pct": 99, "max_drawdown_pct": 99, '
                '"min_position_pnl_usd": 0.01, "exit_rebuy_cooldown_cycles": 0}}\n',
                encoding="utf-8",
            )
            # Winner sleeve for reopen
            (state / "strategy.json").write_text(
                '{"params": {"tickers": ["AAPL", "MSFT", "GOOGL"], '
                '"weights": [0.4, 0.3, 0.3]}}\n',
                encoding="utf-8",
            )

            # AAPL +5.56% TP; MSFT/GOOGL flat at cost
            pf = _pf(
                cash=10.0,
                positions=[
                    _pos("AAPL", 1.0, 36.0, 38.0),
                    _pos("MSFT", 0.5, 27.0, 54.0),  # mark=cost/shares
                    _pos("GOOGL", 0.1, 27.0, 270.0),
                ],
            )
            pf["equity"] = round(
                10.0
                + sum(float(p["market_value"]) for p in pf["positions"]),
                2,
            )
            pf["peak_equity"] = pf["equity"]

            prices = {"AAPL": 38.0, "MSFT": 54.0, "GOOGL": 270.0}

            with mock.patch.object(runner, "STATE_DIR", state), mock.patch.object(
                store, "STATE_DIR", state
            ), mock.patch.object(runner, "TRADER_DIR", trader), mock.patch.object(
                runner, "_fetch_prices", return_value=prices
            ), mock.patch.object(
                runner, "write_runner", side_effect=lambda patch: {**patch}
            ):
                # Persist empty runner.json for load_auto_exit_cfg
                store.write_json(state / "runner.json", {"running": True})
                store.write_json(state / store.PORTFOLIO, pf)

                planned = auto_exit.plan_exits(
                    pf, auto_exit.merge_auto_exit_config({"auto_exit": {
                        "take_profit_pct": 4.0,
                        "stop_loss_pct": -2.5,
                        "portfolio_take_profit_pct": 99,
                        "max_drawdown_pct": 99,
                        "min_position_pnl_usd": 0.01,
                    }})
                )
                self.assertEqual(planned, [("AAPL", auto_exit.REASON_TP)])

                cfg = auto_exit.merge_auto_exit_config(
                    store.read_json(trader / "config.json")
                )
                out = runner.run_risk_cycle(pf, prices, budget=100.0, cfg=cfg)

                sells = [
                    f
                    for f in (out.get("fills") or [])
                    if f.get("side") == "sell" and f.get("symbol") == "AAPL"
                ]
                self.assertTrue(sells, "expected AAPL sell fill after TP")
                self.assertGreater(float(out.get("realized_pnl") or 0), 0)

                tickers = {p["ticker"] for p in (out.get("positions") or [])}
                # MSFT/GOOGL remain; AAPL may stay out (cooldown 0 → can rebuy)
                self.assertIn("MSFT", tickers)
                self.assertIn("GOOGL", tickers)

                events_path = state / "events.jsonl"
                self.assertTrue(events_path.exists())
                text = events_path.read_text(encoding="utf-8")
                self.assertIn("Авто-выход", text)
                self.assertIn("тейк-профит", text)


class TestStopDoesNotClose(unittest.TestCase):
    def test_request_stop_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            state.mkdir()
            with mock.patch.object(runner, "STATE_DIR", state), mock.patch.object(
                store, "STATE_DIR", state
            ):
                runner.clear_stop_flag()
                runner.request_stop()
                data = runner.read_runner()
                self.assertIn("позиции не закрываем", data.get("message", ""))
                self.assertTrue((state / "runner.stop").exists())


if __name__ == "__main__":
    unittest.main()
