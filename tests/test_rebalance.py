import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

rebalance = load("trader-rebalance", "rebalance")
check = load("trader-risk", "check")

D = os.path.join(FIX, "trader-rebalance")


def read(name):
    with open(os.path.join(D, name)) as fh:
        return json.load(fh)


def setup(cash_add=0, prices=None, **cfg_over):
    pf = read("portfolio.json")
    pf["cash"] += cash_add
    px = dict(read("prices.json")["prices"])
    px.update(prices or {})
    cfg = read("config.json")
    cfg.update(cfg_over)
    return pf, px, rebalance.parse_targets(read("targets.json"), cfg.get("blocked")), cfg


def orders(res):
    return [(o["side"], o["symbol"], o.get("notional") or o.get("qty")) for o in res["orders"]]


class TheReel(unittest.TestCase):
    def test_on_the_day_it_was_bought_nothing_trades(self):
        res = rebalance.plan(*setup())
        self.assertEqual(res["orders"], [])
        self.assertEqual([r["ticker"] for r in res["rows"]], ["AAPL", "AXP", "GOOGL", "KO", "BAC"])
        for r in res["rows"]:
            self.assertLess(abs(r["drift"]), 0.1, r["ticker"])
        self.assertEqual(res["rows"][0]["target_pct"], 25.0)

    def test_new_cash_buys_only_what_left_the_band(self):
        res = rebalance.plan(*setup(cash_add=2000))
        self.assertEqual(res["cap"], 1750)
        self.assertEqual(orders(res), [("buy", "AAPL", "499"), ("buy", "AXP", "446")])
        hold = [r["ticker"] for r in res["rows"] if r["action"] == "hold"]
        self.assertEqual(hold, ["GOOGL", "KO", "BAC"])

    def test_bigger_deposit_buys_everything_and_keeps_the_buffer(self):
        pf, px, w, cfg = setup(cash_add=5000)
        res = rebalance.plan(pf, px, w, cfg)
        self.assertEqual([o[1] for o in orders(res)], ["AAPL", "AXP", "GOOGL", "KO", "BAC"])
        self.assertGreaterEqual(res["cash_after"], res["cash_buffer"])
        risk = check.check(rebalance.clean(res["orders"]), cfg, portfolio=pf, prices=px, today="2026-10-13")
        self.assertEqual(risk["block"], 0, risk["results"])


class Drift(unittest.TestCase):
    def test_sells_come_before_buys(self):
        res = rebalance.plan(*setup(prices={"AAPL": 341.27 * 1.6}))
        sides = [o["side"] for o in res["orders"]]
        self.assertEqual(sides[0], "sell")
        self.assertEqual(res["orders"][0]["symbol"], "AAPL")
        self.assertEqual(sides, sorted(sides, key=lambda s: s != "sell"))
        held = read("portfolio.json")["positions"][0]["shares"]
        self.assertLessEqual(float(res["orders"][0]["qty"]), held)
        self.assertIn("drifted", res["orders"][0]["why"])

    def test_band(self):
        res = rebalance.plan(*setup(cash_add=2000), band=8)
        self.assertEqual(res["orders"], [])
        res = rebalance.plan(*setup(cash_add=2000), band=3)
        self.assertEqual(len(res["orders"]), 5)

    def test_untracked_holdings_are_left_alone_unless_asked(self):
        pf, px, w, cfg = setup()
        pf["positions"].append({"ticker": "DHI", "shares": 2})
        px["DHI"] = 150.0
        res = rebalance.plan(pf, px, w, cfg)
        dhi = [r for r in res["rows"] if r["ticker"] == "DHI"][0]
        self.assertEqual(dhi["action"], "untracked, left alone")
        self.assertNotIn("DHI", [o["symbol"] for o in res["orders"]])
        res = rebalance.plan(pf, px, w, cfg, sell_untracked=True)
        self.assertIn(("sell", "DHI", "2"), orders(res))

    def test_missing_price_is_an_error(self):
        pf, px, w, cfg = setup()
        del px["KO"]
        with self.assertRaises(rebalance.RebalanceError):
            rebalance.plan(pf, px, w, cfg)

    def test_whole_shares(self):
        res = rebalance.plan(*setup(cash_add=5000, fractional=False))
        for o in res["orders"]:
            self.assertEqual(o["qty"], str(int(o["qty"])))
            self.assertNotIn("notional", o)

    def test_max_order_and_scaling(self):
        res = rebalance.plan(*setup(cash_add=20000))
        self.assertTrue(all(o["_usd"] <= 2000 for o in res["orders"]))
        self.assertTrue(any("max order" in n for n in res["notes"]))
        pf, px, w, cfg = setup(cash_add=5000)
        pf["positions"].append({"ticker": "DHI", "shares": 10})
        px["DHI"] = 150.0
        res = rebalance.plan(pf, px, w, cfg)
        self.assertTrue(any("buys scaled" in n for n in res["notes"]), res["notes"])
        self.assertEqual(len(res["orders"]), 5)
        self.assertGreaterEqual(res["cash_after"], res["cash_buffer"] - 1)


class Cli(unittest.TestCase):
    def test_write_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "portfolio.json")
            doc = read("portfolio.json")
            doc["cash"] += 2000
            with open(pf, "w") as fh:
                json.dump(doc, fh)
            out = os.path.join(d, "orders.json")
            base = ["--prices", os.path.join(D, "prices.json"), "--targets", os.path.join(D, "targets.json"),
                    "--config", os.path.join(D, "config.json")]
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = rebalance.main(["--portfolio", pf, "--write", out] + base)
            self.assertEqual(code, 0)
            self.assertIn("not financial advice", buf.getvalue())
            with open(out) as fh:
                written = json.load(fh)
            self.assertEqual(written["created_by"], "trader-rebalance")
            self.assertTrue(all(not k.startswith("_") for o in written["orders"] for k in o))
            with redirect_stdout(io.StringIO()):
                code = rebalance.main(["--portfolio", os.path.join(D, "portfolio.json")] + base)
            self.assertEqual(code, 1)
            err = io.StringIO()
            with redirect_stderr(err):
                code = rebalance.main(["--portfolio", os.path.join(d, "none.json")] + base)
            self.assertEqual(code, 1)
            self.assertIn("no portfolio", err.getvalue())


if __name__ == "__main__":
    unittest.main()
