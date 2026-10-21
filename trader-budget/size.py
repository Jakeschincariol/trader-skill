#!/usr/bin/env python3
"""Position sizing for trader-budget. Standard library only, no network.

Turns target weights (the portfolio you want to copy) and your rules (budget,
cash buffer, a cap on any one stock, a minimum order) into dollar amounts per
stock, then into proposed orders shaped like Alpaca's place_stock_order. It
never places anything: it writes proposals to trader/orders.json, which
trader-risk checks and trader-broker shows you one by one.

    python3 size.py                                  # trader/targets.json + trader/config.json
    python3 size.py --budget 5000 --write            # write trader/orders.json
    python3 size.py --weights AAPL=22.04,AXP=17.14,GOOGL=12.62,KO=10.86,BAC=9.20
    python3 size.py --whole-shares --prices trader/prices.json
    python3 size.py --json

How the money is split:

  1. cash buffer   budget x cash_buffer_pct stays in cash, never invested
  2. weights       renormalised to 100 over the stocks you keep (blocked
                   tickers are dropped first; --top N keeps the N biggest)
  3. cap           no stock gets more than max_position_pct of the budget;
                   anything over the cap is shared out to the others by
                   weight, again and again until nobody is over
  4. min order     a slice under min_order_usd is dropped and its weight
                   shared out, so you never place a $2 order
  5. rounding      whole dollars by default (--cents for cents); the rounding
                   dollar goes to the biggest position that is not capped, so
                   every dollar of the investable money is used
  6. whole shares  only with --whole-shares or "fractional": false; needs
                   prices; buys whole shares, then spends what is left one
                   share at a time on the stock furthest below its slice

Money that cannot be invested (every stock at its cap, or leftovers from
whole shares) is reported as "left idle", never hidden.

With "market": "asx" in the config (ASX shares through CMC Invest, in AUD):
whole shares only (prices needed), codes normalised (BHP.AX -> BHP) and
checked, every slice at least the ASX $500 marketable parcel, CMC brokerage
estimated per order and paid out of the investable money (so invested plus
brokerage never eats the cash buffer), and a note when an order sits just
over CMC's free first-buy tier. Orders are written as share counts for you to
type into CMC yourself.
"""

import argparse
import datetime
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asx as A  # noqa: E402  (ships next to this file)

DEFAULTS = {
    "budget": None,
    "cash_buffer_pct": 10,
    "max_position_pct": 25,
    "max_order_usd": 2000,
    "min_order_usd": 5,
    "fractional": True,
    "broker": "alpaca",
    "paper": True,
    "blocked": [],
    "allow_options": False,
}
EPS = 1e-9


class SizeError(Exception):
    pass


# ---------------------------------------------------------------- inputs

def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SizeError("no %s at %s" % (what, path))
    except json.JSONDecodeError as e:
        raise SizeError("%s is not valid JSON: %s" % (path, e))


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        cfg.update(read_json(path, "config"))
    return cfg


def parse_weights(data):
    """Accept targets.json, a {ticker: weight} dict or a list of rows.

    Returns [{"ticker", "name", "weight"}] with weight as a float."""
    if isinstance(data, dict) and "weights" in data:
        data = data["weights"]
    rows = []
    if isinstance(data, dict):
        for t, w in data.items():
            rows.append({"ticker": t, "name": "", "weight": w})
    elif isinstance(data, list):
        for i, r in enumerate(data):
            if not isinstance(r, dict):
                raise SizeError("weights[%d] is not an object" % i)
            t = r.get("ticker") or r.get("symbol")
            w = r.get("weight", r.get("weight_pct", r.get("pct")))
            if not t or w is None:
                raise SizeError("weights[%d] needs a ticker and a weight" % i)
            rows.append({"ticker": t, "name": r.get("name", ""), "weight": w})
    else:
        raise SizeError("weights must be a list or an object")
    out = []
    for r in rows:
        try:
            w = float(r["weight"])
        except (TypeError, ValueError):
            raise SizeError("weight for %s is not a number: %r" % (r["ticker"], r["weight"]))
        out.append({"ticker": str(r["ticker"]).strip().upper(), "name": r["name"] or "", "weight": w})
    return out


def weights_from_arg(text):
    """AAPL=22.04,AXP=17.14 -> {ticker: weight}"""
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SizeError("--weights wants TICKER=WEIGHT pairs, got %r" % part)
        t, w = part.split("=", 1)
        out[t.strip().upper()] = w.strip()
    return out


def load_prices(path):
    if not path:
        return {}
    data = read_json(path, "prices")
    prices = data.get("prices", data) if isinstance(data, dict) else {}
    out = {}
    for t, p in prices.items():
        try:
            out[str(t).upper()] = float(p)
            out[A.code(t)] = float(p)
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------- the maths

def normalise(rows, blocked=(), top=None):
    """Drop blocked and non-positive weights, keep the top N, renormalise to 100."""
    blocked = {str(b).upper() for b in (blocked or [])}
    dropped, keep, seen = [], [], set()
    for r in rows:
        if r["ticker"] in seen:
            raise SizeError("%s is listed twice in the weights" % r["ticker"])
        seen.add(r["ticker"])
        if r["ticker"] in blocked:
            dropped.append({"ticker": r["ticker"], "reason": "on your blocked list"})
        elif r["weight"] <= 0:
            dropped.append({"ticker": r["ticker"], "reason": "weight is zero or negative"})
        else:
            keep.append(dict(r))
    keep.sort(key=lambda r: -r["weight"])
    if top:
        for r in keep[top:]:
            dropped.append({"ticker": r["ticker"], "reason": "outside the top %d" % top})
        keep = keep[:top]
    total = sum(r["weight"] for r in keep)
    for r in keep:
        r["source_weight"] = r["weight"]
        r["weight"] = 100.0 * r["weight"] / total if total else 0.0
    return keep, dropped


def allocate(rows, investable, cap):
    """Exact (unrounded) dollars per ticker with the cap enforced.

    Proportional to weight; any stock over the cap is pinned at the cap and
    the rest of the money is shared over the others, repeated until no stock
    is over. Returns ({ticker: dollars}, set of capped tickers)."""
    alloc, capped = {}, set()
    active = [r for r in rows]
    money = investable
    while active:
        wsum = sum(r["weight"] for r in active)
        trial = {r["ticker"]: money * r["weight"] / wsum for r in active}
        over = [r for r in active if trial[r["ticker"]] > cap + EPS]
        if not over:
            alloc.update(trial)
            break
        for r in over:
            alloc[r["ticker"]] = cap
            capped.add(r["ticker"])
            money -= cap
        active = [r for r in active if r["ticker"] not in capped]
        if money <= EPS:
            for r in active:
                alloc[r["ticker"]] = 0.0
            break
    return alloc, capped


def round_to_unit(exact, capped, order, cap, unit):
    """Round to whole dollars (unit 1) or cents (unit 0.01) and keep the total.

    The total placed is the exact total rounded DOWN to the unit, so it never
    spends money you do not have. Each slice is rounded to the nearest unit,
    then the difference (usually one unit) goes to the biggest stock that is
    not capped, then the next, never pushing anyone over the cap."""
    def units(x):
        return int(math.floor(x / unit + EPS))

    cap_u = units(cap)
    total_u = units(sum(exact.values()))
    out = {}
    for t, v in exact.items():
        out[t] = cap_u if t in capped else int(math.floor(v / unit + 0.5 + EPS))
    diff = total_u - sum(out.values())
    takers = [t for t in order if t not in capped]
    guard = 0
    while diff != 0 and takers and guard < 100000:
        moved = False
        for t in takers:
            if diff == 0:
                break
            if diff > 0 and out[t] + 1 <= cap_u:
                out[t] += 1
                diff -= 1
                moved = True
            elif diff < 0 and out[t] > 0:
                out[t] -= 1
                diff += 1
                moved = True
        if not moved:
            break
        guard += 1
    return {t: round(u * unit, 2) for t, u in out.items()}


def whole_shares(dollars, prices, order, cap, money):
    """Whole shares: floor each slice, then buy one more share at a time for the
    stock furthest below its slice while cash lasts and the cap holds."""
    shares = {}
    for t in order:
        shares[t] = int(math.floor(dollars[t] / prices[t] + EPS)) if dollars[t] > 0 else 0
    spent = sum(shares[t] * prices[t] for t in order)
    left = money - spent
    while True:
        best, gap = None, None
        for t in order:
            p = prices[t]
            if p > left + EPS or (shares[t] + 1) * p > cap + EPS:
                continue
            g = dollars[t] - shares[t] * p
            if best is None or g > gap + EPS:
                best, gap = t, g
        if best is None:
            break
        shares[best] += 1
        left -= prices[best]
    return shares


def size(rows, cfg, budget=None, prices=None, whole=None, cents=False, top=None):
    """Pure function: weights + rules -> the full sizing result (a dict)."""
    budget = float(budget if budget is not None else (cfg.get("budget") or 0))
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in trader/config.json")
    asx = A.is_asx(cfg)
    buf_pct = float(cfg.get("cash_buffer_pct", 10))
    cap_pct = float(cfg.get("max_position_pct", 25))
    min_order = float(A.limit(cfg, "min_order", 5) or 0)
    notes = []
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")
    if not 0 < cap_pct <= 100:
        raise SizeError("max_position_pct must be above 0 and at most 100")
    if whole is None:
        whole = not cfg.get("fractional", True)
    prices = prices or {}
    pre_dropped = []
    if asx:
        whole = True
        if min_order < A.MARKETABLE_PARCEL:
            min_order = A.MARKETABLE_PARCEL
            notes.append("minimum order raised to $500: a new ASX holding must be worth at least $500 "
                         "(the marketable parcel)")
        prices = dict(prices)
        for k, v in list(prices.items()):
            prices.setdefault(A.code(k), v)
        ok_rows, seen_codes = [], {}
        for r in rows:
            r = dict(r, ticker=A.code(r["ticker"]))
            why = A.check_code(r["ticker"], cfg.get("asx_allow_codes"))
            if why:
                pre_dropped.append({"ticker": r["ticker"], "reason": why})
                continue
            ok_rows.append(r)
        rows = ok_rows
        if not rows:
            raise SizeError("no tradeable ASX codes left: %s"
                            % "; ".join(d["reason"] for d in pre_dropped))

    keep0, dropped0 = normalise(rows, cfg.get("blocked"), top)
    dropped0 = pre_dropped + dropped0
    if not keep0:
        raise SizeError("nothing to size: no weights left after the blocked list")

    cash_buffer = round(budget * buf_pct / 100.0, 2)
    investable = round(budget - cash_buffer, 2)
    cap = round(budget * cap_pct / 100.0, 2)
    unit = 0.01 if (cents or whole) else 1.0

    if whole:
        missing = [r["ticker"] for r in keep0 if not prices.get(r["ticker"])]
        if missing:
            if asx:
                raise SizeError("ASX orders are whole shares, so every code needs a price: %s. Put the "
                                "latest prices in trader/prices.json ({\"prices\": {\"VAS\": 104.50}}); "
                                "cmc_import.py (trader-risk / trader-rebalance) writes your holdings' "
                                "last prices. Never guess a price." % ", ".join(missing))
            raise SizeError("whole shares need a price for: %s (add them to trader/prices.json, "
                            "e.g. with trader-alerts: alerts.py quote %s --write)"
                            % (", ".join(missing), " ".join(missing)))

    def run(money_to_invest):
        keep, dropped = [dict(r) for r in keep0], list(dropped0)
        while True:
            live = [dict(r) for r in keep]
            tot = sum(r["source_weight"] for r in live)
            for r in live:
                r["weight"] = 100.0 * r["source_weight"] / tot
            exact, capped = allocate(live, money_to_invest, cap)
            order = [r["ticker"] for r in sorted(live, key=lambda r: -exact[r["ticker"]])]
            dollars = round_to_unit(exact, capped, order, cap, unit)
            shares = None
            if whole:
                shares = whole_shares(dollars, prices, order, cap, sum(dollars.values()))
                dollars = {t: round(shares[t] * prices[t], 2) for t in order}
            small = [r for r in live if dollars[r["ticker"]] < min_order - EPS]
            if not small:
                return live, capped, dollars, shares, dropped
            worst = min(small, key=lambda r: (r["source_weight"], r["ticker"]))
            tw = worst["ticker"]
            if whole and shares is not None and shares[tw] == 0 and prices.get(tw, 0) > dollars[tw] \
                    and prices[tw] > min_order:
                reason = ("one share costs $%s, more than its slice; it cannot be bought in whole shares"
                          % _num(prices[tw]))
            else:
                reason = "its slice ($%s) is under your $%s minimum order" % (_num(dollars[tw]), _num(min_order))
            dropped.append({"ticker": worst["ticker"], "reason": reason})
            keep = [r for r in keep if r["ticker"] != worst["ticker"]]
            if not keep:
                raise SizeError("every slice is under your minimum order; raise the budget or hold fewer stocks")

    fees = {}
    if asx:
        fee_guess = 0.0
        for _ in range(8):
            live, capped, dollars, shares, dropped = run(round(investable - fee_guess, 2))
            fees = {t: A.brokerage(dollars[t], "buy", cfg) for t in dollars}
            total_fee = round(sum(fees.values()), 2)
            if sum(dollars.values()) + total_fee <= investable + EPS:
                break
            fee_guess = max(total_fee, fee_guess + 0.01)
        # last resort: give back one share at a time from the biggest position
        while sum(dollars.values()) + sum(fees.values()) > investable + EPS:
            t = max(dollars, key=lambda k: dollars[k])
            if shares[t] <= 0:
                break
            shares[t] -= 1
            dollars[t] = round(shares[t] * prices[t], 2)
            fees[t] = A.brokerage(dollars[t], "buy", cfg)
        upto = A.free_tier(cfg)
        for t in dollars:
            if fees.get(t) and upto and upto < dollars[t] <= upto + 10 * fees[t]:
                n = int(math.floor(upto / prices[t] + EPS))
                if n * prices[t] >= min_order - EPS and n > 0:
                    notes.append("%s: %d shares (%s) would be free to buy on CMC instead of about %s brokerage "
                                 "for %s" % (t, n, money(n * prices[t]), money(fees[t]), money(dollars[t])))
    else:
        live, capped, dollars, shares, dropped = run(investable)

    positions = []
    for r in sorted(live, key=lambda r: -r["weight"]):
        t = r["ticker"]
        p = {"ticker": t, "name": r["name"], "weight": round(r["weight"], 2),
             "source_weight": r["source_weight"], "dollars": dollars[t],
             "capped": t in capped}
        if prices.get(t):
            p["price"] = prices[t]
            p["shares"] = shares[t] if whole else round(dollars[t] / prices[t], 4)
        if asx:
            p["brokerage"] = fees.get(t, 0.0)
        positions.append(p)
    invested = round(sum(p["dollars"] for p in positions), 2)
    out = {
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer,
        "investable": investable, "max_position_pct": cap_pct, "cap": cap,
        "min_order_usd": min_order, "whole_shares": bool(whole),
        "positions": positions, "invested": invested,
        "idle": round(investable - invested, 2), "dropped": dropped,
    }
    if asx:
        total_fee = round(sum(p["brokerage"] for p in positions), 2)
        out.update({"market": "asx", "currency": "AUD", "min_order": out.pop("min_order_usd"),
                    "brokerage": total_fee, "idle": round(investable - invested - total_fee, 2),
                    "notes": notes, "order_type": str(cfg.get("order_type") or "market").lower(),
                    "time_in_force": str(cfg.get("time_in_force") or "day").lower()})
    return out


# ---------------------------------------------------------------- orders

def _num(x):
    """1250 -> "1250", 1118.13 -> "1118.13" (Alpaca wants these as strings)."""
    x = round(float(x), 2)
    if abs(x - round(x)) < 0.005:
        return str(int(round(x)))
    return "%.2f" % x


def orders_from(result, source=""):
    """Proposed buy orders, one per position, in Alpaca place_stock_order shape."""
    out = []
    for p in result["positions"]:
        if p["dollars"] <= 0:
            continue
        why = "%.2f%% of the stock sleeve" % p["weight"]
        if source:
            why = "%s, copying %s" % (why, source)
        if p["capped"]:
            why += "; capped at %s%% of the %s budget" % (_num(result["max_position_pct"]), money(result["budget"]))
        if result.get("market") == "asx":
            o = {"symbol": p["ticker"], "side": "buy", "qty": str(int(p["shares"])),
                 "type": result.get("order_type", "market"), "time_in_force": result.get("time_in_force", "day")}
            if o["type"] in ("limit", "stop_limit"):
                o["limit_price"] = "%g" % A.round_tick(p["price"], down=False)
            o.update({"exchange": "ASX", "currency": "AUD", "est_brokerage": p.get("brokerage", 0.0),
                      "why": why.replace("$", "A$")})
            out.append(o)
            continue
        o = {"symbol": p["ticker"], "side": "buy"}
        if result["whole_shares"]:
            o["qty"] = str(int(p["shares"]))
        else:
            o["notional"] = _num(p["dollars"])
        o.update({"type": "market", "time_in_force": "day", "why": why})
        out.append(o)
    return out


def write_orders(path, orders, result, cfg, source=""):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    asx = result.get("market") == "asx"
    doc = {
        "created_by": "trader-budget",
        "created_at": datetime.datetime.now().replace(microsecond=0).isoformat(),
        "status": "proposed",
        "paper": bool(cfg.get("paper", True)),
        "note": (("Proposals only, for ASX via CMC Invest. Run trader-risk on them; you decide each one "
                  "and type it into CMC yourself.") if asx else
                 ("Proposals only. Nothing is placed until trader-risk passes each order "
                  "and you say yes to it in the chat.")),
        "source": source,
        "orders": orders,
    }
    if asx:
        doc.pop("paper", None)
        doc["market"] = "asx"
        doc["currency"] = "AUD"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


# ---------------------------------------------------------------- report

def money(x):
    x = float(x)
    if abs(x - round(x)) < 0.005:
        return "$" + "{:,.0f}".format(round(x))
    return "$" + "{:,.2f}".format(x)


def report_asx(r, orders, source=""):
    def m(x):
        return "A" + money(x)
    lines = []
    if source:
        lines.append("Copying: %s" % source)
    lines.append("Budget %s, cash buffer %s%% = %s, investing up to %s, cap %s%% = %s per stock (ASX, CMC, AUD)"
                 % (m(r["budget"]), _num(r["cash_buffer_pct"]), m(r["cash_buffer"]), m(r["investable"]),
                    _num(r["max_position_pct"]), m(r["cap"])))
    lines.append("")
    lines.append("%-7s %-22s %8s %10s %7s %11s %10s  note" % ("code", "name", "weight", "price", "shares",
                                                            "cost", "brokerage"))
    for p in r["positions"]:
        row = "%-7s %-22s %7.2f%% %10s %7s %11s %10s" % (
            p["ticker"], (p["name"] or "")[:22], p["weight"], m(p["price"]), p["shares"], m(p["dollars"]),
            m(p["brokerage"]) if p["brokerage"] else "free")
        if p["capped"]:
            row += "  capped at %s%%" % _num(r["max_position_pct"])
        lines.append(row)
    lines.append("%-7s %-22s %8s %10s %7s %11s %10s" % ("", "invested", "", "", "", m(r["invested"]),
                                                       m(r["brokerage"])))
    lines.append("%-7s %-22s %8s %10s %7s %11s" % ("", "cash buffer", "", "", "", m(r["cash_buffer"])))
    lines.append("%-7s %-22s %8s %10s %7s %11s" % ("", "left in cash", "", "", "", m(r["idle"])))
    if r["idle"] >= 1:
        lines.append("")
        lines.append("Left in cash %s on top of the buffer: whole shares do not divide evenly, a slice was "
                     "dropped, or every stock is at its cap." % m(r["idle"]))
    for n in r.get("notes", []):
        lines.append("Note: " + n.replace("$", "A$").replace("AA$", "A$"))
    for d in r["dropped"]:
        lines.append("Dropped %s: %s" % (d["ticker"], d["reason"].replace("$", "A$")))
    lines.append("")
    lines.append("Proposed CMC orders (nothing has been placed; you type these into CMC yourself):")
    for o in orders:
        price = (" limit %s" % o["limit_price"]) if o.get("limit_price") else ""
        lines.append("  %s %s shares of %s, %s%s, %s  (brokerage about %s)"
                     % (o["side"], o["qty"], o["symbol"], "at market" if o["type"] == "market" else o["type"],
                        price, {"day": "day only", "gtc": "good till cancelled"}.get(o["time_in_force"],
                                                                                  o["time_in_force"]),
                        m(o["est_brokerage"]) if o["est_brokerage"] else "A$0"))
    lines.append("")
    lines.append("Prices are the last ones in trader/prices.json: check them in CMC before you trade, a market "
                 "order fills at whatever the market price is then.")
    lines.append("Next: trader-risk checks every order. Education and tooling, not financial advice.")
    return "\n".join(lines)


def report(result, orders, source=""):
    r = result
    if r.get("market") == "asx":
        return report_asx(r, orders, source)
    lines = []
    if source:
        lines.append("Copying: %s" % source)
    lines.append("Budget %s, cash buffer %s%% = %s, investing %s, cap %s%% = %s per stock"
                 % (money(r["budget"]), _num(r["cash_buffer_pct"]), money(r["cash_buffer"]),
                    money(r["investable"]), _num(r["max_position_pct"]), money(r["cap"])))
    lines.append("")
    has_price = any("price" in p for p in r["positions"])
    head = "%-7s %-22s %8s %11s" % ("ticker", "name", "weight", "dollars")
    if has_price:
        head += " %10s %10s" % ("price", "shares")
    lines.append(head + "  note")
    for p in r["positions"]:
        row = "%-7s %-22s %7.2f%% %11s" % (p["ticker"], (p["name"] or "")[:22], p["weight"], money(p["dollars"]))
        if has_price:
            row += " %10s %10s" % (money(p["price"]) if "price" in p else "", p.get("shares", ""))
        if p["capped"]:
            row += "  capped at %s%%" % _num(r["max_position_pct"])
        lines.append(row)
    lines.append("%-7s %-22s %8s %11s" % ("", "invested", "", money(r["invested"])))
    lines.append("%-7s %-22s %8s %11s" % ("", "cash buffer", "", money(r["cash_buffer"])))
    lines.append("%-7s %-22s %8s %11s" % ("", "left idle", "", money(r["idle"])))
    if r["idle"] >= 1:
        lines.append("")
        lines.append("Left idle %s: %s" % (money(r["idle"]), (
            "whole shares do not divide evenly" if r["whole_shares"]
            else "every stock is at its cap; add stocks or raise max_position_pct")))
    for d in r["dropped"]:
        lines.append("Dropped %s: %s" % (d["ticker"], d["reason"]))
    lines.append("")
    lines.append("Proposed orders (place_stock_order arguments, nothing has been placed):")
    for o in orders:
        amount = ("notional \"%s\"" % o["notional"]) if "notional" in o else ("qty \"%s\"" % o["qty"])
        lines.append("  %s %s %s, %s, %s" % (o["side"], o["symbol"], amount, o["type"], o["time_in_force"]))
    lines.append("")
    lines.append("Next: trader-risk checks every order, then trader-broker asks you yes or no for each one.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Size positions to your budget (trader-budget).")
    ap.add_argument("--targets", default="trader/targets.json", help="target weights (default trader/targets.json)")
    ap.add_argument("--weights", help="inline weights instead of a file: AAPL=22.04,AXP=17.14")
    ap.add_argument("--config", default="trader/config.json", help="rules (default trader/config.json)")
    ap.add_argument("--budget", type=float, help="override the budget in the config")
    ap.add_argument("--top", type=int, help="keep only the N biggest weights")
    ap.add_argument("--prices", help="prices JSON (needed for --whole-shares; shows share counts)")
    ap.add_argument("--whole-shares", action="store_true", help="buy whole shares only (needs --prices)")
    ap.add_argument("--cents", action="store_true", help="size to the cent instead of whole dollars")
    ap.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                    help="write the proposed orders (default trader/orders.json)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the table")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        source = ""
        if a.weights:
            rows = parse_weights(weights_from_arg(a.weights))
        else:
            data = read_json(a.targets, "targets file (run trader-copycat, or pass --weights)")
            rows = parse_weights(data)
            if isinstance(data, dict):
                source = data.get("source", "")
        prices = load_prices(a.prices)
        if not prices and os.path.exists("trader/prices.json") and not a.prices:
            prices = load_prices("trader/prices.json")
        whole = True if a.whole_shares else None
        result = size(rows, cfg, a.budget, prices, whole, a.cents, a.top)
        orders = orders_from(result, source)
        if a.write:
            write_orders(a.write, orders, result, cfg, source)
    except SizeError as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps({"result": result, "orders": orders}, indent=2))
    else:
        print(report(result, orders, source))
        if a.write:
            print("Wrote %d proposed orders to %s" % (len(orders), a.write))
    return 0


if __name__ == "__main__":
    sys.exit(main())
