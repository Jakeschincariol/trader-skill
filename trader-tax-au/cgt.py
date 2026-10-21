#!/usr/bin/env python3
"""Australian capital gains tax arithmetic for trader-tax-au. Standard
library only, no network. Estimates, not tax advice.

    python3 cgt.py buy VAS 10 --price 104.55 --date 2026-10-21 --fee 0     # record a parcel
    python3 cgt.py adjust VAS --lot VAS-1 --amount -3.20 --note "AMIT FY2025-26"
    python3 cgt.py report                                  # every parcel: gain, discount, date it qualifies
    python3 cgt.py sell VAS 10 --price 110                 # what a sale would realise (lowest tax first)
    python3 cgt.py sell VAS 10 --price 110 --method fifo --record
    python3 cgt.py year --fy FY2026-27 --carried 400       # net capital gain for the year
    python3 cgt.py harvest --min-loss 100 [--write]        # losses you could realise before 30 June
    python3 cgt.py waits --days 60                         # gains about to get the 50 % discount
    python3 cgt.py wash BHP                                # did you sell BHP at a loss recently?

The rules it applies (individual, Australian resident for tax; checked on
ato.gov.au 2026-10-21):

  parcel        each buy is its own parcel (lot); its cost base is what you
                paid plus brokerage, adjusted by AMIT cost base amounts from an
                ETF's annual tax statement (cgt.py adjust)
  CGT event     the trade (contract) date of the sale, not settlement
  discount      50 % off the gain on a parcel held at least 12 months,
                counting neither the day bought nor the day sold
  which parcels you may choose the parcels you sell if you can identify them;
                "min-tax" (default) picks losses first, then the smallest gain
                after the discount; fifo, lifo and specific are also offered
  the year      1 July to 30 June. Capital losses (this year's, then any carried
                forward) come off gains BEFORE the discount, and you may apply
                them to the gains that do not get the discount first, which
                gives the lowest result; the discount then halves what is left
                of the discountable gains. A net capital loss is not deducted
                from other income; it carries forward.
  wash sales    no fixed window (ATO TR 2008/1): selling at a loss and buying the
                same or a substantially the same asset back soon after, with no
                real change in exposure, can have the loss cancelled.

Not covered: non-residents and temporary residents, companies, trusts and
super funds, shares held as trading stock (a share trader), employee share
schemes, rights and options, takeovers with scrip-for-scrip rollover, and
foreign currency. Say so if any applies.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asx as A  # noqa: E402  (ships next to this file)

EPS = 1e-9


class CGTError(Exception):
    pass


# ---------------------------------------------------------------- files

def read_json(path, what, required=True, default=None):
    if not path or not os.path.exists(path):
        if required:
            raise CGTError("no %s at %s" % (what, path))
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise CGTError("%s is not valid JSON: %s" % (path, e))


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def aud(x):
    x = float(x)
    sign = "-" if x < -0.004 else ""
    s = "{:,.2f}".format(abs(x))
    return sign + "A$" + (s[:-3] if s.endswith(".00") else s)


def today():
    return datetime.date.today()


# ---------------------------------------------------------------- parcels

def position(portfolio, code):
    for p in portfolio.get("positions", []):
        if A.code(p.get("ticker") or p.get("symbol")) == code:
            return p
    return None


def effective_lots(pos):
    """Lots with AMIT and other cost base adjustments folded into "cost"."""
    out = []
    for i, l in enumerate((pos or {}).get("lots", []) or [], 1):
        adj = sum(float(a.get("amount") or 0) for a in l.get("adjustments", []) or [])
        e = dict(l)
        e["id"] = l.get("id") or "%s-%d" % (A.code(pos.get("ticker")), i)
        e["cost"] = float(l.get("cost") or 0) + adj
        out.append(e)
    return out


def add_lot(portfolio, code, shares, price, date, fee=0.0, lot_id=None):
    if shares <= 0 or price <= 0:
        raise CGTError("shares and price must be positive")
    pos = position(portfolio, code)
    if pos is None:
        pos = {"ticker": code, "shares": 0.0, "lots": []}
        portfolio.setdefault("positions", []).append(pos)
    lots = pos.setdefault("lots", [])
    lot_id = lot_id or "%s-%d" % (code, len(lots) + 1)
    if any(l.get("id") == lot_id for l in lots):
        raise CGTError("there is already a lot %s" % lot_id)
    lots.append({"id": lot_id, "date": A.day(date).isoformat(), "shares": shares,
                 "cost": round(shares * price + fee, 2)})
    pos["shares"] = round(sum(float(l["shares"]) for l in lots), 6)
    return lot_id


def ordered(lots, method, ids=None):
    if method == "fifo":
        return sorted(lots, key=lambda l: (str(l["date"]), l["id"]))
    if method == "lifo":
        return sorted(lots, key=lambda l: (str(l["date"]), l["id"]), reverse=True)
    if method == "specific":
        if not ids:
            raise CGTError("--method specific needs --lots ID,ID")
        by = {l["id"]: l for l in lots}
        missing = [i for i in ids if i not in by]
        if missing:
            raise CGTError("no lot %s" % ", ".join(missing))
        return [by[i] for i in ids]
    return lots


def sale(portfolio, code, shares, price, when, fee, method="min-tax", ids=None):
    pos = position(portfolio, code)
    if pos is None:
        raise CGTError("you hold no %s" % code)
    lots = effective_lots(pos)
    if not lots:
        raise CGTError("no lots (cost base) for %s: record them first with cgt.py buy, from your contract notes"
                       % code)
    held = sum(float(l["shares"]) for l in lots)
    if shares > held + EPS:
        raise CGTError("selling %g %s but your lots hold %g" % (shares, code, held))
    keep = method != "min-tax"
    tx = A.sell_preview(ordered(lots, method, ids), shares, price, when, fee, keep_order=keep)
    tx.update({"symbol": code, "shares": shares, "price": price, "fee": fee, "date": A.day(when).isoformat(),
               "method": method, "proceeds": round(shares * price - fee, 2)})
    return tx


def record(portfolio, state, tx):
    pos = position(portfolio, tx["symbol"])
    take = {l["id"]: l["take"] for l in tx["lots"]}
    new = []
    for i, l in enumerate(pos.get("lots", []), 1):
        lid = l.get("id") or "%s-%d" % (tx["symbol"], i)
        l["id"] = lid
        if lid in take:
            sh = float(l["shares"])
            frac = take[lid] / sh if sh else 1.0
            l["cost"] = round(float(l["cost"]) * (1 - frac), 2)
            for a in l.get("adjustments", []) or []:
                a["amount"] = round(float(a.get("amount") or 0) * (1 - frac), 2)
            l["shares"] = round(sh - take[lid], 6)
        if float(l["shares"]) > EPS:
            new.append(l)
    pos["lots"] = new
    pos["shares"] = round(sum(float(l["shares"]) for l in new), 6)
    if pos["shares"] <= EPS:
        portfolio["positions"] = [p for p in portfolio["positions"] if p is not pos]
    portfolio["cash"] = round(float(portfolio.get("cash") or 0) + tx["proceeds"], 2)
    state.setdefault("realized", []).append({
        "date": tx["date"], "fy": tx["fin_year"], "symbol": tx["symbol"], "shares": tx["shares"],
        "price": tx["price"], "fee": tx["fee"], "proceeds": tx["proceeds"], "gain": tx["gain"],
        "discounted_gain": tx["discounted_gain"], "undiscounted_gain": tx["undiscounted_gain"],
        "losses": tx["losses"], "method": tx["method"],
        "lots": [{"id": l["id"], "shares": l["take"], "gain": l["gain"], "discount": l["discount"]}
                 for l in tx["lots"]]})


# ---------------------------------------------------------------- the year

def net_capital_gain(disc, nondisc, losses, carried=0.0):
    """ATO method. disc / nondisc: gains (>= 0); losses: this year's capital
    losses (>= 0); carried: net capital losses from earlier years (>= 0).
    Losses go against the undiscounted gains first, then the discountable
    ones; the 50 % discount applies to what is left of the discountable gains."""
    pool = float(losses) + float(carried)
    used_nd = min(pool, nondisc)
    pool -= used_nd
    used_d = min(pool, disc)
    pool -= used_d
    left_nd = nondisc - used_nd
    left_d = disc - used_d
    return {"undiscounted_gains": round(nondisc, 2), "discountable_gains": round(disc, 2),
            "losses_this_year": round(losses, 2), "losses_carried_in": round(carried, 2),
            "losses_used": round(used_nd + used_d, 2), "discount": round(left_d / 2.0, 2),
            "net_capital_gain": round(left_nd + left_d / 2.0, 2), "loss_to_carry_forward": round(pool, 2)}


def year(state, fy, carried=0.0, extra=None):
    disc = nondisc = loss = 0.0
    n = 0
    for r in (state or {}).get("realized", []) + list(extra or []):
        if (r.get("fy") or A.fin_year(r["date"])) != fy:
            continue
        n += 1
        disc += float(r.get("discounted_gain") or 0)
        nondisc += float(r.get("undiscounted_gain") or 0)
        loss += -float(r.get("losses") or 0)
    out = net_capital_gain(disc, nondisc, loss, carried)
    out.update({"fy": fy, "sales": n})
    return out


def fy_end(fy):
    start = int(fy[2:6])
    return datetime.date(start + 1, 6, 30)


# ---------------------------------------------------------------- report, harvest, waits

def parcels(portfolio, prices, when):
    rows = []
    for p in portfolio.get("positions", []):
        code = A.code(p.get("ticker") or p.get("symbol"))
        if str(p.get("currency") or "AUD").upper() != "AUD":
            continue
        price = prices.get(code)
        for l in effective_lots(p):
            sh = float(l["shares"])
            r = {"symbol": code, "id": l["id"], "date": str(l["date"])[:10], "shares": sh,
                 "cost": round(l["cost"], 2), "price": price,
                 "discount": A.discount_ok(l["date"], when),
                 "discount_from": A.discount_from(l["date"]).isoformat()}
            if price:
                r["value"] = round(sh * price, 2)
                r["gain"] = round(sh * price - l["cost"], 2)
            rows.append(r)
    return rows


def harvest(portfolio, prices, when, state, min_loss=100.0, cfg=None):
    fy = A.fin_year(when)
    so_far = year(state, fy)
    out = []
    for r in parcels(portfolio, prices, when):
        if r.get("gain") is None or r["gain"] > -min_loss:
            continue
        fee = A.brokerage(r["value"], "sell", cfg)
        out.append(dict(r, est_fee=fee, loss_after_fee=round(r["gain"] - fee, 2)))
    out.sort(key=lambda r: r["loss_after_fee"])
    return {"fy": fy, "days_to_30_june": (fy_end(fy) - A.day(when)).days, "year_so_far": so_far,
            "candidates": out}


def waits(portfolio, prices, when, days=60):
    out = []
    for r in parcels(portfolio, prices, when):
        if r["discount"] or not r.get("gain") or r["gain"] <= 0:
            continue
        left = (A.day(r["discount_from"]) - A.day(when)).days
        if 0 < left <= days:
            out.append(dict(r, days=left, taxable_saved=round(r["gain"] / 2.0, 2)))
    return sorted(out, key=lambda r: r["days"])


# ---------------------------------------------------------------- printing

def print_sale(tx):
    print("Sale of %g %s at %s on %s (%s), brokerage %s, method %s"
          % (tx["shares"], tx["symbol"], aud(tx["price"]), tx["date"], tx["fin_year"], aud(tx["fee"]), tx["method"]))
    for l in tx["lots"]:
        print("  lot %-10s bought %s  %8g shares  cost %s a share  gain %s%s"
              % (l["id"], l["date"], l["take"], aud(l["cost_per_share"]), aud(l["gain"]),
                 "  (50% discount)" if l["discount"] and l["gain"] > 0 else ""))
    print("Gain %s: %s gets the discount, %s does not, %s losses. On its own: net capital gain about %s."
          % (aud(tx["gain"]), aud(tx["discounted_gain"]), aud(tx["undiscounted_gain"]), aud(tx["losses"]),
             aud(net_capital_gain(tx["discounted_gain"], tx["undiscounted_gain"], -tx["losses"])["net_capital_gain"])))
    for w in tx["waits"]:
        print("  lot %s qualifies for the discount on %s (%d days): waiting would cut its taxable gain by about %s"
              % (w["id"], w["date"], w["days"], aud(w["taxable_saved"])))


def print_year(y, rate=None):
    print("%s, %d recorded sale(s)" % (y["fy"], y["sales"]))
    print("  gains without the discount   %12s" % aud(y["undiscounted_gains"]))
    print("  gains eligible for discount  %12s" % aud(y["discountable_gains"]))
    print("  capital losses this year     %12s" % aud(y["losses_this_year"]))
    print("  losses carried in            %12s" % aud(y["losses_carried_in"]))
    print("  losses used (undiscounted gains first) %s" % aud(y["losses_used"]))
    print("  50%% discount                 %12s" % aud(-y["discount"]))
    print("  NET CAPITAL GAIN             %12s" % aud(y["net_capital_gain"]))
    if y["loss_to_carry_forward"]:
        print("  net capital loss to carry forward: %s (not deductible from other income)"
              % aud(y["loss_to_carry_forward"]))
    if rate:
        print("  at the %g%% marginal rate you gave: about %s of tax (your rate, not looked up; Medicare levy "
              "only if you included it)" % (rate, aud(y["net_capital_gain"] * rate / 100.0)))


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="Australian CGT arithmetic (trader-tax-au). Estimates, not tax advice.")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--state", default="trader/state/tax-au.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--date", help="YYYY-MM-DD (default today): the trade date")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("buy", help="record a parcel from a contract note")
    b.add_argument("code"); b.add_argument("shares", type=float)
    b.add_argument("--price", type=float, required=True); b.add_argument("--fee", type=float, default=0.0)
    b.add_argument("--id")
    j = sub.add_parser("adjust", help="cost base adjustment (AMIT) on a lot")
    j.add_argument("code"); j.add_argument("--lot", required=True)
    j.add_argument("--amount", type=float, required=True, help="+ raises the cost base, - lowers it")
    j.add_argument("--note", default="")
    sub.add_parser("report", help="every parcel with its gain and discount date")
    s = sub.add_parser("sell", help="what a sale would realise")
    s.add_argument("code"); s.add_argument("shares", type=float)
    s.add_argument("--price", type=float, help="default: trader/prices.json")
    s.add_argument("--fee", type=float, help="default: CMC brokerage estimate")
    s.add_argument("--method", default="min-tax", choices=["min-tax", "fifo", "lifo", "specific"])
    s.add_argument("--lots", help="lot ids for --method specific, in order")
    s.add_argument("--record", action="store_true", help="record the sale (after it was placed)")
    y = sub.add_parser("year", help="net capital gain for a financial year")
    y.add_argument("--fy", help="FY2026-27 (default: the current one)")
    y.add_argument("--carried", type=float, help="net capital losses carried in from earlier years")
    y.add_argument("--rate", type=float, help="your marginal rate in %%, for a rough tax figure")
    h = sub.add_parser("harvest", help="unrealised losses you could realise this year")
    h.add_argument("--min-loss", type=float, default=100.0)
    h.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                   help="write the sells as proposals for trader-risk")
    w = sub.add_parser("waits", help="gains about to qualify for the discount")
    w.add_argument("--days", type=int, default=60)
    x = sub.add_parser("wash", help="recent loss sales of a code")
    x.add_argument("code"); x.add_argument("--days", type=int, default=30)
    a = ap.parse_args(argv)

    try:
        when = A.day(a.date) if a.date else today()
        cfg = read_json(a.config, "config", required=False, default={}) or {}
        state = read_json(a.state, "tax state", required=False, default=None) or {"realized": []}
        pdoc = read_json(a.prices, "prices", required=False, default={}) or {}
        prices = {A.code(k): float(v) for k, v in (pdoc.get("prices", pdoc) or {}).items()
                  if isinstance(v, (int, float))}
        need_port = a.cmd != "year"
        portfolio = read_json(a.portfolio, "portfolio", required=need_port and a.cmd != "buy",
                              default=None) or {"cash": 0.0, "positions": []}

        if a.cmd == "buy":
            code = A.code(a.code)
            lid = add_lot(portfolio, code, a.shares, a.price, when, a.fee, a.id)
            write_json(a.portfolio, portfolio)
            print("Recorded %s: %g %s bought %s at %s plus %s brokerage = cost base %s."
                  % (lid, a.shares, code, when.isoformat(), aud(a.price), aud(a.fee), aud(a.shares * a.price + a.fee)))
            if not a.fee:
                print("Brokerage was 0: if your contract note shows a fee, record it, it is part of the cost base.")
            return 0

        if a.cmd == "adjust":
            pos = position(portfolio, A.code(a.code))
            lot = next((l for l in (pos or {}).get("lots", []) if l.get("id") == a.lot), None)
            if lot is None:
                raise CGTError("no lot %s for %s" % (a.lot, A.code(a.code)))
            lot.setdefault("adjustments", []).append({"amount": a.amount, "note": a.note, "date": when.isoformat()})
            write_json(a.portfolio, portfolio)
            print("Cost base of %s adjusted by %s (%s)." % (a.lot, aud(a.amount), a.note or "no note"))
            return 0

        if a.cmd == "report":
            rows = parcels(portfolio, prices, when)
            if not rows:
                print("No parcels recorded. Add them with cgt.py buy, from your CMC contract notes.")
                return 1
            print("Parcels on %s (%s). Prices as of %s." % (when.isoformat(), A.fin_year(when), pdoc.get("as_of", "?")))
            for r in rows:
                g = aud(r["gain"]) if "gain" in r else "no price"
                print("  %-6s %-10s bought %s %8g sh  cost %11s  gain %11s  %s"
                      % (r["symbol"], r["id"], r["date"], r["shares"], aud(r["cost"]), g,
                         "discount" if r["discount"] else "discount from " + r["discount_from"]))
            return 0

        if a.cmd == "sell":
            code = A.code(a.code)
            price = a.price or prices.get(code)
            if not price:
                raise CGTError("no price for %s: pass --price or add it to trader/prices.json" % code)
            fee = a.fee if a.fee is not None else A.brokerage(a.shares * price, "sell", cfg)
            tx = sale(portfolio, code, a.shares, price, when, fee, a.method,
                      [i.strip() for i in (a.lots or "").split(",") if i.strip()])
            print_sale(tx)
            if a.record:
                record(portfolio, state, tx)
                write_json(a.portfolio, portfolio)
                write_json(a.state, state)
                print("Recorded in %s; lots and cash updated in %s." % (a.state, a.portfolio))
            else:
                print("Not recorded. After the sale is placed, run again with --record.")
            return 0

        if a.cmd == "year":
            fy = a.fy or A.fin_year(when)
            carried = a.carried if a.carried is not None else float(
                (state.get("carried_forward") or {}).get(fy, 0) or 0)
            print_year(year(state, fy, carried), a.rate)
            return 0

        if a.cmd == "harvest":
            res = harvest(portfolio, prices, when, state, a.min_loss, cfg)
            ys = res["year_so_far"]
            print("%s: %d days to 30 June. Recorded so far: net capital gain %s."
                  % (res["fy"], res["days_to_30_june"], aud(ys["net_capital_gain"])))
            if not res["candidates"]:
                print("No parcel has an unrealised loss of %s or more." % aud(a.min_loss))
                return 1
            for r in res["candidates"]:
                print("  %-6s %-10s %8g sh  loss %s after about %s brokerage"
                      % (r["symbol"], r["id"], r["shares"], aud(r["loss_after_fee"]), aud(r["est_fee"])))
            print("A loss only saves tax against gains. Do not buy the same (or a substantially the same) "
                  "holding back soon after: the ATO can cancel the loss (TR 2008/1, no fixed window).")
            if a.write:
                orders = {}
                for r in res["candidates"]:
                    orders[r["symbol"]] = orders.get(r["symbol"], 0) + r["shares"]
                doc = {"created_by": "trader-tax-au", "status": "proposed", "market": "asx", "currency": "AUD",
                       "note": "Tax-loss sale proposals. Run trader-risk, decide each one, place it yourself in CMC.",
                       "orders": [{"symbol": t, "side": "sell", "qty": str(int(n)) if n == int(n) else str(n),
                                   "type": "market", "time_in_force": "day", "exchange": "ASX",
                                   "why": "realise an unrealised capital loss before 30 June"}
                                  for t, n in orders.items()]}
                write_json(a.write, doc)
                print("Wrote %d sell proposal(s) to %s." % (len(doc["orders"]), a.write))
            return 0

        if a.cmd == "waits":
            rows = waits(portfolio, prices, when, a.days)
            if not rows:
                print("No parcel with a gain qualifies for the discount in the next %d days." % a.days)
                return 1
            for r in rows:
                print("  %-6s %-10s gain %s qualifies on %s (%d days): selling after that cuts the taxable "
                      "gain by about %s" % (r["symbol"], r["id"], aud(r["gain"]), r["discount_from"], r["days"],
                                            aud(r["taxable_saved"])))
            return 0

        if a.cmd == "wash":
            code = A.code(a.code)
            hits = A.recent_loss_sales(code, when, state.get("realized"), a.days)
            if not hits:
                print("No recorded loss sale of %s in the last %d days. (The ATO sets no fixed window; this is "
                      "only what was recorded here.)" % (code, a.days))
                return 1
            for r in hits:
                print("You sold %s at a loss of %s on %s. Buying it back now could be a wash sale (TR 2008/1)."
                      % (code, aud(abs(float(r.get("gain") or 0))), r.get("date")))
            return 0
    except CGTError as e:
        print("cgt.py: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
