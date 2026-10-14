import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

size = load("trader-budget", "size")

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
TOP5 = os.path.join(FIX, "trader-budget", "berkshire-q2-2026-top5.json")
REEL = {"AAPL": 1250, "AXP": 1119, "GOOGL": 823, "KO": 708, "BAC": 600}
PRICES = {"AAPL": 341.27, "AXP": 309.84, "GOOGL": 353.78, "KO": 90.11, "BAC": 53.96}


def cfg(**over):
    with open(CFG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def rows(path):
    with open(path) as fh:
        return size.parse_weights(json.load(fh))


def dollars(result):
    return {p["ticker"]: p["dollars"] for p in result["positions"]}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheReel(unittest.TestCase):
    """$5,000, 10% cash, 25% cap: AAPL 1250 (capped), AXP 1119, GOOGL 823, KO 708, BAC 600, cash 500, idle 0."""

    def check_reel(self, r):
        self.assertEqual(dollars(r), REEL)
        self.assertEqual(r["cash_buffer"], 500)
        self.assertEqual(r["invested"], 4500)
        self.assertEqual(r["idle"], 0)
        self.assertEqual(r["cap"], 1250)
        self.assertEqual([p["ticker"] for p in r["positions"] if p["capped"]], ["AAPL"])

    def test_from_the_raw_13f_weights(self):
        self.check_reel(size.size(rows(TOP5), cfg()))

    def test_from_the_renormalised_targets_file(self):
        self.check_reel(size.size(rows(TARGETS), cfg()))

    def test_from_inline_weights(self):
        w = size.parse_weights(size.weights_from_arg("AAPL=22.04,AXP=17.14,GOOGL=12.62,KO=10.86,BAC=9.20"))
        self.check_reel(size.size(w, cfg()))

    def test_renormalised_weights_match_the_reel_labels(self):
        r = size.size(rows(TOP5), cfg())
        shown = {p["ticker"]: round(p["weight"], 1) for p in r["positions"]}
        self.assertEqual(shown, {"AAPL": 30.7, "AXP": 23.9, "GOOGL": 17.6, "KO": 15.1, "BAC": 12.8})

    def test_one_decimal_weights_stay_within_a_dollar(self):
        w = size.parse_weights({"AAPL": 30.7, "AXP": 23.9, "GOOGL": 17.6, "KO": 15.1, "BAC": 12.8})
        r = size.size(w, cfg())
        self.assertEqual(r["invested"], 4500)
        self.assertEqual(dollars(r)["AAPL"], 1250)
        for t, v in dollars(r).items():
            self.assertLessEqual(abs(v - REEL[t]), 1.0, t)

    def test_orders_are_place_stock_order_shaped(self):
        r = size.size(rows(TOP5), cfg())
        orders = size.orders_from(r, "Berkshire 13F")
        self.assertEqual(orders[0], {"symbol": "AAPL", "side": "buy", "notional": "1250", "type": "market",
                                     "time_in_force": "day", "why": orders[0]["why"]})
        self.assertIn("capped at 25%", orders[0]["why"])
        for o in orders:
            self.assertIsInstance(o["notional"], str)
            self.assertNotIn("qty", o)
        self.assertEqual([o["notional"] for o in orders], ["1250", "1119", "823", "708", "600"])

    def test_cents_mode_keeps_the_total(self):
        r = size.size(rows(TOP5), cfg(), cents=True)
        d = dollars(r)
        self.assertEqual(d["AAPL"], 1250)
        self.assertAlmostEqual(sum(d.values()), 4500, places=2)
        self.assertAlmostEqual(d["AXP"], 1118.13, places=2)
        self.assertEqual(size.orders_from(r)[1]["notional"], "1118.13")


class Rules(unittest.TestCase):
    def test_cap_redistributes_until_nobody_is_over(self):
        w = size.parse_weights({"A": 60, "B": 30, "C": 10})
        r = size.size(w, {"budget": 1000, "cash_buffer_pct": 0, "max_position_pct": 40, "min_order_usd": 1})
        self.assertEqual(dollars(r), {"A": 400, "B": 400, "C": 200})
        self.assertEqual(sorted(p["ticker"] for p in r["positions"] if p["capped"]), ["A", "B"])

    def test_everything_capped_leaves_money_idle(self):
        w = size.parse_weights({"AAPL": 50, "KO": 50})
        r = size.size(w, cfg())
        self.assertEqual(dollars(r), {"AAPL": 1250, "KO": 1250})
        self.assertEqual(r["idle"], 2000)
        self.assertIn("every stock is at its cap", size.report(r, size.orders_from(r)))

    def test_slices_under_the_minimum_are_dropped_and_shared_out(self):
        w = size.parse_weights({"A": 90, "B": 7, "C": 3})
        r = size.size(w, {"budget": 100, "cash_buffer_pct": 10, "max_position_pct": 100, "min_order_usd": 5})
        self.assertNotIn("C", dollars(r))
        self.assertEqual(sum(dollars(r).values()), 90)
        self.assertTrue(all(v >= 5 for v in dollars(r).values()))
        self.assertEqual(r["dropped"][0]["ticker"], "C")

    def test_blocked_tickers_are_dropped_first(self):
        r = size.size(rows(TOP5), cfg(blocked=["ko"]))
        self.assertNotIn("KO", dollars(r))
        self.assertEqual(r["invested"], 4500)
        self.assertIn("blocked", r["dropped"][0]["reason"])

    def test_top_n(self):
        r = size.size(rows(TOP5), cfg(max_position_pct=100), top=2)
        self.assertEqual(sorted(dollars(r)), ["AAPL", "AXP"])

    def test_whole_shares_need_prices(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(TOP5), cfg(fractional=False))

    def test_whole_shares_with_prices(self):
        r = size.size(rows(TOP5), cfg(fractional=False), prices=PRICES)
        self.assertTrue(r["whole_shares"])
        for p in r["positions"]:
            self.assertEqual(p["shares"], int(p["shares"]))
            self.assertAlmostEqual(p["dollars"], p["shares"] * PRICES[p["ticker"]], places=2)
            self.assertLessEqual(p["dollars"], 1250)
        self.assertAlmostEqual(r["invested"] + r["idle"], 4500, places=2)
        self.assertLess(r["idle"], min(PRICES.values()) * 2)
        orders = size.orders_from(r)
        self.assertTrue(all("qty" in o and "notional" not in o for o in orders))

    def test_no_budget_is_an_error(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(TOP5), {"cash_buffer_pct": 10})

    def test_duplicate_tickers_are_an_error(self):
        with self.assertRaises(size.SizeError):
            size.size([{"ticker": "KO", "name": "", "weight": 1}, {"ticker": "KO", "name": "", "weight": 2}], cfg())


class Cli(unittest.TestCase):
    def test_write_orders_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--targets", TARGETS, "--config", CFG, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("left idle", text)
            self.assertIn("not financial advice", text)
            with open(out) as fh:
                doc = json.load(fh)
            self.assertEqual(doc["status"], "proposed")
            self.assertIs(doc["paper"], True)
            self.assertEqual([o["symbol"] for o in doc["orders"]], ["AAPL", "AXP", "GOOGL", "KO", "BAC"])

    def test_json_output_and_missing_targets(self):
        code, text, _ = run(["--targets", TARGETS, "--config", CFG, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["result"]["idle"], 0)
        code, _, err = run(["--targets", "/nonexistent/targets.json", "--config", CFG])
        self.assertEqual(code, 1)
        self.assertIn("no targets file", err)


if __name__ == "__main__":
    unittest.main()
