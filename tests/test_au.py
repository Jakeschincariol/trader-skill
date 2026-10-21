"""trader-tax-au, trader-news-au and trader-analyst-au. Offline."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

cgt = load("trader-tax-au", "cgt")
news = load("trader-news-au", "news_au")
facts = load("trader-analyst-au", "facts_au")

REPORT = os.path.join(FIX, "trader-asx", "report-TST.json")


def portfolio():
    p = {"cash": 500.0, "positions": []}
    cgt.add_lot(p, "VAS", 20, 90, "2025-10-20", 0)
    cgt.add_lot(p, "VAS", 10, 99, "2026-01-10", 10)
    cgt.add_lot(p, "BHP", 30, 46, "2026-03-01", 11)
    return p


class Tax(unittest.TestCase):
    def test_add_lot_and_cost_base(self):
        p = portfolio()
        vas = cgt.position(p, "VAS")
        self.assertEqual(vas["shares"], 30)
        self.assertEqual([l["cost"] for l in vas["lots"]], [1800, 1000])
        with self.assertRaises(cgt.CGTError):
            cgt.add_lot(p, "VAS", 1, 100, "2026-01-01", 0, lot_id="VAS-1")

    def test_min_tax_beats_fifo(self):
        p = portfolio()
        mt = cgt.sale(p, "VAS", 25, 104.55, "2026-10-21", 11)
        ff = cgt.sale(p, "VAS", 25, 104.55, "2026-10-21", 11, "fifo")
        self.assertEqual([l["id"] for l in mt["lots"]], ["VAS-2", "VAS-1"])
        self.assertEqual([l["id"] for l in ff["lots"]], ["VAS-1", "VAS-2"])
        ncg = lambda t: cgt.net_capital_gain(t["discounted_gain"], t["undiscounted_gain"], -t["losses"])["net_capital_gain"]
        self.assertAlmostEqual(ncg(mt), 146.93, places=2)
        self.assertAlmostEqual(ncg(ff), 161.65, places=2)

    def test_specific_lots_and_errors(self):
        p = portfolio()
        t = cgt.sale(p, "VAS", 10, 104.55, "2026-10-21", 0, "specific", ["VAS-2"])
        self.assertEqual(t["lots"][0]["id"], "VAS-2")
        with self.assertRaises(cgt.CGTError):
            cgt.sale(p, "VAS", 31, 104.55, "2026-10-21", 0)
        with self.assertRaises(cgt.CGTError):
            cgt.sale(p, "CSL", 1, 200, "2026-10-21", 0)

    def test_ato_loss_ordering(self):
        # losses go against undiscounted gains first, then the discount halves what is left
        y = cgt.net_capital_gain(disc=282.20, nondisc=20.55, losses=202.0)
        self.assertEqual(y["net_capital_gain"], 50.38)
        y = cgt.net_capital_gain(disc=1000, nondisc=0, losses=0, carried=300)
        self.assertEqual(y["net_capital_gain"], 350.0)
        y = cgt.net_capital_gain(disc=100, nondisc=50, losses=400)
        self.assertEqual((y["net_capital_gain"], y["loss_to_carry_forward"]), (0.0, 250.0))

    def test_record_updates_lots_cash_and_the_year(self):
        p, st = portfolio(), {"realized": []}
        cgt.record(p, st, cgt.sale(p, "VAS", 25, 104.55, "2026-10-21", 11, "fifo"))
        self.assertEqual(cgt.position(p, "VAS")["lots"], [{"id": "VAS-2", "date": "2026-01-10", "shares": 5.0,
                                                          "cost": 500.0}])
        self.assertAlmostEqual(p["cash"], 500 + 25 * 104.55 - 11)
        cgt.record(p, st, cgt.sale(p, "BHP", 30, 40, "2026-10-22", 11))
        self.assertIsNone(cgt.position(p, "BHP"))
        y = cgt.year(st, "FY2026-27")
        self.assertEqual(y["sales"], 2)
        self.assertEqual(y["net_capital_gain"], 50.38)
        self.assertEqual(cgt.year(st, "FY2025-26")["sales"], 0)

    def test_amit_adjustment_changes_the_cost_base(self):
        p = portfolio()
        cgt.position(p, "VAS")["lots"][0]["adjustments"] = [{"amount": -20.0}]
        lots = cgt.effective_lots(cgt.position(p, "VAS"))
        self.assertEqual(lots[0]["cost"], 1780.0)

    def test_harvest_waits_and_report(self):
        p = portfolio()
        prices = {"VAS": 104.55, "BHP": 40.0}
        h = cgt.harvest(p, prices, cgt.A.day("2026-10-21"), {"realized": []}, 100)
        self.assertEqual([c["symbol"] for c in h["candidates"]], ["BHP"])
        self.assertEqual(h["days_to_30_june"], 252)
        w = cgt.waits(p, prices, cgt.A.day("2026-10-21"), 90)
        self.assertEqual([(r["id"], r["days"]) for r in w], [("VAS-2", 82)])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            pp, st, pr = (os.path.join(d, x) for x in ("p.json", "s.json", "pr.json"))
            with open(pr, "w") as fh:
                json.dump({"prices": {"VAS": 104.55}}, fh)
            base = ["--portfolio", pp, "--state", st, "--prices", pr]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cgt.main(base + ["--date", "2025-10-20", "buy", "VAS", "20", "--price", "90"]), 0)
                self.assertEqual(cgt.main(base + ["--date", "2026-10-21", "sell", "VAS", "5", "--record"]), 0)
            with redirect_stdout(io.StringIO()) as out:
                cgt.main(base + ["year", "--fy", "FY2026-27", "--rate", "30"])
            self.assertIn("NET CAPITAL GAIN", out.getvalue())
            with redirect_stderr(io.StringIO()):
                self.assertEqual(cgt.main(base + ["sell", "CSL", "1", "--price", "1"]), 1)


class News(unittest.TestCase):
    def test_kinds(self):
        for title, want in [("Trading Halt", "halt"), ("Appendix 4E and Annual Report", "results"),
                            ("Response to ASX Price Query", "regulator"), ("FY27 Earnings Guidance Update", "guidance"),
                            ("Change of Director's Interest Notice", "director trade"),
                            ("Becoming a substantial holder from XYZ", "substantial holder"),
                            ("Placement and Share Purchase Plan", "capital"), ("Proposed issue of securities", "capital"),
                            ("Scheme Implementation Deed", "deal"), ("Impairment of goodwill", "risk"),
                            ("Quarterly Activities Report", "quarterly"), ("Dividend/Distribution - VAS", "dividend"),
                            ("Daily Fund Update", "fund"), ("Application for quotation of securities", "admin"),
                            ("Something unusual", "other")]:
            self.assertEqual(news.kind(title)["kind"], want, title)

    def test_query(self):
        q = news.gmail_query(["BHP", "CSL"], {"BHP": "BHP Group"}, ["alerts@example.com"], 3)
        self.assertEqual(q, 'newer_than:3d from:(alerts@example.com) (BHP OR "BHP Group" OR CSL)')

    def test_triage_sorts_and_dedupes(self):
        items = [{"id": "1", "title": "Daily Fund Update", "code": "VAS", "date": "2026-10-21"},
                 {"id": "2", "title": "Change of Director's Interest Notice", "code": "CSL", "date": "2026-10-20"},
                 {"id": "3", "title": "Notice of AGM", "code": "XYZ", "date": "2026-10-21", "price_sensitive": True},
                 {"id": "4", "title": "BHP Group operational review", "code": "", "date": "2026-10-19"}]
        rows, skipped = news.triage(items, ["VAS", "CSL", "BHP"], {"BHP": "BHP Group"}, {"1": "2026-10-21"})
        self.assertEqual(skipped, 1)
        self.assertEqual([r["id"] for r in rows], ["3", "2", "4"])
        self.assertEqual(rows[2]["touches"], ["BHP"])

    def test_cli_marks_seen(self):
        with tempfile.TemporaryDirectory() as d:
            items, state = os.path.join(d, "i.json"), os.path.join(d, "s.json")
            with open(items, "w") as fh:
                json.dump([{"id": "a", "title": "Trading Halt", "code": "BHP", "date": "2026-10-21"}], fh)
            args = ["--state", state, "--config", "none", "--portfolio", "none", "--targets", "none"]
            with redirect_stdout(io.StringIO()) as out:
                news.main(args + ["triage", items, "--codes", "BHP", "--mark"])
            self.assertIn("halt", out.getvalue())
            with redirect_stdout(io.StringIO()) as out:
                news.main(args + ["triage", items, "--codes", "BHP"])
            self.assertIn("Nothing new", out.getvalue())


class Analyst(unittest.TestCase):
    def setUp(self):
        self.doc = facts.read(REPORT)

    def test_check_passes_and_catches_gaps(self):
        self.assertEqual(facts.check_file(self.doc), [])
        bad = json.loads(json.dumps(self.doc))
        del bad["periods"][3]["npat"]
        bad["periods"][3]["url"] = ""
        probs = " | ".join(facts.check_file(bad))
        self.assertIn("no npat", probs)
        self.assertIn("no source page", probs)

    def test_numbers(self):
        r = facts.analyse(self.doc, price=6.50)
        m = r["metrics"]
        self.assertEqual((r["period"], r["pcp"]), ("FY2026", "FY2025"))
        self.assertEqual(m["revenue_growth_yoy"], 15.0)
        self.assertEqual(m["npat_growth_yoy"], -7.1)
        self.assertEqual(m["operating_margin"], 18.3)
        self.assertEqual(m["cash_conversion"], 69.2)
        self.assertEqual(m["net_debt"], 300.0)
        self.assertEqual(m["pe"], 26.0)
        self.assertEqual(m["dividend_yield"], 3.38)
        self.assertEqual(m["grossed_up_yield"], 4.84)
        h2 = {x["period"]: x for x in r["second_halves"]}
        self.assertEqual(h2["2H of FY2026"]["revenue"], 590.0)

    def test_ttm_from_a_half(self):
        doc = json.loads(json.dumps(self.doc))
        doc["periods"] = [p for p in doc["periods"] if p["period"] != "FY2026"]
        r = facts.analyse(doc)
        self.assertEqual(r["metrics"]["revenue_ttm"], 1080.0)     # 560 + 1000 - 480
        self.assertEqual(r["metrics"]["revenue_growth_yoy"], 16.7)

    def test_forensic_flags(self):
        levels = {t.split(":")[0]: lvl for lvl, t in facts.analyse(self.doc)["flags"]}
        text = " | ".join(t for _, t in facts.analyse(self.doc)["flags"])
        self.assertIn("receivables grew 41.7%", text)
        self.assertIn("'one-off' costs", text)
        self.assertIn("diluted", text)
        self.assertIn("CONCERNING", levels.values())

    def test_foreign_currency_needs_fx(self):
        doc = json.loads(json.dumps(self.doc))
        doc["currency"] = "USD"
        r = facts.analyse(doc, price=6.50)
        self.assertNotIn("pe", r["metrics"])
        self.assertTrue(r["no_fx"])
        doc["fx_to_aud"] = 1.5
        self.assertEqual(facts.analyse(doc, price=6.50)["metrics"]["pe"], 17.3)

    def test_metrics_file_for_the_screener(self):
        with tempfile.TemporaryDirectory() as d:
            m = os.path.join(d, "metrics.json")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(facts.main(["analyse", REPORT, "--price", "6.5", "--metrics", m,
                                             "--prices", os.path.join(d, "none.json")]), 0)
            with open(m) as fh:
                row = json.load(fh)["companies"][0]
            self.assertEqual(row["ticker"], "TST")
            self.assertEqual(row["revenue_growth_yoy"], 15.0)


if __name__ == "__main__":
    unittest.main()
