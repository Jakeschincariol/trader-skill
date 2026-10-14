import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

e13 = load("trader-copycat", "edgar13f")
DIR = os.path.join(FIX, "trader-copycat")
BRK = "1067983"

# Every CUSIP in Berkshire's Q2 2026 table and the ticker SEC / OpenFIGI give it.
TICKERS = {
    "037833100": "AAPL", "025816109": "AXP", "191216100": "KO", "02079K305": "GOOGL",
    "060505104": "BAC", "166764100": "CVX", "674599105": "OXY", "H1467J104": "CB",
    "615369105": "MCO", "02079K107": "GOOG", "500754106": "KHC", "23918K108": "DVA",
    "247361702": "DAL", "829933100": "SIRI", "92343E102": "VRSN", "501044101": "KR",
    "02005N100": "ALLY", "526057104": "LEN", "530909308": "LLYVK", "650111107": "NYT",
    "14040H105": "COF", "530909100": "LLYVA", "546347105": "LPX", "670346105": "NUE",
    "55616P104": "M", "62944T105": "NVR", "526057302": "LEN.B", "47233W109": "JEF",
    "23331A109": "DHI",
}


def offline():
    return e13.Fetcher(offline=DIR)


def quarter(combine=True, **kw):
    f = offline()
    meta, rows, filings = e13.load_quarter(f, BRK, **kw)
    index = e13.ticker_index(f)
    overrides = e13.load_overrides([e13.CLASSES])
    positions, total = e13.build(meta, rows, index, overrides, combine=combine)
    return meta, rows, positions, total


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = e13.main(args)
    return code, out.getvalue(), err.getvalue()


class ReelNumbers(unittest.TestCase):
    def test_latest_filing_is_q2_2026(self):
        meta, rows, _, total = quarter()
        self.assertEqual(meta["period"], "2026-06-30")
        self.assertEqual(meta["filed"], "2026-08-14")
        self.assertEqual(meta["accession"], "0001193125-26-352200")
        self.assertEqual(len(rows), 89)
        self.assertEqual(total, 299253556246)
        self.assertEqual(meta["total_check"], "Matches the cover page total.")

    def test_top_five_as_percent_of_the_full_13f(self):
        _, _, positions, _ = quarter()
        top = [(p["ticker"], p["pct_of_13f"]) for p in positions[:5]]
        self.assertEqual(top, [("AAPL", 22.04), ("AXP", 17.14), ("GOOGL", 12.62),
                               ("KO", 10.86), ("BAC", 9.20)])
        alphabet = positions[2]
        self.assertEqual(alphabet["tickers"], ["GOOGL", "GOOG"])
        self.assertEqual(alphabet["shares"], 105979600)
        self.assertEqual(alphabet["value"], 37764088383)

    def test_positions_and_issuers(self):
        _, _, separate, _ = quarter(combine=False)
        _, _, combined, _ = quarter(combine=True)
        self.assertEqual(len(separate), 29)
        self.assertEqual(len(combined), 26)
        # By CUSIP, Alphabet splits: GOOGL is 4th at 9.41 %, GOOG 10th at 3.21 %.
        self.assertEqual((separate[3]["ticker"], separate[3]["pct_of_13f"]), ("GOOGL", 9.41))
        self.assertEqual((separate[9]["ticker"], separate[9]["pct_of_13f"]), ("GOOG", 3.21))

    def test_copy_top_five_renormalised(self):
        _, _, positions, _ = quarter()
        weights, skipped = e13.copy_weights(positions, top=5)
        self.assertEqual([w["ticker"] for w in weights], ["AAPL", "AXP", "GOOGL", "KO", "BAC"])
        # stored with 2 decimals (budget sizing needs them), 30.7 / 23.9 / ... when rounded
        self.assertEqual([w["weight"] for w in weights], [30.67, 23.85, 17.56, 15.11, 12.80])
        self.assertEqual([round(w["weight"] + 1e-9, 1) for w in weights],
                         [30.7, 23.9, 17.6, 15.1, 12.8])
        self.assertAlmostEqual(sum(w["weight"] for w in weights), 100.0, delta=0.05)
        self.assertEqual(weights[2]["classes"], ["GOOGL", "GOOG"])
        self.assertEqual(len(skipped), 21)
        self.assertTrue(all(s["why"] == "outside the top 5" for s in skipped))

    def test_copy_cli_writes_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "trader", "targets.json")
            code, text, err = run(["copy", BRK, "--top", "5", "--offline", DIR,
                                   "--map", os.path.join(tmp, "none.json"), "--out", out])
            self.assertEqual(code, 0, err)
            with open(out) as fh:
                t = json.load(fh)
        self.assertEqual(t["as_of"], "2026-06-30")
        self.assertIn("filed 2026-08-14", t["source"])
        self.assertIn("0001193125-26-352200", t["source"])
        self.assertEqual([(w["ticker"], w["name"]) for w in t["weights"]][:3],
                         [("AAPL", "Apple"), ("AXP", "American Express"), ("GOOGL", "Alphabet")])
        self.assertEqual([w["weight"] for w in t["weights"]], [30.67, 23.85, 17.56, 15.11, 12.8])
        self.assertTrue(any("45 days" in c for c in t["caveats"]))
        self.assertIn("wrote", text)
        self.assertIn("Holdings as of 2026-06-30", text)


class Changes(unittest.TestCase):
    def test_diff_vs_the_prior_quarter(self):
        f = offline()
        meta, rows, _ = e13.load_quarter(f, BRK)
        pmeta, prows, _ = e13.load_quarter(f, BRK, before=meta["period"])
        self.assertEqual(pmeta["period"], "2026-03-31")
        index = e13.ticker_index(f)
        ov = e13.load_overrides([e13.CLASSES])
        cur, _ = e13.aggregate(rows)
        pri, ptotal = e13.aggregate(prows)
        self.assertEqual(ptotal, 263095703570)
        e13.resolve_tickers(cur, index, ov)
        e13.resolve_tickers(pri, index, ov)
        changes = e13.diff(cur, pri)
        by = {}
        for c in changes:
            by.setdefault(c["status"], []).append(c["ticker"])
        self.assertEqual(by["NEW"], ["DHI"])
        self.assertEqual(by["SOLD OUT"], ["STZ"])
        self.assertEqual(sorted(by["ADDED"]),
                         sorted(["GOOGL", "GOOG", "DAL", "LEN", "LEN.B", "M", "NYT"]))
        self.assertEqual(sorted(by["TRIMMED"]), sorted(["BAC", "KR", "COF", "NUE", "ALLY", "DVA"]))
        self.assertEqual(len(by["UNCHANGED"]), 15)
        bac = next(c for c in changes if c["ticker"] == "BAC")
        self.assertEqual(bac["change"], -30230150)
        stz = next(c for c in changes if c["ticker"] == "STZ")
        self.assertEqual(stz["value_before"], 94933500)

    def test_diff_cli_text(self):
        code, text, err = run(["diff", BRK, "--offline", DIR, "--map", "/nonexistent.json"])
        self.assertEqual(code, 0, err)
        self.assertIn("2026-06-30 vs 2026-03-31", text)
        self.assertIn("SOLD OUT (1)", text)
        self.assertIn("not in a 13F", text)


class Tickers(unittest.TestCase):
    def test_every_cusip_gets_the_right_ticker(self):
        _, _, positions, _ = quarter(combine=False)
        got = {p["cusip"]: p["ticker"] for p in positions}
        self.assertEqual(got, TICKERS)

    def test_guesses_are_flagged(self):
        _, _, positions, _ = quarter(combine=False)
        unsure = sorted(p["ticker"] for p in positions if p["ticker_check"])
        # Matched by a name prefix: the 13F shortens or decorates these names.
        self.assertEqual(unsure, ["JEF", "NYT"])

    def test_name_normalising(self):
        k = e13.name_keys
        self.assertEqual(k("BANK OF AMER CORP")[0], k("BANK OF AMERICA CORP /DE/")[0])
        self.assertEqual(k("HORTON D R INC /DE/")[1], k("D R HORTON INC")[1])
        self.assertEqual(k("SIRIUSXM HOLDINGS INC")[0], k("SIRIUS XM HOLDINGS INC.")[0])
        self.assertEqual(k("MACYS INC")[0], k("Macy's, Inc.")[0])
        self.assertEqual(k("LOUISIANA PAC CORP")[0], k("LOUISIANA-PACIFIC CORP")[0])
        self.assertEqual(e13.norm_ticker("BRK-B"), "BRK.B")
        self.assertEqual(e13.norm_ticker("LEN/B"), "LEN.B")
        self.assertEqual(e13.norm_ticker("BAC-PB"), "BAC-PB")
        self.assertEqual(e13.class_letter("COM SHS SER C"), "C")
        self.assertEqual(e13.class_letter("CAP STK CL A"), "A")
        self.assertIsNone(e13.class_letter("COM NEW"))

    def test_preferreds_and_warrants_are_not_the_stock(self):
        with open(os.path.join(DIR, "company_tickers.json")) as fh:
            index = e13.TickerIndex(e13.load_company_tickers(json.load(fh)))
        bac = e13.cik10(70858)
        self.assertEqual(index.ticker_for(bac, "COM"), ("BAC", False))
        oxy = e13.cik10(797468)
        self.assertEqual(index.ticker_for(oxy, "COM"), ("OXY", False))
        lly = e13.cik10(2078416)
        self.assertEqual(index.ticker_for(lly, "COM SER A"), ("LLYVA", True))

    def test_openfigi_answers(self):
        cusips = ["02079K305", "02079K107", "530909308", "530909100", "526057104",
                  "526057302", "084670702", "084670108", "037833100", "000000000"]
        with open(os.path.join(DIR, "openfigi-classes.json")) as fh:
            got = e13.parse_figi(cusips, json.load(fh))
        self.assertEqual(got["02079K305"], "GOOGL")
        self.assertEqual(got["526057302"], "LEN.B")
        self.assertEqual(got["084670702"], "BRK.B")
        self.assertNotIn("000000000", got)
        # the bundled share-class table agrees with OpenFIGI
        bundled = e13.load_overrides([e13.CLASSES])
        for c, t in got.items():
            if c in bundled:
                self.assertEqual(bundled[c], t, c)

    def test_figi_lookup_offline(self):
        got = e13.figi_lookup(offline(), ["650111107", "47233W109"])
        self.assertEqual(got, {"650111107": "NYT", "47233W109": "JEF"})

    def test_your_own_map_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tickers.json")
            with open(path, "w") as fh:
                json.dump({"650111107": "NYT", "47233W109": "JEF"}, fh)
            f = offline()
            meta, rows, _ = e13.load_quarter(f, BRK)
            ov = e13.load_overrides([e13.CLASSES, path])
            positions, _ = e13.build(meta, rows, e13.ticker_index(f), ov)
        self.assertFalse(any(p["ticker_check"] for p in positions))


class Filers(unittest.TestCase):
    def test_bundled_filers(self):
        hits = e13.find_bundled("buffett")
        self.assertEqual([h["cik"] for h in hits], ["0001067983"])
        burry = e13.find_bundled("michael burry")[0]
        self.assertEqual(burry["cik"], "0001649339")
        self.assertIn("2025-11-03", burry["status"])
        self.assertEqual(e13.find_bundled("1067983")[0]["name"], "Berkshire Hathaway Inc")
        self.assertEqual(e13.find_bundled("nobody at all"), [])
        for f in e13.load_filers():
            self.assertRegex(f["cik"], r"^\d{10}$")

    def test_search_every_filer(self):
        hits = e13.search_sec(offline(), "scion asset")
        self.assertIn({"cik": "0001649339", "name": "Scion Asset Management, LLC"}, hits)

    def test_filings_list_and_amendments(self):
        sub = offline().get_json(e13.SUBMISSIONS % e13.cik10(BRK))
        filings = e13.list_filings(sub)
        self.assertTrue(all(f["form"] in e13.FORMS for f in filings))
        self.assertEqual(filings[0]["period"], "2026-06-30")
        chosen, amendments = e13.pick_filing(filings, period="2025-03-31")
        self.assertEqual(chosen["form"], "13F-HR")
        self.assertEqual(chosen["accession"], "0000950123-25-005701")
        self.assertEqual([a["accession"] for a in amendments], ["0000950123-25-008361"])
        with self.assertRaises(e13.EdgarError):
            e13.pick_filing(filings, accession="0000000000-00-000000")

    def test_find_cli(self):
        code, text, _ = run(["find", "ackman"])
        self.assertEqual(code, 0)
        self.assertIn("0001336528", text)
        code, text, _ = run(["find", "zzzz"])
        self.assertEqual(code, 1)


class Rules(unittest.TestCase):
    def test_user_agent_is_required_and_never_the_placeholder(self):
        old = os.environ.pop("SEC_USER_AGENT", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = os.path.join(tmp, "config.json")
                with open(cfg, "w") as fh:
                    json.dump({"sec_user_agent": "Your Name you@example.com"}, fh)
                self.assertIsNone(e13.user_agent(None, cfg))
                self.assertIsNone(e13.user_agent(None, os.path.join(tmp, "missing.json")))
                self.assertIsNone(e13.user_agent("no email here", cfg))
                with open(cfg, "w") as fh:
                    json.dump({"sec_user_agent": "Ada Lovelace ada@example.org"}, fh)
                self.assertEqual(e13.user_agent(None, cfg), "Ada Lovelace ada@example.org")
                os.environ["SEC_USER_AGENT"] = "Grace Hopper grace@example.org"
                self.assertEqual(e13.user_agent(None, cfg), "Grace Hopper grace@example.org")
                os.environ.pop("SEC_USER_AGENT")
                code, _, err = run(["filings", BRK, "--config", os.path.join(tmp, "x.json")])
                self.assertEqual(code, 2)
                self.assertIn("User-Agent", err)
                with self.assertRaises(e13.EdgarError):
                    e13.Fetcher(None).get("https://data.sec.gov/submissions/CIK0001067983.json")
        finally:
            if old is not None:
                os.environ["SEC_USER_AGENT"] = old

    def test_options_and_bonds_are_never_copied(self):
        rows = [
            {"issuer": "APPLE INC", "title": "COM", "cusip": "037833100", "figi": "",
             "value": 600.0, "shares": 3.0, "share_type": "SH", "put_call": "", "manager": ""},
            {"issuer": "APPLE INC", "title": "CALL", "cusip": "037833100", "figi": "",
             "value": 300.0, "shares": 100.0, "share_type": "SH", "put_call": "CALL", "manager": ""},
            {"issuer": "SOME CO", "title": "NOTE 2.5% 2030", "cusip": "123456AB7", "figi": "",
             "value": 100.0, "shares": 100.0, "share_type": "PRN", "put_call": "", "manager": ""},
        ]
        positions, total = e13.aggregate(rows)
        self.assertEqual(total, 1000.0)
        positions[0]["ticker"] = positions[1]["ticker"] = "AAPL"
        positions[2]["ticker"] = None
        weights, skipped = e13.copy_weights(positions)
        self.assertEqual([(w["ticker"], w["weight"]) for w in weights], [("AAPL", 100.0)])
        whys = sorted(s["why"] for s in skipped)
        self.assertTrue(whys[0].startswith("CALL row"))
        self.assertTrue(whys[1].startswith("PRN row"))

    def test_old_filings_are_in_thousands(self):
        self.assertEqual(e13.value_multiplier("2022-11-14"), 1000)
        self.assertEqual(e13.value_multiplier("2026-08-14"), 1)

    def test_offline_never_touches_the_network(self):
        with self.assertRaises(e13.EdgarError) as ctx:
            offline().get("https://data.sec.gov/submissions/CIK0000000001.json")
        self.assertIn("offline", str(ctx.exception))

    def test_holdings_cli_says_how_old_the_data_is(self):
        code, text, err = run(["holdings", BRK, "--combine", "--top", "5", "--offline", DIR,
                               "--map", "/nonexistent.json"])
        self.assertEqual(code, 0, err)
        self.assertIn("filed 2026-08-14", text)
        self.assertIn("Matches the cover page total.", text)
        self.assertIn("up to 45 days late", text)
        self.assertIn("GOOGL+GOOG", text)
        self.assertNotIn(chr(0x2014), text)

    def test_xml_file_without_network(self):
        xml = os.path.join(DIR, "000119312526352200", "56757.xml")
        code, text, err = run(["holdings", "--xml", xml, "--tickers",
                               os.path.join(DIR, "company_tickers.json"), "--combine",
                               "--map", "/nonexistent.json", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(text)
        self.assertEqual(data["total"], 299253556246)
        self.assertEqual(data["positions"][0]["ticker"], "AAPL")


if __name__ == "__main__":
    unittest.main()
