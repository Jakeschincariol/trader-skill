#!/usr/bin/env python3
"""Cost-basis lots for trader-tax. Standard library only, no network.

Not tax advice: it does arithmetic on the lots you give it. A tax
professional decides what applies to you.

    python3 lots.py report                          # unrealized gain/loss per stock and lot
    python3 lots.py report --lots                   # every lot, short or long term
    python3 lots.py sell KO 2 --price 88.50         # what selling 2 KO would realize (FIFO)
    python3 lots.py sell KO 2 --price 88.50 --method specific --lots KO-2
    python3 lots.py sell KO 2 --price 88.50 --record    # it filled: update lots + realized log
    python3 lots.py harvest --min-loss 100          # tax-loss harvesting candidates
    python3 lots.py harvest --min-loss 100 --write  # write them to trader/orders.json as proposals
    python3 lots.py wash KO                         # would buying KO today be a wash sale?

Lots live in trader/portfolio.json:

    {"cash": 500.0, "positions": [{"ticker": "KO", "shares": 7.8571, "lots": [
        {"id": "KO-1", "date": "2026-10-13", "shares": 7.8571, "cost": 708.0}]}]}

"cost" is the lot's total cost basis in dollars. A lot without an "id" gets
TICKER-N by its place in the list.

The rules it applies (US federal, the arithmetic only):
  term          long term if sold MORE than one year after the purchase date,
                otherwise short term
  lot methods   fifo (oldest first, what most brokers do by default), hifo
                (highest cost first) and specific (you name the lots)
  wash sale     a loss is disallowed to the extent you buy the same or a
                substantially identical stock within 30 days before or after
                the sale; GOOG/GOOGL and BRK.A/BRK.B count as one family here.
                It checks your other lots, the buys in trader/orders.json and
                the loss sales you recorded. It cannot see your IRA, your
                spouse's accounts or other brokers: those count too.
"""

import argparse
import datetime
import json
import os
import sys

FAMILIES = [{"GOOGL", "GOOG"}, {"BRK.A", "BRK.B", "BRK-A", "BRK-B"}, {"FOXA", "FOX"},
            {"NWSA", "NWS"}, {"LLYVA", "LLYVK"}, {"LEN", "LEN.B"}, {"UA", "UAA"}]
EPS = 1e-9


class TaxError(Exception):
    pass


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TaxError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TaxError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def day(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        raise TaxError("not a date (YYYY-MM-DD): %r" % s)


def money(x):
    x = float(x)
    sign = "-" if x < -0.004 else ""
    return "%s$%s" % (sign, "{:,.2f}".format(abs(x)))


def fmt_qty(x):
    s = ("%.6f" % float(x)).rstrip("0").rstrip(".")
    return s or "0"


def family(ticker):
    t = ticker.upper()
    for f in FAMILIES:
        if t in f:
            return set(f)
    return {t}


# ---------------------------------------------------------------- lots

def lots_of(portfolio):
    """{ticker: [lot, ...]} with id, date (date), shares, cost, per_share; oldest first."""
    out = {}
    for p in (portfolio or {}).get("positions", []):
        t = str(p.get("ticker") or p.get("symbol") or "").upper()
        if not t:
            continue
        lots = []
        for i, l in enumerate(p.get("lots", []), 1):
            sh, cost = float(l.get("shares", 0)), float(l.get("cost", 0))
            if sh <= EPS:
                continue
            lots.append({"id": l.get("id") or "%s-%d" % (t, i), "date": day(l.get("date")),
                         "shares": sh, "cost": cost, "per_share": cost / sh})
        if not lots and float(p.get("shares", 0) or 0) > 0:
            raise TaxError("%s has shares but no lots; add its lots (date, shares, cost) to "
                           "trader/portfolio.json" % t)
        lots.sort(key=lambda l: (l["date"], l["id"]))
        out[t] = lots
    return out


def anniversary(d):
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return d.replace(year=d.year + 1, day=28)


def term(acquired, sold):
    """long if held MORE than one year (sold after the first anniversary)."""
    return "long" if sold > anniversary(acquired) else "short"


def unrealized(portfolio, prices, today):
    rows, totals, missing = [], [], []
    for t, lots in sorted(lots_of(portfolio).items()):
        p = prices.get(t)
        if not p:
            missing.append(t)
            continue
        tot = {"ticker": t, "shares": 0.0, "cost": 0.0, "value": 0.0, "short": 0.0, "long": 0.0}
        for l in lots:
            value = l["shares"] * p
            g = value - l["cost"]
            tm = term(l["date"], today)
            rows.append({"ticker": t, "id": l["id"], "date": l["date"].isoformat(), "shares": l["shares"],
                         "cost": round(l["cost"], 2), "price": p, "value": round(value, 2),
                         "gain": round(g, 2), "term": tm,
                         "long_term_on": (anniversary(l["date"]) + datetime.timedelta(days=1)).isoformat()})
            tot["shares"] += l["shares"]
            tot["cost"] += l["cost"]
            tot["value"] += value
            tot[tm] += g
        tot["gain"] = round(tot["value"] - tot["cost"], 2)
        tot["pct"] = round(100.0 * tot["gain"] / tot["cost"], 2) if tot["cost"] else 0.0
        if abs(tot["pct"]) < 0.005:
            tot["pct"] = 0.0
        for k in ("cost", "value", "short", "long"):
            tot[k] = round(tot[k], 2)
        totals.append(tot)
    return rows, totals, missing


def pick_lots(lots, shares, method="fifo", ids=None):
    """[(lot, shares_taken)] for selling `shares` by the lot method."""
    held = sum(l["shares"] for l in lots)
    if shares > held + EPS:
        raise TaxError("selling %s shares but only %s are held" % (fmt_qty(shares), fmt_qty(held)))
    if method == "fifo":
        order = list(lots)
    elif method == "hifo":
        order = sorted(lots, key=lambda l: -l["per_share"])
    elif method == "specific":
        if not ids:
            raise TaxError("--method specific needs --lots ID,ID")
        by_id = {l["id"]: l for l in lots}
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            raise TaxError("no such lot: %s (lots: %s)" % (", ".join(unknown), ", ".join(by_id)))
        order = [by_id[i] for i in ids]
        if shares > sum(l["shares"] for l in order) + EPS:
            raise TaxError("the named lots hold fewer than %s shares" % fmt_qty(shares))
    else:
        raise TaxError("unknown method %r (fifo, hifo, specific)" % method)
    out, left = [], shares
    for l in order:
        if left <= EPS:
            break
        take = min(left, l["shares"])
        out.append((l, take))
        left -= take
    return out


def simulate_sale(lots, shares, price, sold, method="fifo", ids=None):
    used, res = [], {"proceeds": 0.0, "basis": 0.0, "short": 0.0, "long": 0.0}
    for l, take in pick_lots(lots, shares, method, ids):
        basis = l["per_share"] * take
        proceeds = price * take
        tm = term(l["date"], sold)
        used.append({"id": l["id"], "acquired": l["date"].isoformat(), "shares": round(take, 6),
                     "basis": round(basis, 2), "proceeds": round(proceeds, 2),
                     "gain": round(proceeds - basis, 2), "term": tm})
        res["proceeds"] += proceeds
        res["basis"] += basis
        res[tm] += proceeds - basis
    res = {k: round(v, 2) for k, v in res.items()}
    res["gain"] = round(res["proceeds"] - res["basis"], 2)
    res["lots"] = used
    return res


# ---------------------------------------------------------------- wash sales

def planned_buys(orders, prices):
    out = []
    for o in orders or []:
        if str(o.get("side", "")).lower() != "buy":
            continue
        t = str(o.get("symbol", "")).upper()
        sh = None
        if o.get("qty") is not None:
            sh = float(o["qty"])
        elif o.get("notional") is not None and prices.get(t):
            sh = float(o["notional"]) / prices[t]
        out.append({"ticker": t, "shares": sh})
    return out


def wash_check(ticker, sold, used, portfolio, orders, prices):
    """Purchases that would make a loss on this sale a wash sale.

    used: the lots consumed by the sale [{id, shares}]. Shares bought within
    30 days before or after the sale that you still hold after it count, and
    so do planned buys in trader/orders.json."""
    fam = family(ticker)
    sold_from = {u["id"]: u["shares"] for u in used}
    hits = []
    lo, hi = sold - datetime.timedelta(days=30), sold + datetime.timedelta(days=30)
    for t, lots in lots_of(portfolio).items():
        if t not in fam:
            continue
        for l in lots:
            keep = l["shares"] - sold_from.get(l["id"], 0.0)
            if keep > EPS and lo <= l["date"] <= hi:
                hits.append({"ticker": t, "date": l["date"].isoformat(), "shares": round(keep, 6),
                             "source": "lot %s you still hold" % l["id"]})
    for b in planned_buys(orders, prices):
        if b["ticker"] in fam:
            hits.append({"ticker": b["ticker"], "date": "planned", "shares": b["shares"],
                         "source": "a planned buy in trader/orders.json"})
    return hits


def disallowed(loss, shares_sold, hits):
    """Loss disallowed: proportional to replacement shares (all of it if unknown)."""
    if loss >= 0 or not hits:
        return 0.0
    repl = 0.0
    for h in hits:
        if h["shares"] is None:
            return round(-loss, 2)
        repl += h["shares"]
    return round(-loss * min(1.0, repl / shares_sold), 2) if shares_sold else 0.0


def recent_loss_sales(ticker, when, realized):
    fam = family(ticker)
    out = []
    for r in realized or []:
        if str(r.get("symbol", "")).upper() not in fam or float(r.get("gain", 0)) >= 0:
            continue
        d = day(r.get("date"))
        if d <= when <= d + datetime.timedelta(days=30):
            out.append(r)
    return out


def rebuy_date(sold):
    return (sold + datetime.timedelta(days=31)).isoformat()


# ---------------------------------------------------------------- harvest

def harvest(portfolio, prices, today, min_loss=100.0, min_pct=0.0, orders=None):
    cands = []
    for t, lots in sorted(lots_of(portfolio).items()):
        p = prices.get(t)
        if not p:
            continue
        losers = [l for l in lots if l["shares"] * p < l["cost"] - 0.005]
        if not losers:
            continue
        shares = sum(l["shares"] for l in losers)
        cost = sum(l["cost"] for l in losers)
        loss = shares * p - cost
        pct = 100.0 * loss / cost if cost else 0.0
        if -loss < min_loss or -pct < min_pct:
            continue
        sale = simulate_sale(lots, shares, p, today, "specific", [l["id"] for l in losers])
        hits = wash_check(t, today, sale["lots"], portfolio, orders, prices)
        cands.append({
            "ticker": t, "price": p, "shares": round(shares, 6), "lots": [l["id"] for l in losers],
            "all_lots": len(losers) == len(lots),
            "fifo_same": [l["id"] for l in lots[:len(losers)]] == [l["id"] for l in losers],
            "loss": round(loss, 2), "pct": round(pct, 2),
            "short": sale["short"], "long": sale["long"], "wash": hits,
            "disallowed": disallowed(loss, shares, hits), "rebuy_on_or_after": rebuy_date(today),
        })
    cands.sort(key=lambda c: c["loss"])
    return cands


def harvest_orders(cands, include_wash=False):
    out = []
    for c in cands:
        if c["wash"] and not include_wash:
            continue
        why = ("tax-loss harvest proposal: sell lots %s for an estimated %s loss (%s short term, %s long "
               "term). Do not buy %s again before %s (wash sale). Not tax advice."
               % (", ".join(c["lots"]), money(c["loss"]), money(c["short"]), money(c["long"]),
                  "/".join(sorted(family(c["ticker"]))), c["rebuy_on_or_after"]))
        if not c["fifo_same"]:
            why += (" These are not your oldest lots: tell your broker to sell these specific lots, "
                    "or it may sell by FIFO and realize a different result.")
        out.append({"symbol": c["ticker"], "side": "sell", "qty": fmt_qty(c["shares"]),
                    "type": "market", "time_in_force": "day", "why": why})
    return out


# ---------------------------------------------------------------- record a sale

def record_sale(portfolio, ticker, sale, sold, price, state):
    t = ticker.upper()
    taken = {u["id"]: u for u in sale["lots"]}
    for p in portfolio.get("positions", []):
        if str(p.get("ticker", "")).upper() != t:
            continue
        new = []
        for i, l in enumerate(p.get("lots", []), 1):
            lid = l.get("id") or "%s-%d" % (t, i)
            l["id"] = lid
            if lid in taken:
                sh = float(l["shares"])
                take = taken[lid]["shares"]
                l["cost"] = round(float(l["cost"]) * (1 - take / sh), 2) if sh else 0.0
                l["shares"] = round(sh - take, 6)
            if float(l["shares"]) > EPS:
                new.append(l)
        p["lots"] = new
        p["shares"] = round(sum(float(l["shares"]) for l in new), 6)
    portfolio["positions"] = [p for p in portfolio.get("positions", []) if float(p.get("shares", 0)) > EPS]
    portfolio["cash"] = round(float(portfolio.get("cash", 0)) + sale["proceeds"], 2)
    state.setdefault("realized", []).append({
        "date": sold.isoformat(), "symbol": t, "shares": round(sum(u["shares"] for u in sale["lots"]), 6),
        "price": price, "proceeds": sale["proceeds"], "basis": sale["basis"], "gain": sale["gain"],
        "short": sale["short"], "long": sale["long"], "wash_disallowed": sale.get("wash_disallowed", 0.0),
        "lots": sale["lots"]})
    return portfolio, state


def realized_ytd(state, year):
    out = {"short": 0.0, "long": 0.0, "disallowed": 0.0, "count": 0}
    for r in (state or {}).get("realized", []):
        if str(r.get("date", "")).startswith(str(year)):
            out["short"] += float(r.get("short", 0))
            out["long"] += float(r.get("long", 0))
            out["disallowed"] += float(r.get("wash_disallowed", 0))
            out["count"] += 1
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in out.items()}


# ---------------------------------------------------------------- CLI

NOT_ADVICE = ("Not tax advice: arithmetic on your lots. Wash-sale rules also count buys in your IRA, "
              "your spouse's accounts and other brokers, which this tool cannot see. Check with a tax "
              "professional before you act.")


def _inputs(a):
    portfolio = read_json(a.portfolio, "portfolio (lots)")
    pdoc = read_json(a.prices, "prices", required=False) or {}
    prices = {str(k).upper(): float(v) for k, v in (pdoc.get("prices", pdoc) or {}).items()
              if isinstance(v, (int, float))}
    state = read_json(a.state, "tax state", required=False) or {"realized": []}
    odoc = read_json(a.orders, "orders", required=False) or {}
    orders = odoc.get("orders", []) if isinstance(odoc, dict) else odoc
    today = day(a.today) if a.today else datetime.date.today()
    return portfolio, prices, state, orders, today


def cmd_report(a):
    portfolio, prices, state, orders, today = _inputs(a)
    rows, totals, missing = unrealized(portfolio, prices, today)
    if a.json:
        print(json.dumps({"lots": rows, "positions": totals, "missing_prices": missing,
                          "realized_ytd": realized_ytd(state, today.year)}, indent=2))
        return 0
    print("%-7s %10s %12s %12s %12s %8s" % ("ticker", "shares", "cost", "value", "gain", "gain %"))
    for t in totals:
        print("%-7s %10s %12s %12s %12s %7.2f%%" % (t["ticker"], fmt_qty(t["shares"]), money(t["cost"]),
                                                   money(t["value"]), money(t["gain"]), t["pct"]))
    if a.lots:
        print("")
        print("%-10s %-11s %10s %11s %11s %6s %s" % ("lot", "bought", "shares", "cost", "gain", "term", "long term from"))
        for r in rows:
            print("%-10s %-11s %10s %11s %11s %6s %s" % (r["id"], r["date"], fmt_qty(r["shares"]), money(r["cost"]),
                                                       money(r["gain"]), r["term"], r["long_term_on"]))
    for t in missing:
        print("No price for %s: add it to trader/prices.json (trader-alerts: alerts.py quote %s --write)" % (t, t))
    ytd = realized_ytd(state, today.year)
    print("")
    print("Realized in %d (recorded sales): %s short term, %s long term, %s disallowed by wash sales."
          % (today.year, money(ytd["short"]), money(ytd["long"]), money(ytd["disallowed"])))
    print(NOT_ADVICE)
    return 0


def cmd_sell(a):
    portfolio, prices, state, orders, today = _inputs(a)
    t = a.ticker.upper()
    lots = lots_of(portfolio).get(t)
    if not lots:
        raise TaxError("no lots for %s in %s" % (t, a.portfolio))
    price = a.price or prices.get(t)
    if not price:
        raise TaxError("no price for %s: pass --price" % t)
    ids = [i.strip() for i in (a.lots or "").split(",") if i.strip()]
    sale = simulate_sale(lots, float(a.shares), float(price), today, a.method, ids)
    hits = wash_check(t, today, sale["lots"], portfolio, orders, prices) if sale["gain"] < 0 else []
    sale["wash"] = hits
    sale["wash_disallowed"] = disallowed(sale["gain"], float(a.shares), hits)
    if a.json:
        print(json.dumps(sale, indent=2))
    else:
        print("Selling %s %s at %s on %s (%s):" % (fmt_qty(a.shares), t, money(price), today, a.method))
        for u in sale["lots"]:
            print("  lot %-8s bought %s  %s shares  basis %s  proceeds %s  %s (%s term)"
                  % (u["id"], u["acquired"], fmt_qty(u["shares"]), money(u["basis"]), money(u["proceeds"]),
                     money(u["gain"]), u["term"]))
        print("Total: proceeds %s, basis %s, %s %s (%s short term, %s long term)."
              % (money(sale["proceeds"]), money(sale["basis"]), "gain" if sale["gain"] >= 0 else "loss",
                 money(sale["gain"]), money(sale["short"]), money(sale["long"])))
        for h in hits:
            print("WASH SALE: %s (%s shares, %s)." % (h["source"], fmt_qty(h["shares"]) if h["shares"] else "?",
                                                        h["date"]))
        if hits:
            print("Up to %s of this loss would be disallowed and added to the replacement shares' basis."
                  % money(sale["wash_disallowed"]))
        elif sale["gain"] < 0:
            print("No wash-sale conflict found. Do not buy %s again before %s."
                  % ("/".join(sorted(family(t))), rebuy_date(today)))
        print(NOT_ADVICE)
    if a.record:
        portfolio, state = record_sale(portfolio, t, sale, today, float(price), state)
        write_json(a.portfolio, portfolio)
        write_json(a.state, state)
        if not a.json:
            print("Recorded: lots updated in %s, sale logged in %s." % (a.portfolio, a.state))
    return 0


def cmd_harvest(a):
    portfolio, prices, state, orders, today = _inputs(a)
    cands = harvest(portfolio, prices, today, a.min_loss, a.min_pct,
                    [o for o in orders if str(o.get("side", "")).lower() == "buy"])
    props = harvest_orders(cands, a.include_wash)
    if a.write and props:
        write_json(a.write, {
            "created_by": "trader-tax", "created_at": datetime.datetime.now().replace(microsecond=0).isoformat(),
            "status": "proposed",
            "note": ("Tax-loss harvesting proposals, not tax advice. Nothing is placed until trader-risk "
                     "passes each order and you say yes to it in the chat."),
            "orders": props})
    if a.json:
        print(json.dumps({"candidates": cands, "orders": props}, indent=2))
        return 0 if cands else 1
    if not cands:
        print("No lot has a loss of at least %s%s." % (money(a.min_loss),
                                                      (" and %s%%" % a.min_pct) if a.min_pct else ""))
        print(NOT_ADVICE)
        return 1
    for c in cands:
        print("%-6s sell %s shares (lots %s) at %s: est. loss %s (%.2f%%), %s short term, %s long term"
              % (c["ticker"], fmt_qty(c["shares"]), ", ".join(c["lots"]), money(c["price"]), money(c["loss"]),
                 c["pct"], money(c["short"]), money(c["long"])))
        for h in c["wash"]:
            print("       WASH SALE RISK: %s (%s)." % (h["source"], h["date"]))
        if c["wash"]:
            print("       Up to %s of the loss would be disallowed. Not proposed%s."
                  % (money(c["disallowed"]), "" if not a.include_wash else " unless --include-wash"))
        else:
            print("       Clear of wash sales in what this tool can see. Do not buy %s before %s."
                  % ("/".join(sorted(family(c["ticker"]))), c["rebuy_on_or_after"]))
    if a.write:
        print("Wrote %d proposed sell order(s) to %s. trader-risk checks them next." % (len(props), a.write)
              if props else "Nothing written: every candidate has a wash-sale conflict.")
    print(NOT_ADVICE)
    return 0


def cmd_wash(a):
    portfolio, prices, state, orders, today = _inputs(a)
    t = a.ticker.upper()
    when = day(a.date) if a.date else today
    hits = recent_loss_sales(t, when, state.get("realized", []))
    if not hits:
        print("No recorded loss sale of %s in the 30 days before %s. Buying it then is not a wash sale "
              "against anything this tool knows about." % ("/".join(sorted(family(t))), when))
        print(NOT_ADVICE)
        return 0
    for r in hits:
        print("WASH SALE: you sold %s %s at a %s loss on %s. Buying %s before %s would disallow that loss."
              % (fmt_qty(r["shares"]), r["symbol"], money(r["gain"]), r["date"], t, rebuy_date(day(r["date"]))))
    print(NOT_ADVICE)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cost-basis lots, gains, harvesting and wash sales (trader-tax). "
                                             "Not tax advice.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--prices", default="trader/prices.json")
        p.add_argument("--state", default="trader/state/tax.json")
        p.add_argument("--orders", default="trader/orders.json", help="planned buys count for wash sales")
        p.add_argument("--today", help="YYYY-MM-DD (default: today)")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("report", help="unrealized gains and losses")
    common(p)
    p.add_argument("--lots", action="store_true", help="show every lot")
    p = sub.add_parser("sell", help="what a sale would realize")
    common(p)
    p.add_argument("ticker")
    p.add_argument("shares", type=float)
    p.add_argument("--price", type=float)
    p.add_argument("--method", default="fifo", choices=["fifo", "hifo", "specific"])
    p.add_argument("--lots", help="lot ids for --method specific: KO-1,KO-2")
    p.add_argument("--record", action="store_true", help="the sale filled: update lots and log it")
    p = sub.add_parser("harvest", help="tax-loss harvesting candidates")
    common(p)
    p.add_argument("--min-loss", type=float, default=100.0, help="dollars (default 100)")
    p.add_argument("--min-pct", type=float, default=0.0, help="percent of cost (default 0)")
    p.add_argument("--include-wash", action="store_true", help="also propose candidates with a wash-sale conflict")
    p.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                   help="write proposals (default trader/orders.json)")
    p = sub.add_parser("wash", help="would buying this stock now be a wash sale?")
    common(p)
    p.add_argument("ticker")
    p.add_argument("--date", help="the planned buy date (default today)")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"report": cmd_report, "sell": cmd_sell, "harvest": cmd_harvest, "wash": cmd_wash}[a.cmd](a)
    except TaxError as e:
        print("lots.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
