"""ASX / CMC Invest / Australian CGT mode for trader-budget, trader-risk and
trader-rebalance, and the CMC holdings importer. Offline."""

import datetime
import filecmp
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

A = load("trader-risk", "asx")
size = load("trader-budget", "size")
check = load("trader-risk", "check")
rebalance = load("trader-rebalance", "rebalance")
cmc = load("trader-risk", "cmc_import")

ASX_FIX = os.path.join(FIX, "trader-asx")
TODAY = "2026-10-21"
PRICES = {"BHP": 44.12, "CBA": 168.30, "VAS": 104.55, "WDS": 24.07, "BGBL": 85.85}


def cfg(**over):
    with open(os.path.join(ASX_FIX, "config.json")) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def order(sym, side, qty, **kw):
    o = {"symbol": sym, "side": side, "qty": str(qty), "type": "market", "time_in_force": "day"}
    o.update(kw)
    return o


def run_check(orders, portfolio=None, c=None, tax_state=None, state=None):
    return check.check(orders, c or cfg(), portfolio, PRICES, state, TODAY, tax_state)


def text(res, i, key="reasons"):
    return " | ".join(res["results"][i][key])


class Copies(unittest.TestCase):
    def test_shared_helpers_are_identical_in_every_skill(self):
        for name in ("trader-budget", "trader-rebalance", "trader-tax-au"):
            self.assertTrue(filecmp.cmp(os.path.join(ROOT, "trader-risk", "asx.py"),
                                        os.path.join(ROOT, name, "asx.py"), shallow=False), name)
        self.assertTrue(filecmp.cmp(os.path.join(ROOT, "trader-risk", "cmc_import.py"),
                                    os.path.join(ROOT, "trader-rebalance", "cmc_import.py"), shallow=False))


class Helpers(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(A.code(" bhp.ax "), "BHP")
        self.assertEqual(A.code("ASX:CBA"), "CBA")
        self.assertEqual(A.code("vas.asx"), "VAS")
        self.assertIsNone(A.check_code("BHP"))
        self.assertIsNone(A.check_code("S32"))
        self.assertIn("asx_allow_codes", A.check_code("BGBL"))
        self.assertIsNone(A.check_code("BGBL", ["bgbl.ax"]))
        self.assertIn("not an ASX code", A.check_code("BTC/USD"))
        self.assertIn("not an ASX code", A.check_code("AB"))

    def test_price_steps(self):
        self.assertEqual(A.tick(0.05), 0.001)
        self.assertEqual(A.tick(1.50), 0.005)
        self.assertEqual(A.tick(2.00), 0.01)
        self.assertTrue(A.on_tick(1.235))
        self.assertFalse(A.on_tick(1.233))
        self.assertTrue(A.on_tick(24.07))
        self.assertFalse(A.on_tick(24.075))
        self.assertEqual(A.round_tick(24.075), 24.07)
        self.assertEqual(A.round_tick(1.233, down=False), 1.235)

    def test_cmc_brokerage(self):
        self.assertEqual(A.brokerage(1000, "buy"), 0.0)          # first buy up to $1,000 is free
        self.assertEqual(A.brokerage(1000.01, "buy"), 11.0)
        self.assertEqual(A.brokerage(800, "buy", free_used=True), 11.0)
        self.assertEqual(A.brokerage(800, "sell"), 11.0)          # sells are always charged
        self.assertEqual(A.brokerage(20000, "buy"), 22.0)         # 0.11 % beats $11
        self.assertEqual(A.brokerage(20000, "buy", {"brokerage": {"pct": 0.10}}), 20.0)

    def test_cgt_discount_counts_neither_end_day(self):
        self.assertEqual(A.discount_from("2025-10-20"), datetime.date(2026, 10, 21))
        self.assertFalse(A.discount_ok("2025-10-20", "2026-10-20"))
        self.assertTrue(A.discount_ok("2025-10-20", "2026-10-21"))
        self.assertEqual(A.discount_from("2024-02-29"), datetime.date(2025, 3, 2))

    def test_financial_year(self):
        self.assertEqual(A.fin_year("2026-06-30"), "FY2025-26")
        self.assertEqual(A.fin_year("2026-07-01"), "FY2026-27")

    def test_sell_preview_picks_the_lowest_taxable_parcels(self):
        lots = [{"id": "old", "date": "2024-01-02", "shares": 10, "cost": 500},     # $50, discounted
                {"id": "new", "date": "2026-09-01", "shares": 10, "cost": 950},     # $95, not discounted
                {"id": "loss", "date": "2026-05-01", "shares": 5, "cost": 600}]     # $120, a loss
        tx = A.sell_preview(lots, 12, 100.0, TODAY)
        self.assertEqual([l["id"] for l in tx["lots"]], ["loss", "new"])
        self.assertEqual(tx["losses"], -100.0)
        self.assertEqual(tx["undiscounted_gain"], 35.0)
        self.assertEqual(tx["taxable"], -65.0)
        self.assertEqual(tx["fin_year"], "FY2026-27")

    def test_sell_preview_points_at_a_lot_about_to_qualify(self):
        lots = [{"id": "L1", "date": "2026-01-10", "shares": 10, "cost": 1000}]
        tx = A.sell_preview(lots, 10, 104.55, TODAY, fee=11)
        self.assertEqual(tx["gain"], 34.5)
        self.assertEqual(tx["waits"], [{"id": "L1", "date": "2027-01-11", "days": 82, "taxable_saved": 17.25}])


class Budget(unittest.TestCase):
    def test_whole_shares_parcel_and_brokerage_inside_the_budget(self):
        rows = size.parse_weights({"BHP.AX": 40, "CBA": 30, "VAS": 20, "WDS": 8, "XYZW": 2})
        r = size.size(rows, cfg(), prices=dict(PRICES))
        got = {p["ticker"]: p["shares"] for p in r["positions"]}
        self.assertEqual(got, {"BHP": 45, "CBA": 9, "VAS": 9})
        dropped = {d["ticker"]: d["reason"] for d in r["dropped"]}
        self.assertIn("asx_allow_codes", dropped["XYZW"])
        self.assertIn("minimum order", dropped["WDS"])
        self.assertEqual(r["brokerage"], 22.0)
        self.assertLessEqual(r["invested"] + r["brokerage"], r["investable"] + 1e-6)
        self.assertGreaterEqual(r["idle"], 0)
        orders = size.orders_from(r)
        self.assertEqual(orders[0], {"symbol": "BHP", "side": "buy", "qty": "45", "type": "market",
                                     "time_in_force": "day", "exchange": "ASX", "currency": "AUD",
                                     "est_brokerage": 11.0, "why": "44.44% of the stock sleeve"})
        self.assertNotIn("notional", orders[0])

    def test_min_order_is_raised_to_the_marketable_parcel(self):
        r = size.size(size.parse_weights({"VAS": 100}), cfg(min_order=5, max_position_pct=100), budget=600, prices=dict(PRICES))
        self.assertEqual(r["min_order"], 500)
        self.assertTrue(any("marketable parcel" in n for n in r["notes"]))

    def test_needs_real_prices(self):
        with self.assertRaises(size.SizeError) as e:
            size.size(size.parse_weights({"NAB": 100}), cfg(), prices={})
        self.assertIn("Never guess a price", str(e.exception))

    def test_free_tier_note(self):
        r = size.size(size.parse_weights({"VAS": 100}), cfg(max_position_pct=100), budget=1200,
                      prices=dict(PRICES))
        self.assertEqual(r["positions"][0]["shares"], 10)          # A$1,045.50, charged A$11
        self.assertTrue(any("would be free" in n for n in r["notes"]))

    def test_us_mode_is_untouched(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            us = json.load(fh)
        r = size.size(size.parse_weights({"AAPL": 50, "KO": 50}), us)
        self.assertNotIn("market", r)
        self.assertIn("notional", size.orders_from(r)[0])


class Risk(unittest.TestCase):
    def test_asx_order_rules(self):
        res = run_check([
            order("BHP.AX", "buy", 45),
            {"symbol": "CBA", "side": "buy", "notional": "600", "type": "market", "time_in_force": "day"},
            order("WDS", "buy", 30, type="limit", limit_price="24.075", time_in_force="gtc"),
            order("VAS", "buy", 2.5),
            order("CBA", "buy", 3, time_in_force="gtd"),
        ])
        self.assertEqual([r["verdict"] for r in res["results"]], ["PASS", "BLOCK", "BLOCK", "BLOCK", "BLOCK"])
        self.assertEqual(res["results"][0]["symbol"], "BHP")
        self.assertIn("not a dollar amount", text(res, 1))
        self.assertIn("price step", text(res, 2))
        self.assertIn("not a whole number", text(res, 3))
        self.assertIn("expire_date", text(res, 4))

    def test_marketable_parcel(self):
        res = run_check([order("WDS", "buy", 10)], c=cfg(min_order=0))
        self.assertIn("under the ASX $500 minimum holding", text(res, 0))
        port = {"cash": 3000, "positions": [{"ticker": "WDS", "shares": 30}]}   # A$722 already held
        res = run_check([order("WDS", "buy", 10)], portfolio=port, c=cfg(min_order=0))
        self.assertEqual(res["results"][0]["verdict"], "PASS")

    def test_brokerage_counts_against_cash_and_the_free_tier_is_once_a_day(self):
        port = {"cash": 1000, "positions": []}
        res = run_check([order("VAS", "buy", 9)], portfolio=port, c=cfg(cash_buffer_pct=0, max_position_pct=100))
        self.assertEqual(res["results"][0]["est_brokerage"], 0.0)          # A$940.95, free
        self.assertAlmostEqual(res["cash_after"], 59.05)
        state = {"orders": [{"symbol": "VAS", "side": "buy", "status": "placed", "date": TODAY}]}
        res = run_check([order("VAS", "buy", 9)], portfolio=port, c=cfg(cash_buffer_pct=0, max_position_pct=100, max_orders_per_day=0),
                        state=state)
        self.assertEqual(res["results"][0]["est_brokerage"], 11.0)

    def test_foreign_holding_is_valued_but_not_traded(self):
        port = {"cash": 2000, "positions": [{"ticker": "BTC", "shares": 0.01, "currency": "USD", "value_aud": 840}]}
        res = run_check([order("BTC", "sell", 1)], portfolio=port)
        self.assertIn("not in AUD", text(res, 0))

    def test_codes_needing_the_allow_list(self):
        res = run_check([order("BHPZZ9", "buy", 20)], c=cfg(min_order=0))
        self.assertIn("asx_allow_codes", text(res, 0))
        res = run_check([order("BGBL", "buy", 10)])
        self.assertEqual(res["results"][0]["verdict"], "PASS")

    def test_sale_warnings_cgt_and_parcel(self):
        port = {"cash": 100, "positions": [{"ticker": "VAS", "shares": 12, "lots": [
            {"id": "VAS-1", "date": "2026-01-10", "shares": 12, "cost": 1200}]}]}
        res = run_check([order("VAS", "sell", 8)], portfolio=port)
        self.assertEqual(res["results"][0]["verdict"], "PASS")              # small sells are allowed
        w = text(res, 0, "warnings")
        self.assertIn("CGT estimate (FY2026-27)", w)
        self.assertIn("becomes discount-eligible on 2027-01-11", w)
        self.assertIn("under the ASX $500 marketable parcel", w)

    def test_sale_without_lots_says_so(self):
        port = {"cash": 100, "positions": [{"ticker": "VAS", "shares": 12}]}
        res = run_check([order("VAS", "sell", 6)], portfolio=port)
        self.assertIn("no cost base", text(res, 0, "warnings"))

    def test_wash_sale_warning(self):
        tax = {"realized": [{"date": "2026-10-10", "symbol": "BHP", "gain": -120.0}]}
        res = run_check([order("BHP", "buy", 20)], tax_state=tax)
        self.assertIn("TR 2008/1", text(res, 0, "warnings"))

    def test_trade_risk_to_the_stop(self):
        port = {"cash": 10000, "positions": []}
        o = order("CBA", "buy", 10, type="limit", limit_price="168.30", stop_loss="150.00", take_profit="205.00")
        res = run_check([o], portfolio=port, c=cfg(max_risk_per_trade_pct=1))
        self.assertIn("trade risk", text(res, 0))                            # A$183 + fees > A$100
        res = run_check([o], portfolio=port, c=cfg(max_risk_per_trade_pct=3))
        self.assertEqual(res["results"][0]["verdict"], "PASS")
        self.assertIn("reward to risk 2.0 : 1", text(res, 0, "warnings"))
        res = run_check([order("CBA", "buy", 4)], portfolio=port, c=cfg(require_stop=True))
        self.assertIn("require_stop", text(res, 0))
        bad = order("CBA", "buy", 4, stop_loss="170.00")
        self.assertIn("not below the entry", text(run_check([bad], portfolio=port), 0))

    def test_buy_that_needs_unsettled_sale_money(self):
        port = {"cash": 200, "positions": [{"ticker": "VAS", "shares": 20}]}
        res = run_check([order("VAS", "sell", 10), order("BHP", "buy", 12)], portfolio=port,
                        c=cfg(cash_buffer_pct=0))
        self.assertEqual([r["verdict"] for r in res["results"]], ["PASS", "PASS"])
        self.assertIn("T+2", text(res, 1, "warnings"))

    def test_report_is_in_aud_and_says_who_places_it(self):
        out = check.report(run_check([order("BHP", "buy", 45)]))
        self.assertIn("A$", out)
        self.assertIn("you place it yourself in CMC", out)


class Rebalance(unittest.TestCase):
    PORT = {"cash": 300, "positions": [
        {"ticker": "VAS", "shares": 30, "lots": [{"id": "VAS-1", "date": "2025-10-20", "shares": 20, "cost": 1800},
                                                 {"id": "VAS-2", "date": "2026-01-10", "shares": 10, "cost": 1000}]},
        {"ticker": "BHP.AX", "shares": 20},
        {"ticker": "BTC", "shares": 0.01, "currency": "USD", "value_aud": 840}]}

    def plan(self, **over):
        w = rebalance.parse_targets({"VAS": 50, "BHP": 30, "CBA": 20}, asx=True)
        return rebalance.plan(json.loads(json.dumps(self.PORT)), dict(PRICES), w, cfg(**over), today=TODAY)

    def test_whole_share_sale_with_cgt_and_skipped_small_buys(self):
        res = self.plan()
        self.assertEqual(len(res["orders"]), 1)
        o = res["orders"][0]
        self.assertEqual((o["symbol"], o["side"], o["qty"], o["est_brokerage"]), ("VAS", "sell", "10", 11.0))
        self.assertEqual(o["sell_parcels"], [{"id": "VAS-2", "shares": 10.0}])
        self.assertEqual(res["taxes"]["VAS"]["taxable"], 34.5)
        self.assertEqual(res["taxes"]["VAS"]["waits"][0]["date"], "2027-01-11")
        self.assertAlmostEqual(res["cash_after"], 300 + 1045.5 - 11)
        btc = [r for r in res["rows"] if r["ticker"] == "BTC"][0]
        self.assertEqual(btc["action"], "not AUD, left alone")
        self.assertTrue(any("does not reach" in n for n in res["notes"]))

    def test_fee_limit_skips_trades_not_worth_their_brokerage(self):
        res = self.plan(max_fee_pct=1.0)
        self.assertEqual(res["orders"], [])
        self.assertTrue(any("fee limit" in n for n in res["notes"]))
        self.assertIn("every trade was skipped", rebalance.report(res))

    def test_buys_respect_the_parcel_and_brokerage(self):
        port = json.loads(json.dumps(self.PORT))
        port["cash"] = 4000
        w = rebalance.parse_targets({"VAS": 50, "BHP": 30, "CBA": 20}, asx=True)
        res = rebalance.plan(port, dict(PRICES), w, cfg(), today=TODAY)
        buys = {o["symbol"]: o for o in res["orders"] if o["side"] == "buy"}
        self.assertIn("CBA", buys)
        for o in buys.values():
            self.assertEqual(o["qty"], str(int(o["qty"])))
            self.assertGreaterEqual(int(o["qty"]) * PRICES[o["symbol"]], 500)
        self.assertGreaterEqual(res["cash_after"], res["cash_buffer"] - 0.01)


class Importer(unittest.TestCase):
    CSV = os.path.join(ASX_FIX, "Holdings202607010153.csv")

    def test_parse_and_build(self):
        with open(self.CSV, encoding="utf-8") as fh:
            rows = cmc.parse(fh.read())
        self.assertEqual([r["code"] for r in rows], ["A200", "BGBL", "BTC", "ETPMPM"])
        old = {"positions": [{"ticker": "A200", "shares": 10, "lots": [{"id": "A-1", "date": "2025-01-02",
                                                                       "shares": 10, "cost": 1300}]},
                             {"ticker": "BGBL", "shares": 5, "lots": [{"id": "B-1", "date": "2025-01-02",
                                                                      "shares": 5, "cost": 400}]}]}
        port, prices, warns = cmc.build(rows, 1500, old)
        by = {p["ticker"]: p for p in port["positions"]}
        self.assertIn("lots", by["A200"])                   # share count still matches
        self.assertNotIn("lots", by["BGBL"])                # 5 saved vs 20 held: never guessed
        self.assertEqual(by["BTC"]["currency"], "USD")
        self.assertAlmostEqual(prices["BTC"], 84000.0)
        self.assertEqual(prices["A200"], 147.42)
        self.assertTrue(any("BGBL" in w for w in warns))
        self.assertNotIn("EXAMPLE INVESTOR", json.dumps(port))

    def test_only_exclude_and_as_of(self):
        with open(self.CSV, encoding="utf-8") as fh:
            rows = cmc.parse(fh.read())
        port, _, _ = cmc.build(rows, 0, None, only=["A200", "BGBL"])
        self.assertEqual([p["ticker"] for p in port["positions"]], ["A200", "BGBL"])
        port, _, _ = cmc.build(rows, 0, None, exclude=["BTC"])
        self.assertNotIn("BTC", [p["ticker"] for p in port["positions"]])
        self.assertEqual(cmc.as_of_from_name(self.CSV), "2026-07-01T01:53")

    def test_cli_writes_both_files(self):
        with tempfile.TemporaryDirectory() as d:
            pp, pr = os.path.join(d, "portfolio.json"), os.path.join(d, "prices.json")
            with redirect_stdout(io.StringIO()) as out:
                rc = cmc.main([self.CSV, "--cash", "1000", "--portfolio", pp, "--prices", pr])
            self.assertEqual(rc, 0)
            self.assertIn("days old", out.getvalue())
            with open(pr) as fh:
                self.assertEqual(json.load(fh)["prices"]["ETPMPM"], 361.14)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(cmc.main([self.CSV, "--portfolio", os.path.join(d, "x.json")]), 1)


if __name__ == "__main__":
    unittest.main()
