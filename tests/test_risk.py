import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

check = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-reel.json")
TODAY = "2026-10-13"
PRICES = {"AAPL": 341.27, "AXP": 309.84, "GOOGL": 353.78, "KO": 90.11, "BAC": 53.96}


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def reel_orders():
    with open(ORDERS_PATH) as fh:
        return json.load(fh)["orders"]


def buy(sym, usd):
    return {"symbol": sym, "side": "buy", "notional": str(usd), "type": "market", "time_in_force": "day"}


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i):
    return " | ".join(res["results"][i]["reasons"])


class TheReel(unittest.TestCase):
    def test_the_five_buys_pass_on_a_fresh_5000_paper_account(self):
        res = check.check(reel_orders(), cfg(), today=TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5)
        self.assertEqual(res["cash_after"], 500)
        self.assertIs(res["paper"], True)
        self.assertEqual(res["results"][0]["value_usd"], 1250)
        self.assertEqual(res["results"][0]["amount"], "$1,250")

    def test_a_sixth_buy_is_blocked_three_ways(self):
        res = check.check(reel_orders() + [buy("AAPL", 100)], cfg(), today=TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5 + ["BLOCK"])
        why = reasons(res, 5)
        self.assertIn("cash:", why)
        self.assertIn("position:", why)
        self.assertIn("duplicate:", why)

    def test_fingerprint_is_shared_with_the_broker(self):
        o = reel_orders()[0]
        self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
        self.assertEqual(check.fingerprint(dict(o, why="anything")), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, notional="1251")), check.fingerprint(o))


class Limits(unittest.TestCase):
    def one(self, order, **over):
        res = check.check([order], cfg(**over), prices=PRICES, today=TODAY)
        return res["results"][0]

    def test_max_and_min_order(self):
        self.assertIn("over your $2,000 max order", " ".join(self.one(buy("KO", 2500), max_position_pct=100)["reasons"]))
        self.assertIn("under your $5 minimum", " ".join(self.one(buy("KO", 2))["reasons"]))

    def test_blocked_list(self):
        r = self.one(buy("KO", 100), blocked=["KO"])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("blocked list", r["reasons"][0])

    def test_options_off_unless_allowed(self):
        opt = {"symbol": "BE270617C00100000", "side": "buy", "qty": "1", "type": "limit",
               "limit_price": "12.50", "time_in_force": "day"}
        r = self.one(opt)
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("option", " ".join(r["reasons"]))
        r = self.one(opt, allow_options=True)
        self.assertEqual(r["verdict"], "PASS")
        self.assertTrue(r["warnings"])

    def test_crypto_and_junk_symbols(self):
        crypto = {"symbol": "BTC/USD", "side": "buy", "notional": "50", "type": "market", "time_in_force": "day"}
        self.assertIn("stocks and ETFs only", " ".join(self.one(crypto)["reasons"]))

    def test_format_rules(self):
        both = dict(buy("KO", 100), qty="1")
        self.assertIn("exactly one of qty or notional", " ".join(self.one(both)["reasons"]))
        lim = dict(buy("KO", 100), type="limit", limit_price="90")
        self.assertIn("market order with time_in_force day", " ".join(self.one(lim)["reasons"]))
        nolimit = {"symbol": "KO", "side": "buy", "qty": "1", "type": "limit", "time_in_force": "day"}
        self.assertIn("needs a limit_price", " ".join(self.one(nolimit)["reasons"]))
        bad = {"symbol": "KO", "side": "hold", "notional": "100"}
        self.assertIn("side must be buy or sell", " ".join(self.one(bad)["reasons"]))

    def test_numbers_should_be_strings(self):
        r = self.one({"symbol": "KO", "side": "buy", "notional": 100, "type": "market", "time_in_force": "day"})
        self.assertEqual(r["verdict"], "PASS")
        self.assertIn("as a string", r["warnings"][0])

    def test_share_orders_need_a_price(self):
        q = {"symbol": "ZZZZ", "side": "buy", "qty": "2", "type": "market", "time_in_force": "day"}
        self.assertIn("no price for ZZZZ", " ".join(self.one(q)["reasons"]))
        q["symbol"] = "KO"
        r = self.one(q)
        self.assertEqual(r["verdict"], "PASS")
        self.assertAlmostEqual(r["value_usd"], 180.22)

    def test_margin(self):
        r = check.check([buy("KO", 1500)], cfg(cash_buffer_pct=0, max_position_pct=100),
                        portfolio={"cash": 1000, "positions": []}, today=TODAY)["results"][0]
        self.assertIn("margin", " ".join(r["reasons"]))


class Account(unittest.TestCase):
    PF = {"cash": 500.0, "positions": [{"ticker": "KO", "shares": 7.8571,
                                         "lots": [{"date": "2026-10-13", "shares": 7.8571, "cost": 708.0}]}]}

    def test_short_sales_are_blocked(self):
        sell = {"symbol": "KO", "side": "sell", "qty": "10", "type": "market", "time_in_force": "day"}
        r = check.check([sell], cfg(), portfolio=self.PF, prices=PRICES, today=TODAY)["results"][0]
        self.assertIn("short:", " ".join(r["reasons"]))
        sell["qty"] = "3"
        r = check.check([sell], cfg(), portfolio=self.PF, prices=PRICES, today=TODAY)["results"][0]
        self.assertEqual(r["verdict"], "PASS")

    def test_a_sell_frees_cash_for_a_later_buy(self):
        orders = [{"symbol": "KO", "side": "sell", "qty": "5", "type": "market", "time_in_force": "day"},
                  buy("BAC", 450)]
        res = check.check(orders, cfg(max_position_pct=100), portfolio=self.PF, prices=PRICES, today=TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        res = check.check([buy("BAC", 450)], cfg(max_position_pct=100), portfolio=self.PF, prices=PRICES, today=TODAY)
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_daily_limit_and_already_placed_today(self):
        placed = {"orders": [{"symbol": "T%d" % i, "side": "buy", "status": "placed", "at": TODAY + "T10:00:00"}
                             for i in range(10)]}
        res = check.check([buy("KO", 100)], cfg(), state=placed, today=TODAY)
        self.assertIn("daily limit", reasons(res, 0))
        res = check.check([buy("KO", 100)], cfg(), state=placed, today="2026-10-14")
        self.assertEqual(verdicts(res), ["PASS"])
        ko = {"orders": [{"symbol": "KO", "side": "buy", "status": "placed", "at": TODAY + "T10:00:00"}]}
        res = check.check([buy("KO", 100)], cfg(), state=ko, today=TODAY)
        self.assertIn("already placed today", reasons(res, 0))
        declined = {"orders": [{"symbol": "KO", "side": "buy", "status": "declined", "at": TODAY + "T10:00:00"}]}
        self.assertEqual(verdicts(check.check([buy("KO", 100)], cfg(), state=declined, today=TODAY)), ["PASS"])

    def test_live_account_warns(self):
        res = check.check([buy("KO", 100)], cfg(paper=False), today=TODAY)
        self.assertIn("LIVE", res["warnings"][0])


class Cli(unittest.TestCase):
    def test_writes_verdicts_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", ORDERS_PATH, "--config", CFG_PATH, "--portfolio", os.path.join(d, "none"),
                                   "--prices", os.path.join(d, "none"), "--state", os.path.join(d, "none"),
                                   "--today", TODAY, "--out", out])
            self.assertEqual(code, 0)
            self.assertIn("5 passed, 0 blocked", buf.getvalue())
            self.assertIn("not financial advice", buf.getvalue())
            with open(out) as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertEqual(len(rec["results"]), 5)
            self.assertTrue(all(len(r["fingerprint"]) == 16 for r in rec["results"]))

            bad = os.path.join(d, "orders.json")
            with open(bad, "w") as fh:
                json.dump({"orders": reel_orders() + [buy("AAPL", 100)]}, fh)
            with redirect_stdout(io.StringIO()):
                code = check.main(["--orders", bad, "--config", CFG_PATH, "--portfolio", os.path.join(d, "none"),
                                   "--prices", os.path.join(d, "none"), "--state", os.path.join(d, "none"),
                                   "--today", TODAY, "--no-write"])
            self.assertEqual(code, 1)

    def test_missing_orders_file(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", "/nonexistent.json", "--no-write"]), 1)
        self.assertIn("no orders file", err.getvalue())


if __name__ == "__main__":
    unittest.main()
