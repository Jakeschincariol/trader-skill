#!/usr/bin/env python3
"""Rebalancer for trader-rebalance. Standard library only, no network.

Compares what you hold (trader/portfolio.json at trader/prices.json) with
your target weights (trader/targets.json, capped by your rules) and proposes
the trades that bring every stock outside the band back to its target.

    python3 rebalance.py                      # report only
    python3 rebalance.py --band 5 --write     # write trader/orders.json (proposals)
    python3 rebalance.py --sell-untracked     # also sell holdings that are not in the targets
    python3 rebalance.py --json

The rules:
  value         account = cash + every holding at its price
  targets       weights renormalised to 100 over the stock sleeve, which is the
                account minus your cash buffer; no stock over max_position_pct
                of the account (the excess is shared out, like trader-budget)
  drift         current % of the account minus target %, in percentage points
  band          only stocks drifting MORE than the band trade (default 5
                points, or "rebalance_band_pct" in the config); a trade takes
                the stock all the way back to its target
  sells first   sells are listed before buys, so the cash is there
  cash buffer   buys only spend cash above the buffer; if there is not
                enough, every buy is scaled down by the same share
  min / max     trades under min_order_usd are skipped; a trade over
                max_order_usd is cut to the max (run it again after it fills)
  untracked     holdings not in the targets are left alone and listed, unless
                --sell-untracked

Exit code 0 when there are trades to propose, 1 when nothing is outside the band.
"""

import argparse
import datetime
import json
import math
import os
import sys

DEFAULTS = {"cash_buffer_pct": 10, "max_position_pct": 25, "max_order_usd": 2000, "min_order_usd": 5,
            "fractional": True, "blocked": [], "rebalance_band_pct": 5, "paper": True}
EPS = 1e-9


class RebalanceError(Exception):
    pass


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RebalanceError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RebalanceError("%s is not valid JSON: %s" % (path, e))


def money(x):
    x = float(x)
    sign = "-" if x < -0.004 else ""
    s = "{:,.2f}".format(abs(x))
    return sign + "$" + (s[:-3] if s.endswith(".00") else s)


def fmt_qty(x):
    s = ("%.4f" % float(x)).rstrip("0").rstrip(".")
    return s or "0"


def parse_targets(doc, blocked=()):
    rows = doc.get("weights", doc) if isinstance(doc, dict) else doc
    out = {}
    if isinstance(rows, dict):
        rows = [{"ticker": k, "weight": v} for k, v in rows.items()]
    blocked = {str(b).upper() for b in blocked or []}
    for r in rows or []:
        t = str(r.get("ticker") or r.get("symbol") or "").upper()
        w = float(r.get("weight", 0) or 0)
        if t and w > 0 and t not in blocked:
            out[t] = out.get(t, 0.0) + w
    tot = sum(out.values())
    if not tot:
        raise RebalanceError("no target weights (run trader-copycat, or write trader/targets.json)")
    return {t: 100.0 * w / tot for t, w in out.items()}


def capped_targets(weights, sleeve, cap):
    """Dollar target per ticker, proportional to weight, none above the cap."""
    alloc, active, money_left = {}, dict(weights), sleeve
    while active:
        wsum = sum(active.values())
        trial = {t: money_left * w / wsum for t, w in active.items()}
        over = [t for t, v in trial.items() if v > cap + EPS]
        if not over:
            alloc.update(trial)
            break
        for t in over:
            alloc[t] = cap
            money_left -= cap
            del active[t]
        if money_left <= EPS:
            for t in active:
                alloc[t] = 0.0
            break
    return alloc


def holdings_of(portfolio):
    out = {}
    for p in (portfolio or {}).get("positions", []):
        t = str(p.get("ticker") or p.get("symbol") or "").upper()
        if not t:
            continue
        n = p.get("shares", p.get("qty"))
        if n is None:
            n = sum(float(l.get("shares", 0)) for l in p.get("lots", []))
        out[t] = out.get(t, 0.0) + float(n)
    return out


def plan(portfolio, prices, weights, cfg, band=None, sell_untracked=False):
    """Pure function: the drift table and the proposed trades."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    band = float(c["rebalance_band_pct"] if band is None else band)
    cash = float((portfolio or {}).get("cash", 0) or 0)
    held = holdings_of(portfolio)
    need = sorted(set(held) | set(weights), key=lambda t: (-weights.get(t, 0.0), t))
    missing = [t for t in need if not prices.get(t)]
    if missing:
        raise RebalanceError("no price for %s: add to trader/prices.json (trader-alerts: alerts.py quote %s --write)"
                             % (", ".join(missing), " ".join(missing)))
    value = {t: held.get(t, 0.0) * prices[t] for t in need}
    total = cash + sum(value.values())
    if total <= 0:
        raise RebalanceError("the account is empty: nothing to rebalance (trader-budget sizes a first buy)")
    buf = total * float(c["cash_buffer_pct"]) / 100.0
    cap = total * float(c["max_position_pct"]) / 100.0
    target = capped_targets(weights, total - buf, cap)
    whole = not c.get("fractional", True)
    min_o, max_o = float(c.get("min_order_usd") or 0), float(c.get("max_order_usd") or 0)

    rows, sells, buys, notes = [], [], [], []
    for t in need:
        tracked = t in weights
        tgt = target.get(t, 0.0)
        cur_pct = 100.0 * value[t] / total
        tgt_pct = 100.0 * tgt / total
        drift = cur_pct - tgt_pct
        row = {"ticker": t, "shares": round(held.get(t, 0.0), 6), "price": prices[t], "value": round(value[t], 2),
               "current_pct": round(cur_pct, 2), "target_pct": round(tgt_pct, 2),
               "drift": round(drift, 2) if abs(drift) >= 0.005 else 0.0,
               "target_usd": round(tgt, 2), "tracked": tracked, "action": "hold"}
        rows.append(row)
        if not tracked:
            if sell_untracked and held.get(t, 0) > EPS:
                sells.append({"ticker": t, "usd": value[t], "qty": held[t], "all": True,
                              "why": "not in your targets; selling the whole position (--sell-untracked)"})
                row["action"] = "sell all"
            else:
                row["action"] = "untracked, left alone"
            continue
        if abs(drift) <= band + EPS:
            continue
        delta = tgt - value[t]
        why = "drifted %+.1f points from its %.1f%% target (band %s)" % (drift, tgt_pct, _n(band))
        if delta < 0:
            sells.append({"ticker": t, "usd": -delta, "qty": None, "all": False, "why": why})
            row["action"] = "sell"
        else:
            buys.append({"ticker": t, "usd": delta, "why": why})
            row["action"] = "buy"

    orders = []
    cash_after = cash
    for s in sells:
        p = prices[s["ticker"]]
        usd = s["usd"]
        if max_o and usd > max_o + EPS and not s["all"]:
            notes.append("%s sell cut to your %s max order; run again after it fills" % (s["ticker"], money(max_o)))
            usd = max_o
        if s["all"]:
            qty = s["qty"]
        else:
            qty = usd / p
            qty = math.floor(qty + EPS) if whole else math.floor(qty * 10000 + EPS) / 10000.0
            qty = min(qty, held[s["ticker"]])
        if qty <= EPS or qty * p < min_o - EPS:
            notes.append("%s sell of %s is under your %s minimum order: skipped"
                         % (s["ticker"], money(qty * p), money(min_o)))
            continue
        cash_after += qty * p
        orders.append({"symbol": s["ticker"], "side": "sell", "qty": fmt_qty(qty), "type": "market",
                       "time_in_force": "day", "why": s["why"], "_usd": round(qty * p, 2)})

    spendable = max(cash_after - buf, 0.0)
    want = sum(b["usd"] for b in buys)
    scale = 1.0
    if want > spendable + EPS:
        scale = spendable / want if want else 0.0
        notes.append("buys scaled to %.0f%%: only %s is free above your %s cash buffer"
                     % (100 * scale, money(spendable), money(buf)))
    for b in buys:
        p = prices[b["ticker"]]
        usd = b["usd"] * scale
        if max_o and usd > max_o + EPS:
            notes.append("%s buy cut to your %s max order; run again after it fills" % (b["ticker"], money(max_o)))
            usd = max_o
        if whole:
            qty = math.floor(usd / p + EPS)
            if qty < 1 or qty * p < min_o - EPS:
                notes.append("%s buy of %s does not reach one whole share or your minimum: skipped"
                             % (b["ticker"], money(usd)))
                continue
            o = {"symbol": b["ticker"], "side": "buy", "qty": str(int(qty)), "_usd": round(qty * p, 2)}
        else:
            usd = math.floor(usd + EPS)
            if usd < min_o - EPS or usd < 1:
                notes.append("%s buy of %s is under your %s minimum order: skipped" % (b["ticker"], money(usd), money(min_o)))
                continue
            o = {"symbol": b["ticker"], "side": "buy", "notional": str(int(usd)), "_usd": float(usd)}
        o.update({"type": "market", "time_in_force": "day", "why": b["why"]})
        orders.append(o)

    spent = sum(o["_usd"] for o in orders if o["side"] == "buy")
    return {"account_value": round(total, 2), "cash": round(cash, 2), "cash_buffer": round(buf, 2),
            "cap": round(cap, 2), "band": band, "rows": rows, "orders": orders, "notes": notes,
            "cash_after": round(cash_after - spent, 2)}


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def clean(orders):
    return [{k: v for k, v in o.items() if not k.startswith("_")} for o in orders]


def report(res):
    lines = ["Account %s (cash %s, buffer %s, cap %s per stock), band %s points"
             % (money(res["account_value"]), money(res["cash"]), money(res["cash_buffer"]), money(res["cap"]),
                _n(res["band"])), ""]
    lines.append("%-7s %10s %10s %11s %9s %9s %8s  %s" % ("ticker", "shares", "price", "value", "now %",
                                                        "target %", "drift", "action"))
    for r in res["rows"]:
        lines.append("%-7s %10s %10s %11s %8.2f%% %8.2f%% %+8.2f  %s"
                     % (r["ticker"], fmt_qty(r["shares"]), money(r["price"]), money(r["value"]),
                        r["current_pct"], r["target_pct"], r["drift"], r["action"]))
    lines.append("")
    if res["orders"]:
        lines.append("Proposed orders, sells first (place_stock_order arguments, nothing has been placed):")
        for o in res["orders"]:
            amt = ('notional "%s"' % o["notional"]) if "notional" in o else ('qty "%s"' % o["qty"])
            lines.append("  %s %s %s, %s, %s  (about %s)" % (o["side"], o["symbol"], amt, o["type"],
                                                             o["time_in_force"], money(o["_usd"])))
        lines.append("Cash after, if all of them fill: about %s." % money(res["cash_after"]))
    else:
        lines.append("Nothing is outside the band. No trades.")
    for n in res["notes"]:
        lines.append("Note: " + n)
    lines.append("")
    lines.append("Selling can realize a taxable gain: check trader-tax (lots.py sell) first. Then trader-risk "
                 "checks every order and trader-broker asks you yes or no for each one.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bring the portfolio back to its target weights (trader-rebalance).")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--targets", default="trader/targets.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--band", type=float, help="drift band in percentage points (default 5)")
    ap.add_argument("--sell-untracked", action="store_true", help="sell holdings that are not in the targets")
    ap.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                    help="write the proposals (default trader/orders.json)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = read_json(a.config, "config", required=False) or {}
        portfolio = read_json(a.portfolio, "portfolio (your holdings and cash)")
        pdoc = read_json(a.prices, "prices (trader-alerts: alerts.py quote --holdings --write)")
        prices = {str(k).upper(): float(v) for k, v in (pdoc.get("prices", pdoc) or {}).items()
                  if isinstance(v, (int, float))}
        weights = parse_targets(read_json(a.targets, "targets"), cfg.get("blocked"))
        res = plan(portfolio, prices, weights, cfg, a.band, a.sell_untracked)
        if a.write and res["orders"]:
            d = os.path.dirname(a.write)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(a.write, "w", encoding="utf-8") as fh:
                json.dump({"created_by": "trader-rebalance",
                           "created_at": datetime.datetime.now().replace(microsecond=0).isoformat(),
                           "status": "proposed", "paper": bool(cfg.get("paper", True)),
                           "note": ("Proposals only, sells first. Nothing is placed until trader-risk passes "
                                    "each order and you say yes to it in the chat."),
                           "prices_as_of": pdoc.get("as_of", ""), "orders": clean(res["orders"])}, fh, indent=2)
                fh.write("\n")
    except RebalanceError as e:
        print("rebalance.py: %s" % e, file=sys.stderr)
        return 1
    if a.json:
        out = dict(res)
        out["orders"] = clean(res["orders"])
        print(json.dumps(out, indent=2))
    else:
        print(report(res))
        if a.write and res["orders"]:
            print("Wrote %d proposed order(s) to %s." % (len(res["orders"]), a.write))
    return 0 if res["orders"] else 1


if __name__ == "__main__":
    sys.exit(main())
