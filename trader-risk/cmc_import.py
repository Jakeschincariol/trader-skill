#!/usr/bin/env python3
"""Turn a CMC Invest holdings export into trader/portfolio.json and
trader/prices.json. Standard library only, no network.

The same file ships in trader-risk and trader-rebalance (each skill is
uploaded on its own); tests/test_asx.py checks the copies are identical.

    python3 cmc_import.py Holdings202607010153.csv --cash 1200
    python3 cmc_import.py Holdings.csv --cash 1200 --only A200,BGBL,NDQ
    python3 cmc_import.py Holdings.csv --cash 1200 --exclude BTC --dry-run

What it reads (CMC's Holdings CSV columns): Code, Last, Currency, FX Rate,
Holdings, Value AUD. Account number and name are NOT copied anywhere.

What it writes:
  trader/portfolio.json   {"cash", "positions": [{"ticker", "shares",
                          "currency", "value_aud", "lots"}]}
  trader/prices.json      {"as_of", "source", "prices": {code: AUD price}}

The export has no cash balance and no cost base. Cash comes from --cash (the
"available to trade" figure in CMC). Lots (date, shares, total cost including
brokerage) are kept from an existing trader/portfolio.json when the share
count still matches; otherwise they are dropped with a warning, because a
cost base must never be guessed. Holdings in another currency (crypto,
overseas shares) are imported with their AUD value so they count in the
account, and the ASX skills never trade them.
"""

import argparse
import csv
import datetime
import io
import json
import os
import re
import sys


class ImportErrorCMC(Exception):
    pass


def _f(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def as_of_from_name(path):
    """Holdings202607010153.csv -> 2026-07-01T01:53."""
    m = re.search(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})", os.path.basename(path or ""))
    if not m:
        return None
    try:
        return datetime.datetime(*[int(g) for g in m.groups()]).isoformat(timespec="minutes")
    except ValueError:
        return None


def parse(text):
    """Rows of the CMC holdings CSV -> [{code, shares, last, currency, value_aud}]."""
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "Code" not in [h.strip() for h in reader.fieldnames]:
        raise ImportErrorCMC("this does not look like a CMC holdings export (no \"Code\" column)")
    out = []
    for i, raw in enumerate(reader, 2):
        r = {str(k).strip(): (v or "").strip() for k, v in raw.items() if k is not None}
        code = r.get("Code", "").upper()
        if not code:
            continue
        shares = _f(r.get("Holdings"))
        last = _f(r.get("Last"))
        value = _f(r.get("Value AUD"))
        cur = (r.get("Currency") or "AUD").upper()
        if shares is None or shares <= 0:
            continue
        if value is None and last is not None and cur == "AUD":
            value = shares * last
        if value is None:
            raise ImportErrorCMC("row %d (%s): no Value AUD to value it" % (i, code))
        price_aud = last if cur == "AUD" and last else value / shares
        out.append({"code": code, "shares": shares, "last": last, "currency": cur,
                    "value_aud": round(value, 2), "price_aud": price_aud})
    if not out:
        raise ImportErrorCMC("no holdings found in the export")
    return out


def build(rows, cash, old=None, only=None, exclude=None):
    """Returns (portfolio, prices, warnings)."""
    only = {c.strip().upper() for c in only or [] if c.strip()}
    exclude = {c.strip().upper() for c in exclude or [] if c.strip()}
    old_pos = {}
    for p in (old or {}).get("positions", []):
        old_pos[str(p.get("ticker") or p.get("symbol") or "").upper().replace(".AX", "")] = p
    positions, prices, warnings = [], {}, []
    for r in rows:
        c = r["code"]
        if (only and c not in only) or c in exclude:
            continue
        pos = {"ticker": c, "shares": r["shares"], "currency": r["currency"], "value_aud": r["value_aud"]}
        lots = (old_pos.get(c) or {}).get("lots") or []
        if lots:
            n = sum(_f(l.get("shares")) or 0 for l in lots)
            if abs(n - r["shares"]) < 1e-6:
                pos["lots"] = lots
            else:
                warnings.append("%s: your saved lots add up to %g shares but CMC shows %g; lots dropped, "
                                "re-enter them from your contract notes" % (c, n, r["shares"]))
        if r["currency"] != "AUD":
            warnings.append("%s is held in %s: counted at its AUD value (%s), never traded by the ASX skills"
                            % (c, r["currency"], "A${:,.2f}".format(r["value_aud"])))
        positions.append(pos)
        prices[c] = round(r["price_aud"], 6)
    if only - {p["ticker"] for p in positions}:
        warnings.append("not in the export: %s" % ", ".join(sorted(only - {p["ticker"] for p in positions})))
    if not any(p.get("lots") for p in positions):
        warnings.append("no cost base yet: add lots ({\"id\", \"date\", \"shares\", \"cost\"}, cost = total paid "
                        "including brokerage) from your CMC contract notes to get CGT estimates")
    return {"cash": round(float(cash), 2), "positions": positions}, prices, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description="CMC Invest holdings CSV -> trader/portfolio.json and prices.json")
    ap.add_argument("csv", help="the Holdings CSV exported from CMC Invest")
    ap.add_argument("--cash", type=float, help="cash available to trade, in AUD (the export has none)")
    ap.add_argument("--only", help="comma list of codes to import (e.g. your trading sleeve)")
    ap.add_argument("--exclude", help="comma list of codes to leave out")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--dry-run", action="store_true", help="show what would be written, write nothing")
    a = ap.parse_args(argv)
    try:
        with open(a.csv, encoding="utf-8-sig") as fh:
            rows = parse(fh.read())
        old = None
        if os.path.exists(a.portfolio):
            with open(a.portfolio, encoding="utf-8") as fh:
                old = json.load(fh)
        cash = a.cash
        if cash is None:
            if old and old.get("cash") is not None:
                cash = float(old["cash"])
                print("cash not given: keeping A$%s from %s" % ("{:,.2f}".format(cash), a.portfolio))
            else:
                raise ImportErrorCMC("give --cash: the CMC export has no cash balance")
        port, prices, warns = build(rows, cash, old, (a.only or "").split(","), (a.exclude or "").split(","))
    except (OSError, ImportErrorCMC, json.JSONDecodeError) as e:
        print("cmc_import.py: %s" % e, file=sys.stderr)
        return 1
    as_of = as_of_from_name(a.csv) or datetime.datetime.fromtimestamp(os.path.getmtime(a.csv)).isoformat(
        timespec="minutes")
    total = port["cash"] + sum(p["value_aud"] for p in port["positions"])
    print("CMC holdings as of %s: %d holdings, A$%s with cash A$%s"
          % (as_of, len(port["positions"]), "{:,.2f}".format(total), "{:,.2f}".format(port["cash"])))
    for p in port["positions"]:
        print("  %-7s %12g  A$%10s  %s%s" % (p["ticker"], p["shares"], "{:,.2f}".format(p["value_aud"]),
                                           p["currency"], "  lots kept" if p.get("lots") else ""))
    try:
        age = (datetime.datetime.now() - datetime.datetime.fromisoformat(as_of)).days
        if age > 3:
            warns.insert(0, "these prices are %d days old: export a fresh holdings file before you trade" % age)
    except ValueError:
        pass
    for w in warns:
        print("WARNING: " + w)
    if a.dry_run:
        print("Dry run: nothing written.")
        return 0
    for path, doc in ((a.portfolio, port),
                      (a.prices, {"as_of": as_of, "source": "CMC Invest holdings export (Last price)",
                                  "currency": "AUD", "prices": prices})):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        if path == a.prices and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                try:
                    old_prices = json.load(fh).get("prices", {})
                except (json.JSONDecodeError, AttributeError):
                    old_prices = {}
            kept = sorted(set(old_prices) - set(prices))
            if kept:
                print("WARNING: kept older prices for %s (not in this export); check they are current"
                      % ", ".join(kept))
            merged = dict(old_prices)
            merged.update(prices)
            doc["prices"] = merged
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
    print("Wrote %s and %s." % (a.portfolio, a.prices))
    return 0


if __name__ == "__main__":
    sys.exit(main())
