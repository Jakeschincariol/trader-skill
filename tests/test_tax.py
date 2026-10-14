import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

lots = load("trader-tax", "lots")

PF_PATH = os.path.join(FIX, "trader-tax", "portfolio.json")
PRICES_PATH = os.path.join(FIX, "trader-tax", "prices.json")
TODAY = datetime.date(2026, 10, 13)
D = datetime.date


def pf():
    with open(PF_PATH) as fh:
        return json.load(fh)


def prices():
    with open(PRICES_PATH) as fh:
        return json.load(fh)["prices"]


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = lots.main(argv)
    return code, out.getvalue(), err.getvalue()


class Term(unittest.TestCase):
    def test_more_than_one_year_is_long(self):
        self.assertEqual(lots.term(D(2025, 10, 13), D(2026, 10, 13)), "short")
        self.assertEqual(lots.term(D(2025, 10, 13), D(2026, 10, 14)), "long")

    def test_leap_day(self):
        self.assertEqual(lots.term(D(2024, 2, 29), D(2025, 2, 28)), "short")
        self.assertEqual(lots.term(D(2024, 2, 29), D(2025, 3, 1)), "long")


class Lots(unittest.TestCase):
    def test_unrealized_by_lot(self):
        rows, totals, missing = lots.unrealized(pf(), prices(), TODAY)
        self.assertEqual(missing, [])
        by = {r["id"]: r for r in rows}
        self.assertEqual(by["KO-1"]["term"], "long")
        self.assertAlmostEqual(by["KO-1"]["gain"], -148.9, places=2)
        self.assertEqual(by["BAC-1"]["term"], "short")
        self.assertEqual(by["AAPL-1"]["long_term_on"], "2027-10-14")
        axp = [t for t in totals if t["ticker"] == "AXP"][0]
        self.assertAlmostEqual(axp["gain"], 1239.36 - 1030, places=2)
        self.assertAlmostEqual(axp["long"], 239.52, places=2)
        self.assertAlmostEqual(axp["short"], -30.16, places=2)

    def test_fifo_hifo_specific(self):
        axp = lots.lots_of(pf())["AXP"]
        fifo = lots.simulate_sale(axp, 3, 309.84, TODAY)
        self.assertEqual([u["id"] for u in fifo["lots"]], ["AXP-1"])
        self.assertAlmostEqual(fifo["gain"], 239.52, places=2)
        self.assertAlmostEqual(fifo["long"], 239.52, places=2)
        hifo = lots.simulate_sale(axp, 3, 309.84, TODAY, "hifo")
        self.assertEqual([u["id"] for u in hifo["lots"]], ["AXP-2", "AXP-1"])
        self.assertAlmostEqual(hifo["short"], -30.16, places=2)
        self.assertAlmostEqual(hifo["long"], 159.68, places=2)
        spec = lots.simulate_sale(axp, 1, 309.84, TODAY, "specific", ["AXP-2"])
        self.assertAlmostEqual(spec["gain"], -30.16, places=2)
        with self.assertRaises(lots.TaxError):
            lots.simulate_sale(axp, 5, 309.84, TODAY)
        with self.assertRaises(lots.TaxError):
            lots.simulate_sale(axp, 1, 309.84, TODAY, "specific", ["AXP-9"])

    def test_example_portfolio_is_the_reel_buy(self):
        from _load import ROOT
        with open(os.path.join(ROOT, "trader-tax", "portfolio.example.json")) as fh:
            ex = json.load(fh)
        by = lots.lots_of(ex)
        self.assertEqual({t: round(l[0]["cost"]) for t, l in by.items()},
                         {"AAPL": 1250, "AXP": 1119, "GOOGL": 823, "KO": 708, "BAC": 600})
        self.assertEqual(ex["cash"], 500)

    def test_shares_without_lots_are_refused(self):
        with self.assertRaises(lots.TaxError):
            lots.lots_of({"positions": [{"ticker": "KO", "shares": 3}]})


class WashSales(unittest.TestCase):
    def test_harvest_finds_losses_and_skips_wash_sales(self):
        c = lots.harvest(pf(), prices(), TODAY, min_loss=100)
        self.assertEqual([x["ticker"] for x in c], ["KO", "BAC"])
        ko, bac = c
        self.assertEqual(ko["wash"], [])
        self.assertEqual(ko["rebuy_on_or_after"], "2026-11-13")
        self.assertAlmostEqual(ko["long"], -148.9, places=2)
        self.assertEqual(bac["lots"], ["BAC-1"])
        self.assertEqual(bac["wash"][0]["date"], "2026-10-01")
        self.assertAlmostEqual(bac["disallowed"], 22.08, places=2)
        orders = lots.harvest_orders(c)
        self.assertEqual(orders, [{"symbol": "KO", "side": "sell", "qty": "10", "type": "market",
                                   "time_in_force": "day", "why": orders[0]["why"]}])
        self.assertIn("Not tax advice", orders[0]["why"])
        both = lots.harvest_orders(c, include_wash=True)
        self.assertEqual([o["symbol"] for o in both], ["KO", "BAC"])
        self.assertNotIn("not your oldest lots", both[1]["why"])

    def test_threshold(self):
        self.assertEqual(lots.harvest(pf(), prices(), TODAY, min_loss=200), [])
        c = lots.harvest(pf(), prices(), TODAY, min_loss=20)
        self.assertIn("AXP", [x["ticker"] for x in c])
        axp = [x for x in c if x["ticker"] == "AXP"][0]
        self.assertFalse(axp["fifo_same"])
        self.assertIn("not your oldest lots", lots.harvest_orders([axp])[0]["why"])

    def test_a_planned_buy_is_a_wash_sale(self):
        planned = [{"symbol": "KO", "side": "buy", "notional": "200", "type": "market", "time_in_force": "day"}]
        ko = [x for x in lots.harvest(pf(), prices(), TODAY, 100, orders=planned) if x["ticker"] == "KO"][0]
        self.assertEqual(ko["wash"][0]["source"], "a planned buy in trader/orders.json")
        self.assertEqual(lots.harvest_orders([ko]), [])

    def test_share_classes_are_one_family(self):
        p = {"cash": 0, "positions": [
            {"ticker": "GOOGL", "lots": [{"id": "GOOGL-1", "date": "2026-01-05", "shares": 2, "cost": 800}]},
            {"ticker": "GOOG", "lots": [{"id": "GOOG-1", "date": "2026-10-05", "shares": 1, "cost": 340}]}]}
        c = lots.harvest(p, {"GOOGL": 353.78, "GOOG": 352.0}, TODAY, min_loss=50)
        self.assertEqual(c[0]["ticker"], "GOOGL")
        self.assertEqual(c[0]["wash"][0]["ticker"], "GOOG")
        self.assertEqual(lots.family("brk.b"), {"BRK.A", "BRK.B", "BRK-A", "BRK-B"})

    def test_record_sale_and_rebuy_window(self):
        p, st = pf(), {"realized": []}
        sale = lots.simulate_sale(lots.lots_of(p)["KO"], 4, 90.11, TODAY)
        p, st = lots.record_sale(p, "KO", sale, TODAY, 90.11, st)
        ko = [x for x in p["positions"] if x["ticker"] == "KO"][0]
        self.assertEqual(ko["shares"], 6)
        self.assertEqual(ko["lots"][0]["cost"], 630.0)
        self.assertAlmostEqual(p["cash"], 500 + 360.44, places=2)
        self.assertAlmostEqual(st["realized"][0]["gain"], -59.56, places=2)
        self.assertEqual(st["realized"][0]["long"], -59.56)
        self.assertEqual(len(lots.recent_loss_sales("KO", D(2026, 10, 20), st["realized"])), 1)
        self.assertEqual(lots.recent_loss_sales("KO", D(2026, 11, 13), st["realized"]), [])
        self.assertEqual(lots.realized_ytd(st, 2026)["long"], -59.56)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.pf = os.path.join(self.d, "portfolio.json")
        shutil.copy(PF_PATH, self.pf)

    def tearDown(self):
        shutil.rmtree(self.d)

    def common(self):
        return ["--portfolio", self.pf, "--prices", PRICES_PATH, "--state", os.path.join(self.d, "tax.json"),
                "--orders", os.path.join(self.d, "orders.json"), "--today", "2026-10-13"]

    def test_report(self):
        code, text, _ = run(["report", "--lots"] + self.common())
        self.assertEqual(code, 0)
        self.assertIn("KO-1", text)
        self.assertIn("Not tax advice", text)

    def test_harvest_write_then_wash(self):
        code, text, _ = run(["harvest", "--min-loss", "100", "--write", os.path.join(self.d, "orders.json")]
                            + self.common())
        self.assertEqual(code, 0)
        self.assertIn("WASH SALE RISK", text)
        with open(os.path.join(self.d, "orders.json")) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["created_by"], "trader-tax")
        self.assertEqual([o["symbol"] for o in doc["orders"]], ["KO"])
        code, _, _ = run(["harvest", "--min-loss", "1000"] + self.common())
        self.assertEqual(code, 1)

        code, text, _ = run(["sell", "KO", "4", "--price", "90.11", "--record"] + self.common())
        self.assertEqual(code, 0)
        self.assertIn("long term", text)
        code, text, _ = run(["wash", "KO", "--date", "2026-10-20"] + self.common())
        self.assertEqual(code, 1)
        self.assertIn("2026-11-13", text)
        code, _, _ = run(["wash", "KO", "--date", "2026-11-13"] + self.common())
        self.assertEqual(code, 0)

    def test_missing_portfolio(self):
        code, _, err = run(["report", "--portfolio", os.path.join(self.d, "nope.json")])
        self.assertEqual(code, 1)
        self.assertIn("no portfolio", err)


if __name__ == "__main__":
    unittest.main()
