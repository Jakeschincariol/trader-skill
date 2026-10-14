import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

alerts = load("trader-alerts", "alerts")

QDIR = os.path.join(FIX, "trader-alerts")
DT = datetime.datetime

# NYSE holidays as published at https://www.nyse.com/markets/hours-calendars (read 2026-10-13)
NYSE_2026 = ["2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
             "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"]
NYSE_2027 = ["2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
             "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"]
EARLY = ["2026-11-27", "2026-12-24", "2027-11-26"]


def ny(text):
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


class Calendar(unittest.TestCase):
    def test_holidays_match_the_published_list(self):
        self.assertEqual(sorted(alerts.NYSE_HOLIDAYS), NYSE_2026 + NYSE_2027)
        self.assertEqual(sorted(alerts.NYSE_EARLY_CLOSE), EARLY)
        for d in list(alerts.NYSE_HOLIDAYS) + list(alerts.NYSE_EARLY_CLOSE):
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)

    def test_daylight_saving_switches(self):
        self.assertEqual(alerts.eastern_offset(DT(2026, 3, 8, 6, 59)), -5)
        self.assertEqual(alerts.eastern_offset(DT(2026, 3, 8, 7, 0)), -4)
        self.assertEqual(alerts.eastern_offset(DT(2026, 11, 1, 5, 59)), -4)
        self.assertEqual(alerts.eastern_offset(DT(2026, 11, 1, 6, 0)), -5)
        self.assertEqual(alerts.eastern_offset(DT(2027, 3, 14, 7, 0)), -4)

    def test_parse_now(self):
        self.assertEqual(ny("2026-10-13T09:30"), DT(2026, 10, 13, 13, 30))
        self.assertEqual(ny("2026-12-01T09:30"), DT(2026, 12, 1, 14, 30))
        self.assertEqual(ny("2026-10-13T20:00:01Z"), DT(2026, 10, 13, 20, 0, 1))
        with self.assertRaises(alerts.AlertError):
            ny("tomorrow")

    def test_market_status(self):
        st = alerts.market_status(ny("2026-10-13T10:00"))
        self.assertTrue(st["open"])
        self.assertFalse(alerts.market_status(ny("2026-10-13T09:29"))["open"])
        self.assertEqual(alerts.market_status(ny("2026-10-13T09:29"))["next_open"], "2026-10-13 09:30")
        after = alerts.market_status(ny("2026-10-13T16:00"))
        self.assertFalse(after["open"])
        self.assertEqual(after["next_open"], "2026-10-14 09:30")
        july = alerts.market_status(ny("2026-07-03T11:00"))
        self.assertFalse(july["open"])
        self.assertIn("Independence Day", july["reason"])
        self.assertEqual(july["next_open"], "2026-07-06 09:30")
        self.assertTrue(alerts.market_status(ny("2026-11-27T12:59"))["open"])
        self.assertFalse(alerts.market_status(ny("2026-11-27T13:00"))["open"])
        xmas = alerts.market_status(ny("2027-12-24T10:00"))
        self.assertFalse(xmas["open"])
        self.assertEqual(xmas["next_open"], "2027-12-27 09:30")
        self.assertTrue(alerts.market_status(ny("2027-12-31T10:00"))["open"])
        self.assertEqual(alerts.market_status(ny("2026-10-17T10:00"))["reason"], "weekend")
        self.assertFalse(alerts.market_status(ny("2028-03-01T10:00"))["calendar_known"])


class Quotes(unittest.TestCase):
    def test_yahoo_fixture(self):
        with open(os.path.join(QDIR, "yahoo-AAPL.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "AAPL")
        self.assertEqual(q["price"], 341.27)
        self.assertEqual(q["prev_close"], 340.97)
        self.assertEqual(q["time"], "2026-10-13T20:00:01Z")

    def test_stooq_fixture(self):
        with open(os.path.join(QDIR, "stooq-AAPL.csv")) as fh:
            q = alerts.parse_stooq(fh.read(), "AAPL")
        self.assertEqual(q["price"], 341.27)
        self.assertEqual(q["open"], 344.67)
        with open(os.path.join(QDIR, "stooq-ZZZZ.csv")) as fh:
            with self.assertRaises(alerts.AlertError):
                alerts.parse_stooq(fh.read(), "ZZZZ")

    def test_urls(self):
        self.assertEqual(alerts.stooq_url("BRK.B"), "https://stooq.com/q/l/?s=brk-b.us&f=sd2t2ohlcv&h&e=csv")
        self.assertIn("/chart/BRK-B?", alerts.yahoo_url("brk.b"))

    def test_offline_quotes_and_errors(self):
        q, errors = alerts.get_quotes(["AAPL", "KO", "ZZZZ", "NOPE"], "yahoo", QDIR)
        self.assertEqual(sorted(q), ["AAPL", "KO"])
        self.assertEqual(q["KO"]["price"], 90.11)
        self.assertEqual(len(errors), 2)
        self.assertIn("No data found", errors[0])


class Rules(unittest.TestCase):
    def setUp(self):
        self.q, _ = alerts.get_quotes(["AAPL", "BAC", "KO"], "yahoo", QDIR)
        self.open = alerts.market_status(ny("2026-10-13T10:00"))
        self.now = ny("2026-10-13T10:00")

    def test_a_target_rings_once(self):
        rules = [{"id": "aapl-340", "symbol": "AAPL", "type": "above", "price": 340}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(len(rings), 1)
        self.assertIn("AAPL is $341.27", rings[0]["message"])
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])

    def test_stop_and_rearm(self):
        rules = [{"id": "bac-stop", "symbol": "BAC", "type": "stop", "price": 54, "rearm": True}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertIn("your stop $54", rings[0]["message"])
        self.q["BAC"]["price"] = 55.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("bac-stop", st["rung"])
        self.q["BAC"]["price"] = 53.5
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(len(rings), 1)

    def test_pct_move_from_previous_close(self):
        big = [{"id": "bac-3", "symbol": "BAC", "type": "pct_move", "pct": 3}]
        self.assertEqual(alerts.evaluate(big, self.q, self.open, {}, self.now)[0], [])
        small = [{"id": "bac-1", "symbol": "BAC", "type": "pct_move", "pct": 1, "direction": "down"}]
        rings, st = alerts.evaluate(small, self.q, self.open, {}, self.now)
        self.assertIn("-1.14%", rings[0]["message"])
        self.assertEqual(alerts.evaluate(small, self.q, self.open, st, self.now)[0], [])
        up = [{"id": "bac-up", "symbol": "BAC", "type": "pct_move", "pct": 1, "direction": "up"}]
        self.assertEqual(alerts.evaluate(up, self.q, self.open, {}, self.now)[0], [])

    def test_opening_bell_once_per_trading_day(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        closed = alerts.market_status(ny("2026-10-13T08:00"))
        self.assertEqual(alerts.evaluate(rules, {}, closed, {}, ny("2026-10-13T08:00"))[0], [])
        rings, st = alerts.evaluate(rules, {}, self.open, {}, self.now)
        self.assertIn("Opening bell", rings[0]["message"])
        self.assertEqual(alerts.evaluate(rules, {}, self.open, st, ny("2026-10-13T11:00"))[0], [])
        nxt = alerts.market_status(ny("2026-10-14T09:31"))
        self.assertEqual(len(alerts.evaluate(rules, {}, nxt, st, ny("2026-10-14T09:31"))[0]), 1)

    def test_bad_rules(self):
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "moon"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "AAPL"}], {}, self.open, {}, self.now)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def check(self, now):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", QDIR, "--now", now])

    def test_the_reel_portfolio_evening_and_morning(self):
        for args in (["--opening-bell"], ["--symbol", "AAPL", "--above", "350"], ["--symbol", "BAC", "--stop", "50"]):
            code, _, _ = run(["add", "--rules", self.rules] + args)
            self.assertEqual(code, 0)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "AAPL", "--above", "350"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

        code, text, _ = self.check("2026-10-13T20:00")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"AAPL": 341.27, "BAC": 53.96})

        code, text, _ = self.check("2026-10-14T09:31")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        self.assertIn("not an order", text)
        code, text, _ = self.check("2026-10-14T09:45")
        self.assertEqual(code, 1)

        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_quote_write_merges(self):
        code, text, _ = run(["quote", "AAPL", "KO", "--offline", QDIR, "--write", self.prices,
                             "--now", "2026-10-13T20:00:01Z"])
        self.assertEqual(code, 0)
        self.assertIn("AAPL   $341.27", text)
        run(["quote", "BAC", "--offline", QDIR, "--write", self.prices])
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc["prices"]), ["AAPL", "BAC", "KO"])
        self.assertEqual(doc["detail"]["KO"]["prev_close"], 89.79)

    def test_quote_holdings(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 0, "positions": [{"ticker": "KO", "shares": 1}]}, fh)
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--targets",
                             os.path.join(ROOT, "trader-budget", "targets.example.json"), "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["AAPL", "AXP", "BAC", "GOOGL", "KO"])

    def test_clock_exit_codes(self):
        self.assertEqual(run(["clock", "--now", "2026-10-13T10:00"])[0], 0)
        code, text, _ = run(["clock", "--now", "2026-12-25T10:00"])
        self.assertEqual(code, 1)
        self.assertIn("Christmas Day", text)

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", QDIR])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)


if __name__ == "__main__":
    unittest.main()
