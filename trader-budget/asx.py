"""ASX and Australian-tax helpers shared by trader-budget, trader-risk and
trader-rebalance. Standard library only, no network.

The same file ships in each of those three skill folders (each skill is
uploaded on its own), so keep the copies identical: tests/test_asx.py checks.

Turned on with "market": "asx" in trader/config.json. Every rule here was
checked against a public source on 2026-10-21; the defaults are the user's to
change in their config, never this file's to assume:

  codes        ASX codes are 3 characters for ordinary shares and most ETFs.
               4 to 6 characters can be a newer ETF or a structured product
               (ETPMPM), but also options, warrants, rights or partly paid
               shares, so those pass only once listed in "asx_allow_codes".
               Codes may contain digits (S32, A2M, 1AE).       [asx.com.au]
  price steps  up to 10c: 0.1c; 10c to $2: 0.5c; $2 and up: 1c. [asx.com.au]
  parcel       a new holding must be worth at least $500 (excluding
               brokerage); a holding under $500 must be topped back up to
               $500 by the next buy.             [CommSec, Selfwealth help]
  settlement   T+2: cash from a sale arrives two business days later.
               [ASX T+2 settlement calendar 2026]
  brokerage    CMC Invest standard: first buy of each stock per day up to
               $1,000 is free; otherwise $11 or a percentage, whichever is
               higher. Sources disagree on the percentage (CMC's own page:
               0.11%; Finder, Snowball: 0.10%), so the default is CMC's
               own 0.11% and the user should confirm it.
  CGT discount an individual Australian resident halves a capital gain on an
               asset held at least 12 months before the CGT event, counting
               neither the day bought nor the day sold; for a sale the event
               is the trade (contract) date, not settlement.     [ato.gov.au]
  wash sales   no fixed window in Australia: TR 2008/1 says selling at a loss
               and buying the same thing back soon after, with no real change
               in exposure, can have the loss cancelled under Part IVA.
               (A 24-hour gap was caught in its examples.)        [ato.gov.au]

Estimates, not tax advice.
"""

import datetime
import math
import re

MARKETABLE_PARCEL = 500.0
CMC_BROKERAGE = {"free_first_buy_upto": 1000.0, "min": 11.0, "pct": 0.11}
CODE = re.compile(r"^[A-Z0-9]{3,6}$")
EPS = 1e-9


def is_asx(cfg):
    return str((cfg or {}).get("market", "")).strip().lower() == "asx"


def limit(cfg, name, default=None):
    """min_order / max_order in any spelling: min_order, min_order_aud, min_order_usd."""
    for k in (name, name + "_aud", name + "_usd"):
        if (cfg or {}).get(k) is not None:
            return float(cfg[k])
    return default


def code(sym):
    """'asx:bhp', 'BHP.AX', ' bhp.asx ' -> 'BHP'."""
    s = str(sym or "").strip().upper()
    if s.startswith("ASX:"):
        s = s[4:]
    for suffix in (".AX", ".ASX"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s.strip()


def check_code(c, allow=()):
    """None when the code can be traded, else the reason it cannot."""
    allow = {code(a) for a in (allow or [])}
    if not CODE.match(c or ""):
        return ("%s is not an ASX code (3 to 6 letters or digits, e.g. BHP, S32, VAS)" % (c or "''"))
    if len(c) == 3 or c in allow:
        return None
    return ("%s has %d characters: on the ASX that can be an ETF or structured product, but also an "
            "option, warrant, rights issue or partly paid share. Check what it is, then add it to "
            "\"asx_allow_codes\" in trader/config.json" % (c, len(c)))


def tick(price):
    p = float(price)
    if p < 0.10 - EPS:
        return 0.001
    if p < 2.00 - EPS:
        return 0.005
    return 0.01


def on_tick(price):
    p = float(price)
    t = tick(p)
    return abs(p / t - round(p / t)) < 1e-6


def round_tick(price, down=True):
    p = float(price)
    t = tick(p)
    n = (p / t) + (1e-9 if down else -1e-9)
    return round((math.floor(n) if down else math.ceil(n)) * t, 3)


def brokerage(value, side, cfg=None, free_used=False):
    """Estimated brokerage on one order. free_used: this stock already had its
    free first buy today (earlier in the batch or already placed)."""
    b = dict(CMC_BROKERAGE)
    b.update((cfg or {}).get("brokerage") or {})
    value = float(value or 0)
    if value <= 0:
        return 0.0
    upto = float(b.get("free_first_buy_upto") or 0)
    if side == "buy" and not free_used and upto and value <= upto + EPS:
        return 0.0
    return round(max(float(b.get("min") or 0), value * float(b.get("pct") or 0) / 100.0), 2)


def free_tier(cfg=None):
    b = dict(CMC_BROKERAGE)
    b.update((cfg or {}).get("brokerage") or {})
    return float(b.get("free_first_buy_upto") or 0)


# ---------------------------------------------------------------- Australian CGT

def day(x):
    if isinstance(x, datetime.date):
        return x
    return datetime.date.fromisoformat(str(x)[:10])


def _plus_year(d):
    try:
        return d.replace(year=d.year + 1)
    except ValueError:            # 29 February
        return d.replace(year=d.year + 1, month=3, day=1)


def discount_from(bought):
    """First sale (trade) date that counts as held 12 months: neither the day
    bought nor the day sold counts, so it is the anniversary plus one day."""
    return _plus_year(day(bought)) + datetime.timedelta(days=1)


def discount_ok(bought, sold):
    return day(sold) >= discount_from(bought)


def fin_year(d):
    d = day(d)
    start = d.year if d.month >= 7 else d.year - 1
    return "FY%d-%s" % (start, str(start + 1)[2:])


def sell_preview(lots, shares, price, sold, fee=0.0, wait_days=120, keep_order=False):
    """Which lots to sell to keep the taxable gain lowest, and the estimate.

    lots: [{"id", "date", "shares", "cost"}], cost = total cost base of the lot
    (including the buy brokerage). Australia lets you choose the parcels you
    sell when you can identify them, so the lots go in order of taxable gain
    per share: the biggest losses first, then the smallest gains after the
    50 % discount (a discounted gain counts half). The sell brokerage reduces the capital
    proceeds, shared over the lots by shares. Returns a dict; "taxable" is the
    gain after the discount, before netting other losses (the ATO nets losses
    against gains BEFORE the discount, so the true figure can differ)."""
    sold = day(sold)
    price = float(price)
    shares = float(shares)
    rows = []
    for i, l in enumerate(lots or [], 1):
        n = float(l.get("shares") or 0)
        if n <= EPS:
            continue
        cps = float(l.get("cost") or 0) / n
        ok = discount_ok(l["date"], sold)
        g = price - cps
        rank = g if g < 0 else g * (0.5 if ok else 1.0)
        rows.append({"id": l.get("id") or "lot-%d" % i, "date": str(l["date"])[:10], "shares": n,
                     "cost_per_share": cps, "discount": ok, "rank": rank})
    if not keep_order:          # keep_order: sell the lots in the order given (FIFO, specific lots)
        rows.sort(key=lambda r: r["rank"])
    left, out = shares, []
    for r in rows:
        if left <= EPS:
            break
        take = min(left, r["shares"])
        left -= take
        out.append(dict(r, take=take))
    covered = shares - max(left, 0.0)
    gross = gain = disc_gain = nondisc_gain = loss = 0.0
    waits = []
    for r in out:
        share_fee = fee * (r["take"] / covered) if covered else 0.0
        g = r["take"] * (price - r["cost_per_share"]) - share_fee
        r["gain"] = round(g, 2)
        gross += r["take"] * price
        gain += g
        if g < 0:
            loss += g
        elif r["discount"]:
            disc_gain += g
        else:
            nondisc_gain += g
            when = discount_from(r["date"])
            days = (when - sold).days
            if 0 < days <= wait_days:
                waits.append({"id": r["id"], "date": when.isoformat(), "days": days,
                              "taxable_saved": round(g / 2.0, 2)})
        del r["rank"]
        r["take"] = round(r["take"], 6)
        r["cost_per_share"] = round(r["cost_per_share"], 4)
    return {
        "lots": out, "uncovered_shares": round(max(left, 0.0), 6), "fin_year": fin_year(sold),
        "gain": round(gain, 2), "losses": round(loss, 2), "discounted_gain": round(disc_gain, 2),
        "undiscounted_gain": round(nondisc_gain, 2),
        "taxable": round(nondisc_gain + disc_gain / 2.0 + loss, 2),
        "waits": waits,
    }


def recent_loss_sales(sym, when, realized, days=30):
    """Loss sales of this code in the last `days` days (trader/state/tax.json)."""
    when = day(when)
    out = []
    for r in realized or []:
        if code(r.get("symbol")) != sym or float(r.get("gain", 0) or 0) >= 0:
            continue
        try:
            d = day(r.get("date"))
        except (TypeError, ValueError):
            continue
        if d <= when <= d + datetime.timedelta(days=days):
            out.append(r)
    return out
