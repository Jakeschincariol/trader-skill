#!/usr/bin/env python3
"""Pre-trade risk check for trader-risk. Standard library only, no network.

Reads the proposed orders (trader/orders.json) and checks every one against
your rules (trader/config.json), your account (trader/portfolio.json) and
prices (trader/prices.json). Each order gets PASS or BLOCK with the reasons.
Orders are checked in file order as if each PASS had filled, so five buys
that are fine one by one but break the cash buffer together are caught.

    python3 check.py                       # the trader/ files, writes trader/state/risk.json
    python3 check.py --orders trader/orders.json --json
    python3 check.py --no-write            # check without recording the verdicts

What blocks an order:

  format        missing symbol or side, both or neither of qty / notional,
                notional on anything but a market day order (Alpaca's rule),
                a limit order without a limit price, unknown type
  asset         options (OCC symbols) unless "allow_options": true; crypto
                and anything that is not a stock or ETF
  blocked       a ticker on your "blocked" list
  size          under min_order_usd, over max_order_usd
  position      the stock would be over max_position_pct of the account
  cash          the buy would dip into your cash buffer, or need margin
  short         selling more than you hold, unless "allow_short": true
  duplicate     the same symbol and side twice in this batch, or already
                placed today (trader/state/broker.json)
  daily limit   more than max_orders_per_day orders placed today
  trade risk    a buy whose loss down to its stop_loss is over
                max_risk_per_trade_pct of the account (when set); a buy with
                no stop when "require_stop": true

With "market": "asx" in the config (ASX shares through CMC Invest, AUD), also:

  whole shares  qty only, a whole number; no dollar (notional) orders
  ASX code      3-character codes pass; 4-6 characters only once you list
                them in "asx_allow_codes" (they can be options or warrants)
  price step    limit, stop, stop_loss and take_profit prices on the ASX tick
  parcel        a buy that leaves the holding under the $500 marketable parcel
  brokerage     CMC's fee is added to every buy's cost and taken off every
                sale; a fee over max_fee_pct of the order is a warning
  validity      day, gtc, or gtd with an expire_date
  warnings      CGT on a sale (gain, which lots get the 50 % discount, days
                until more do), a buy soon after a loss sale of the same code
                (ATO wash-sale ruling TR 2008/1), and buys that lean on sale
                proceeds that only settle T+2

Exit code 0 when every order passes, 1 when any order is blocked.
The verdicts go to trader/state/risk.json with a fingerprint of each order,
so trader-broker only shows an order as ready if THIS exact order passed today.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asx as A  # noqa: E402  (ships next to this file)

DEFAULTS = {
    "budget": None, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_usd": 2000,
    "min_order_usd": 5, "max_orders_per_day": 10, "paper": True, "blocked": [],
    "allow_options": False, "allow_margin": False, "allow_short": False,
    "max_fee_pct": 2.0, "max_risk_per_trade_pct": None, "require_stop": False, "asx_allow_codes": [],
}
ASX_TIFS = {"day", "gtc", "gtd"}
ALPACA_FIELDS = ("symbol", "side", "qty", "notional", "type", "time_in_force", "limit_price",
                 "stop_price", "trail_price", "trail_percent", "extended_hours", "client_order_id",
                 "order_class", "take_profit_limit_price", "stop_loss_stop_price",
                 "stop_loss_limit_price", "advanced_instructions")
TYPES = {"market", "limit", "stop", "stop_limit", "trailing_stop"}
TIFS = {"day", "gtc", "opg", "cls", "ioc", "fok"}
OCC = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
TICKER = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")
CENT = 0.005


class RiskError(Exception):
    pass


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def fingerprint(order):
    """Same function in trader-broker/ticket.py: the exact order that was checked."""
    core = {k: order[k] for k in ALPACA_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def money(x, cur="$"):
    return cur + "{:,.2f}".format(float(x)).replace(".00", "")


def account(cfg, portfolio, prices):
    """Starting account: cash and position values. No portfolio = a fresh
    account funded with the budget. In ASX mode, codes are normalised (BHP.AX
    -> BHP) and holdings in another currency (crypto, US shares) still count
    in the account value but cannot be traded here."""
    warnings = []
    asx = A.is_asx(cfg)
    cur = "A$" if asx else "$"
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("no trader/portfolio.json and no budget in the config: "
                            "nothing to check the orders against")
        return {"cash": budget, "shares": {}, "values": {}, "lots": {}, "foreign": set()}, warnings
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json needs a \"cash\" number")
    shares, values, lots, foreign = {}, {}, {}, set()
    for p in portfolio.get("positions", []):
        raw = p.get("ticker") or p.get("symbol") or ""
        t = A.code(raw) if asx else str(raw).upper()
        if not t:
            continue
        if asx and str(p.get("currency") or "AUD").upper() != "AUD":
            foreign.add(t)
        n = _f(p.get("shares", p.get("qty")))
        if n is None:
            n = sum(_f(l.get("shares")) or 0 for l in p.get("lots", []))
        shares[t] = shares.get(t, 0) + n
        lots.setdefault(t, []).extend(p.get("lots", []) or [])
        if prices.get(t):
            values[t] = shares[t] * prices[t]
        elif _f(p.get("value_aud")) and asx:
            values[t] = _f(p.get("value_aud"))
        else:
            basis = sum(_f(l.get("cost")) or 0 for l in p.get("lots", []))
            values[t] = basis
            warnings.append("no price for %s: valued at its cost basis %s" % (t, money(basis, cur)))
    return {"cash": cash, "shares": shares, "values": values, "lots": lots, "foreign": foreign}, warnings


def placed_today(state, today):
    """Orders recorded as placed today by trader-broker."""
    out = []
    for rec in (state or {}).get("orders", []):
        if rec.get("status") == "placed" and str(rec.get("date") or str(rec.get("at", ""))[:10]) == today:
            out.append(rec)
    return out


def check(orders, cfg, portfolio=None, prices=None, state=None, today=None, tax_state=None):
    """Pure function. Returns {"results": [...], "pass": n, "block": n, "warnings": [...]}."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    asx = A.is_asx(c)
    cur = "A$" if asx else "$"
    norm = A.code if asx else (lambda x: str(x or "").strip().upper())
    prices = {norm(k): float(v) for k, v in (prices or {}).items() if _f(v)}
    today = today or datetime.date.today().isoformat()
    acct, top_warn = account(c, portfolio, prices)
    blocked = {norm(b) for b in c.get("blocked") or []}
    done_today = placed_today(state, today)
    count_today = len(done_today)
    placed_keys = {(norm(r.get("symbol", "")), r.get("side")) for r in done_today}
    free_used = {k[0] for k in placed_keys if k[1] == "buy"}
    min_order = A.limit(c, "min_order", 0.0) or 0.0
    max_order = A.limit(c, "max_order")
    realized = (tax_state or {}).get("realized", [])
    settled = acct["cash"]          # cash that is already settled (ASX sales settle T+2)
    seen = set()
    results = []
    if asx:
        top_warn.append("CMC Invest: these are tickets you type into CMC yourself. They are real-money "
                        "orders; nothing here sends them anywhere.")
    elif not c.get("paper", True):
        top_warn.append("LIVE account (\"paper\": false): these orders use real money")

    for i, o in enumerate(orders, 1):
        reasons, warns = [], []
        sym = norm(o.get("symbol", ""))
        side = str(o.get("side", "")).strip().lower()
        typ = str(o.get("type", "market")).strip().lower()
        tif = str(o.get("time_in_force", "day")).strip().lower()
        qty, notional = o.get("qty"), o.get("notional")
        value = None
        fee = 0.0

        # format
        if not sym:
            reasons.append("format: no symbol")
        if side not in ("buy", "sell"):
            reasons.append("format: side must be buy or sell, got %r" % o.get("side"))
        if (qty is None) == (notional is None):
            reasons.append("format: give exactly one of qty or notional")
        for k in ("qty", "notional", "limit_price", "stop_price", "stop_loss", "take_profit"):
            if o.get(k) is not None:
                v = _f(o.get(k))
                if v is None or v <= 0:
                    reasons.append("format: %s must be a positive number, got %r" % (k, o.get(k)))
                elif not isinstance(o.get(k), str) and k in ("qty", "notional", "limit_price", "stop_price"):
                    warns.append("send %s as a string (\"%s\"), the way place_stock_order types it"
                                 % (k, o.get(k)))
        if typ not in TYPES:
            reasons.append("format: unknown order type %r" % typ)
        if asx:
            if tif not in ASX_TIFS:
                reasons.append("format: on CMC the validity is day, gtc or gtd (good till a date), got %r" % tif)
            if tif == "gtd":
                try:
                    if A.day(o.get("expire_date")) < A.day(today):
                        reasons.append("format: expire_date %s is in the past" % o.get("expire_date"))
                except (TypeError, ValueError):
                    reasons.append("format: a gtd order needs an expire_date (YYYY-MM-DD)")
            if notional is not None:
                reasons.append("format: ASX shares are bought in whole units on CMC; give qty, not a "
                               "dollar amount (notional)")
            if qty is not None and _f(qty) and abs(_f(qty) - round(_f(qty))) > 1e-9:
                reasons.append("format: qty %s is not a whole number of shares" % qty)
            for k in ("limit_price", "stop_price", "stop_loss", "take_profit"):
                v = _f(o.get(k))
                if v and not A.on_tick(v):
                    reasons.append("price step: %s %s is not on the ASX price step of %s (nearest below: %s)"
                                   % (k, o.get(k), _tick(v), A.round_tick(v)))
        else:
            if tif not in TIFS:
                reasons.append("format: unknown time_in_force %r" % tif)
            if notional is not None and (typ != "market" or tif != "day"):
                reasons.append("format: a notional (dollar) order must be a market order with time_in_force day")
        if typ in ("limit", "stop_limit") and o.get("limit_price") is None:
            reasons.append("format: a %s order needs a limit_price" % typ)
        if typ in ("stop", "stop_limit") and o.get("stop_price") is None:
            reasons.append("format: a %s order needs a stop_price" % typ)
        if o.get("extended_hours") and not (typ == "limit" and tif == "day"):
            reasons.append("format: extended hours needs a limit order with time_in_force day")
        if o.get("order_class") or o.get("advanced_instructions"):
            warns.append("advanced order (%s): check every leg before you say yes"
                         % (o.get("order_class") or "advanced_instructions"))

        # asset
        if asx:
            why = A.check_code(sym, c.get("asx_allow_codes")) if sym else None
            if why:
                reasons.append("asset: " + why)
            elif sym in acct["foreign"]:
                reasons.append("asset: your %s holding is not in AUD (crypto or an overseas listing); "
                               "it counts in the account value but is not traded here" % sym)
        elif sym and OCC.match(sym.replace(" ", "")):
            if not c.get("allow_options"):
                reasons.append("asset: %s is an option contract; options are off "
                               "(\"allow_options\": false)" % sym)
            else:
                warns.append("option order: options can lose 100%% of what you pay")
        elif sym and ("/" in sym or not TICKER.match(sym)):
            reasons.append("asset: %s does not look like a US stock or ETF ticker; "
                           "this pack trades stocks and ETFs only" % sym)

        # blocked list
        if sym in blocked:
            reasons.append("blocked: %s is on your blocked list" % sym)

        # dollar value of the order
        ref = None
        if typ in ("limit", "stop_limit"):
            ref = _f(o.get("limit_price"))
        elif typ == "stop":
            ref = _f(o.get("stop_price"))
        if ref is None:
            ref = prices.get(sym)
        if notional is not None and _f(notional):
            value = _f(notional)
        elif qty is not None and _f(qty):
            if ref:
                value = _f(qty) * ref
            else:
                reasons.append("price: no price for %s to size a %s-share order; add it to "
                               "trader/prices.json (%s)"
                               % (sym, qty, "cmc_import.py writes your holdings' last prices; add new codes "
                                  "by hand" if asx else "trader-alerts: alerts.py quote %s --write" % sym))

        if value is not None:
            if value < min_order - CENT and not (asx and side == "sell"):
                reasons.append("size: %s is under your %s minimum order"
                               % (money(value, cur), money(min_order, cur)))
            if max_order and value > max_order + CENT:
                reasons.append("size: %s is over your %s max order"
                               % (money(value, cur), money(max_order, cur)))
            if asx:
                fee = A.brokerage(value, side, c, free_used=sym in free_used)
                if fee and c.get("max_fee_pct") and 100.0 * fee / value > float(c["max_fee_pct"]) + 1e-9:
                    warns.append("brokerage about %s is %.1f%% of this %s order, over your %s%% fee limit"
                                 % (money(fee, cur), 100.0 * fee / value, money(value, cur),
                                    _pct(c["max_fee_pct"])))
                upto = A.free_tier(c)
                if side == "buy" and fee and upto and sym not in free_used and value > upto and value - upto < fee * 10:
                    warns.append("a buy of %s or less would be free (CMC's first buy of the day per stock); "
                                 "this one costs about %s" % (money(upto, cur), money(fee, cur)))

        # account effects, simulated in order
        cash = acct["cash"]
        held = acct["shares"].get(sym, 0.0)
        held_value = acct["values"].get(sym, 0.0)
        total = cash + sum(acct["values"].values())
        buffer_usd = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "buy":
            after_cash = cash - value - fee
            if after_cash < -CENT and not c.get("allow_margin"):
                reasons.append("cash: needs %s but only %s cash; that would be margin (borrowed money)"
                               % (money(value + fee, cur), money(cash, cur)))
            elif after_cash < buffer_usd - CENT:
                reasons.append("cash: leaves %s cash, under your %s%% buffer of %s"
                               % (money(max(after_cash, 0), cur), _pct(c["cash_buffer_pct"]),
                                  money(buffer_usd, cur)))
            elif asx and value + fee > settled + CENT:
                warns.append("this buy leans on money from a sale in this batch; ASX sales settle T+2 "
                             "(two business days), so check CMC lets you use it before then")
            pos_after = held_value + value
            limit_ = total * float(c.get("max_position_pct") or 100) / 100.0
            if total > 0 and pos_after > limit_ + CENT:
                reasons.append("position: %s would be %s, %.1f%% of the %s account, over your %s%% cap"
                               % (sym, money(pos_after, cur), 100.0 * pos_after / total, money(total, cur),
                                  _pct(c["max_position_pct"])))
            if asx and pos_after < A.MARKETABLE_PARCEL - CENT:
                reasons.append("parcel: %s of %s would be under the ASX $500 minimum holding; a first buy (or "
                               "a top-up of a holding under $500) must bring it to at least $500"
                               % (sym, money(pos_after, cur)))
            if asx:
                for r in A.recent_loss_sales(sym, today, realized):
                    warns.append("you sold %s at a loss on %s; buying it back soon after can be a wash sale "
                                 "(ATO TR 2008/1, no fixed window) and the loss can be cancelled"
                                 % (sym, r.get("date")))
            # trade risk: the loss if the stop is hit
            stop = _f(o.get("stop_loss", o.get("stop_loss_stop_price")))
            entry = ref or (value / _f(qty) if qty is not None and _f(qty) else None)
            n_sh = _f(qty) if qty is not None else (value / entry if entry else None)
            if stop and entry and n_sh:
                if stop >= entry - 1e-9:
                    reasons.append("stop: stop_loss %s is not below the entry price %s" % (stop, round(entry, 4)))
                else:
                    sell_fee = A.brokerage(stop * n_sh, "sell", c) if asx else 0.0
                    at_risk = (entry - stop) * n_sh + fee + sell_fee
                    pct = 100.0 * at_risk / total if total else 0.0
                    msg = "risk to the stop: %s (%.2f%% of the account)" % (money(at_risk, cur), pct)
                    tp = _f(o.get("take_profit", o.get("take_profit_limit_price")))
                    if tp:
                        if tp <= entry + 1e-9:
                            reasons.append("target: take_profit %s is not above the entry %s" % (tp, round(entry, 4)))
                        else:
                            msg += ", reward to risk %.1f : 1" % ((tp - entry) / (entry - stop))
                    cap_r = _f(c.get("max_risk_per_trade_pct"))
                    if cap_r and pct > cap_r + 1e-9:
                        reasons.append("trade risk: %s, over your %s%% per-trade risk limit (%s)"
                                       % (msg[len("risk to the stop: "):], _pct(cap_r),
                                          money(total * cap_r / 100.0, cur)))
                    else:
                        warns.append(msg)
            elif c.get("require_stop"):
                reasons.append("stop: no stop_loss on this buy and your rules require one (\"require_stop\": true)")
        if side == "sell" and sym:
            if qty is not None and _f(qty):
                over = _f(qty) > held + 1e-9
            elif value is not None:
                over = value > held_value + CENT
            else:
                over = held <= 0
            if over and not c.get("allow_short"):
                reasons.append("short: selling more %s than you hold (%s shares); short selling is off"
                               % (sym, _num(held)))
            if asx and value is not None and not over and held_value - value > CENT \
                    and held_value - value < A.MARKETABLE_PARCEL - CENT:
                warns.append("this sale leaves %s of %s, under the ASX $500 marketable parcel; any later buy "
                             "must bring it back to $500" % (money(held_value - value, cur), sym))
            if asx and value is not None and qty is not None and _f(qty) and not over:
                lots = acct["lots"].get(sym) or []
                if lots and ref:
                    tx = A.sell_preview(lots, _f(qty), ref, today, fee)
                    if tx["uncovered_shares"] > 1e-6:
                        warns.append("CGT: your lots for %s cover fewer shares than you hold; update the lots "
                                     "before trusting the estimate" % sym)
                    warns.append("CGT estimate (%s): gain %s, of which %s gets the 50%% discount; taxable about "
                                 "%s before netting other losses. Not tax advice."
                                 % (tx["fin_year"], money(tx["gain"], cur), money(tx["discounted_gain"], cur),
                                    money(tx["taxable"], cur)))
                    for w in tx["waits"]:
                        warns.append("CGT: lot %s becomes discount-eligible on %s (%d days); waiting would cut "
                                     "its taxable gain by about %s" % (w["id"], w["date"], w["days"],
                                                                     money(w["taxable_saved"], cur)))
                else:
                    warns.append("CGT: no cost base (lots) for %s in trader/portfolio.json, so the gain and the "
                                 "50%% discount cannot be estimated; add lots from your CMC contract notes" % sym)

        # duplicates and the daily limit
        key = (sym, side)
        if key in seen:
            reasons.append("duplicate: a second %s %s in this batch" % (side, sym))
        if key in placed_keys:
            reasons.append("duplicate: a %s %s was already placed today" % (side, sym))
        passed_so_far = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and count_today + passed_so_far >= cap_n:
            reasons.append("daily limit: %d placed today plus %d passed earlier in this batch, "
                           "your limit is %d a day" % (count_today, passed_so_far, cap_n))
        seen.add(key)

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS" and value is not None:
            if side == "buy":
                acct["cash"] -= value + fee
                settled -= value + fee
                free_used.add(sym)
                acct["values"][sym] = acct["values"].get(sym, 0.0) + value
                if ref:
                    acct["shares"][sym] = held + value / ref
            else:
                acct["cash"] += value - fee
                acct["values"][sym] = max(acct["values"].get(sym, 0.0) - value, 0.0)
                if qty is not None:
                    acct["shares"][sym] = held - _f(qty)
                elif ref:
                    acct["shares"][sym] = held - value / ref
        res = {
            "index": i, "symbol": sym, "side": side,
            "amount": money(_f(notional) or 0, cur) if notional is not None else ("%s shares" % qty),
            "value_usd": round(value, 2) if value is not None else None,
            "fingerprint": fingerprint(o), "verdict": verdict,
            "reasons": reasons, "warnings": warns,
        }
        if asx:
            res["value_aud"] = res.pop("value_usd")
            res["est_brokerage"] = fee
        results.append(res)

    return {
        "date": today,
        "market": "asx" if asx else "us",
        "currency": "AUD" if asx else "USD",
        "paper": bool(c.get("paper", True)),
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn,
        "cash_after": round(acct["cash"], 2),
    }


def _tick(price):
    t = A.tick(price)
    return "%gc" % (t * 100)


def _pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x)


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.4f" % x).rstrip("0")


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.append("WARNING: " + w)
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        lines.append("%-5s #%d %s %s %s" % (r["verdict"], r["index"], r["side"], r["symbol"], r["amount"]))
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    lines.append("")
    asx = res.get("market") == "asx"
    if asx:
        lines.append("%d passed, %d blocked. Cash after the passed orders, brokerage included: about %s."
                     % (res["pass"], res["block"], money(res["cash_after"], "A$")))
    else:
        lines.append("%d passed, %d blocked. Cash after the passed orders: %s (%s account)."
                     % (res["pass"], res["block"], money(res["cash_after"]), "paper" if res["paper"] else "LIVE"))
    if asx:
        lines.append("Blocked orders should not be entered in CMC. Fix the order or your rules, then check again."
                     if res["block"] else
                     "Every order passed. A PASS is a rules check, not a recommendation: you still decide each "
                     "one, and you place it yourself in CMC.")
    elif res["block"]:
        lines.append("Blocked orders do not go to the broker. Fix the order or your rules, then check again.")
    else:
        lines.append("Every order passed. trader-broker still asks you yes or no for each one.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check proposed orders against your limits (trader-risk).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; if missing, a fresh account funded with the budget")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--state", default="trader/state/broker.json", help="what trader-broker placed today")
    ap.add_argument("--tax-state", default="trader/state/tax.json",
                    help="recorded sales (trader-tax), for the ASX wash-sale warning")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--out", default="trader/state/risk.json", help="where the verdicts are recorded")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        doc = read_json(a.orders, "orders file (run trader-budget or trader-rebalance first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        cfg = read_json(a.config, "config", required=False) or {}
        portfolio = read_json(a.portfolio, "portfolio", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        prices = pdoc.get("prices", pdoc) if isinstance(pdoc, dict) else {}
        state = read_json(a.state, "broker state", required=False)
        tax_state = read_json(a.tax_state, "tax state", required=False)
        res = check(orders, cfg, portfolio, prices, state, a.today, tax_state)
        if pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = datetime.datetime.now().replace(microsecond=0).isoformat()
            rec["orders_file"] = a.orders
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2)
                fh.write("\n")
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
