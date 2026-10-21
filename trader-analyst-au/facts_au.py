#!/usr/bin/env python3
"""Earnings arithmetic for ASX companies (trader-analyst-au). Standard
library only, no network.

ASX companies do not file machine-readable financials the way US companies
file XBRL with the SEC, so the numbers come from the company's own published
results (Appendix 4D / 4E, the half-year or annual report, the results
release), found on the company's investor website and typed into a small
JSON file with the page each number came from. This tool then does every
calculation the same way each time and runs the forensic checks.

    python3 facts_au.py check trader/reports/CSL.json              # is the file complete and consistent?
    python3 facts_au.py analyse trader/reports/CSL.json --price 182.40
    python3 facts_au.py analyse trader/reports/*.json --metrics     # also update trader/metrics.json
    python3 facts_au.py template CSL > trader/reports/CSL.json

The report file (numbers in "units", default millions, of "currency"):

    {"code": "CSL", "name": "CSL Limited", "currency": "USD", "units": "m",
     "fx_to_aud": 1.52,                    # only if the currency is not AUD, for valuation
     "periods": [
       {"period": "FY2026", "type": "FY", "end": "2026-06-30",
        "revenue": ..., "gross_profit": ..., "ebitda": ..., "ebit": ...,
        "npat": ..., "npat_underlying": ..., "eps_cents": ..., "dps_cents": ...,
        "franking_pct": 100, "operating_cash_flow": ..., "capex": ...,
        "cash": ..., "debt": ..., "receivables": ..., "inventories": ...,
        "intangibles": ..., "equity": ..., "shares_m": ...,
        "sources": {"revenue": "Appendix 4E p.2", ...}, "url": "..."}]}

"npat" is statutory net profit after tax attributable to members; anything
"underlying" is the company's own adjusted figure. EPS and DPS are in cents
(of the reporting currency), shares in millions. Leave out what the report
does not give; never estimate a figure.
"""

import argparse
import datetime
import glob
import json
import os
import sys

FIELDS = ["revenue", "gross_profit", "ebitda", "ebit", "npat", "npat_underlying", "eps_cents", "dps_cents",
          "franking_pct", "operating_cash_flow", "capex", "cash", "debt", "receivables", "inventories",
          "intangibles", "equity", "shares_m"]
REQUIRED = ["revenue", "npat"]
CORPORATE_TAX = 0.30      # franking credits for large companies; 25 % for base rate entities (set "tax_rate")


class AnalystError(Exception):
    pass


def read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise AnalystError("no report file at %s" % path)
    except json.JSONDecodeError as e:
        raise AnalystError("%s is not valid JSON: %s" % (path, e))


def num(p, k):
    v = p.get(k)
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def pct(a, b):
    """Growth of a over b in %, None when b is missing or zero."""
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / abs(b), 1)


def ratio(a, b, scale=100.0, nd=1):
    if a is None or b in (None, 0):
        return None
    return round(scale * a / b, nd)


# ---------------------------------------------------------------- periods

def periods(doc):
    ps = [dict(p) for p in doc.get("periods", [])]
    for p in ps:
        p["type"] = str(p.get("type") or "").upper()
        if p["type"] not in ("FY", "HY"):
            raise AnalystError("period %s: type must be FY or HY" % p.get("period"))
        if not p.get("end"):
            raise AnalystError("period %s: needs an end date" % p.get("period"))
    return sorted(ps, key=lambda p: str(p["end"]))


def same_period_last_year(ps, p):
    end = datetime.date.fromisoformat(str(p["end"])[:10])
    for q in ps:
        if q["type"] != p["type"]:
            continue
        e = datetime.date.fromisoformat(str(q["end"])[:10])
        if 330 <= (end - e).days <= 400:
            return q
    return None


def derive_second_half(ps):
    """2H = full year minus the first half, marked derived."""
    out = []
    for fy in [p for p in ps if p["type"] == "FY"]:
        end = datetime.date.fromisoformat(str(fy["end"])[:10])
        h1 = None
        for p in ps:
            if p["type"] == "HY":
                e = datetime.date.fromisoformat(str(p["end"])[:10])
                if 150 <= (end - e).days <= 200:
                    h1 = p
        if not h1:
            continue
        h2 = {"period": "2H of " + str(fy.get("period")), "type": "2H", "end": fy["end"], "derived": True}
        for k in ("revenue", "gross_profit", "ebitda", "ebit", "npat", "npat_underlying", "operating_cash_flow",
                  "capex"):
            a, b = num(fy, k), num(h1, k)
            if a is not None and b is not None:
                h2[k] = round(a - b, 3)
        out.append(h2)
    return out


def ttm(ps, k):
    """Trailing twelve months: the latest FY, or latest HY + last FY - the HY before it."""
    if not ps:
        return None
    last = ps[-1]
    if last["type"] == "FY":
        return num(last, k)
    prev_hy = same_period_last_year(ps, last)
    fys = [p for p in ps if p["type"] == "FY" and str(p["end"]) < str(last["end"])]
    if not prev_hy or not fys:
        return None
    a, b, c = num(last, k), num(fys[-1], k), num(prev_hy, k)
    if None in (a, b, c):
        return None
    return round(a + b - c, 3)


# ---------------------------------------------------------------- analysis

def analyse(doc, price=None, tax_rate=None):
    ps = periods(doc)
    if not ps:
        raise AnalystError("%s: no periods" % doc.get("code"))
    cur = ps[-1]
    pcp = same_period_last_year(ps, cur)
    g = lambda k: num(cur, k)
    h = lambda k: num(pcp, k) if pcp else None
    tax_rate = float(tax_rate if tax_rate is not None else doc.get("tax_rate", CORPORATE_TAX))
    rows = {}

    rows["revenue_growth_yoy"] = pct(g("revenue"), h("revenue"))
    rows["npat_growth_yoy"] = pct(g("npat"), h("npat"))
    rows["underlying_npat_growth_yoy"] = pct(g("npat_underlying"), h("npat_underlying"))
    rows["eps_growth_yoy"] = pct(g("eps_cents"), h("eps_cents"))
    rows["dps_growth_yoy"] = pct(g("dps_cents"), h("dps_cents"))
    rows["gross_margin"] = ratio(g("gross_profit"), g("revenue"))
    rows["ebitda_margin"] = ratio(g("ebitda"), g("revenue"))
    rows["operating_margin"] = ratio(g("ebit"), g("revenue"))
    rows["net_margin"] = ratio(g("npat"), g("revenue"))
    rows["gross_margin_pcp"] = ratio(h("gross_profit"), h("revenue"))
    rows["operating_margin_pcp"] = ratio(h("ebit"), h("revenue"))
    rows["net_margin_pcp"] = ratio(h("npat"), h("revenue"))
    rows["cash_conversion"] = ratio(g("operating_cash_flow"), g("npat"))
    fcf = (g("operating_cash_flow") - g("capex")) if g("operating_cash_flow") is not None and g("capex") is not None else None
    rows["free_cash_flow"] = round(fcf, 3) if fcf is not None else None
    rows["fcf_margin"] = ratio(fcf, g("revenue"))
    nd = (g("debt") - g("cash")) if g("debt") is not None and g("cash") is not None else None
    rows["net_debt"] = round(nd, 3) if nd is not None else None
    ebitda_ttm = ttm(ps, "ebitda")
    rows["net_debt_ebitda"] = ratio(nd, ebitda_ttm, 1.0, 2) if nd is not None and ebitda_ttm else None
    rows["receivables_growth_yoy"] = pct(g("receivables"), h("receivables"))
    rows["inventory_growth_yoy"] = pct(g("inventories"), h("inventories"))
    rows["intangibles_growth_yoy"] = pct(g("intangibles"), h("intangibles"))
    rows["share_count_change_yoy"] = pct(g("shares_m"), h("shares_m"))
    rows["payout_ratio"] = ratio(g("dps_cents"), g("eps_cents"))
    rows["underlying_vs_statutory_gap"] = pct(g("npat_underlying"), g("npat"))
    npat_ttm = ttm(ps, "npat")
    rows["roe"] = ratio(npat_ttm, g("equity")) if npat_ttm is not None else None
    rows["revenue_ttm"] = ttm(ps, "revenue")
    rows["npat_ttm"] = npat_ttm
    eps_ttm_c = ttm(ps, "eps_cents")
    dps_ttm_c = ttm(ps, "dps_cents")
    fx = float(doc.get("fx_to_aud") or (1.0 if str(doc.get("currency", "AUD")).upper() == "AUD" else 0))
    rows["eps_ttm"] = round(eps_ttm_c / 100.0, 4) if eps_ttm_c is not None else None
    if price and fx:
        if eps_ttm_c and eps_ttm_c > 0:
            rows["pe"] = round(price / (eps_ttm_c / 100.0 * fx), 1)
            rows["earnings_yield"] = round(100.0 / rows["pe"], 2)
        if dps_ttm_c is not None:
            y = 100.0 * dps_ttm_c / 100.0 * fx / price
            rows["dividend_yield"] = round(y, 2)
            fr = num(cur, "franking_pct") or 0.0
            rows["grossed_up_yield"] = round(y * (1 + (fr / 100.0) * tax_rate / (1 - tax_rate)), 2)
        rows["price"] = price
    rows = {k: v for k, v in rows.items() if v is not None}
    return {"code": doc.get("code"), "name": doc.get("name", ""), "currency": doc.get("currency", "AUD"),
            "units": doc.get("units", "m"), "period": cur.get("period"), "type": cur["type"], "end": cur["end"],
            "pcp": pcp.get("period") if pcp else None, "metrics": rows, "flags": flags(ps, cur, pcp, rows),
            "second_halves": derive_second_half(ps), "missing": [k for k in FIELDS if num(cur, k) is None],
            "no_fx": bool(price) and not fx}


def flags(ps, cur, pcp, m):
    """Forensic checks: (level, finding). level: CONCERNING / WATCH / OK."""
    out = []
    rg, recg = m.get("revenue_growth_yoy"), m.get("receivables_growth_yoy")
    if rg is not None and recg is not None:
        if recg - rg > 10:
            out.append(("CONCERNING", "receivables grew %.1f%% against revenue %.1f%%: sales may be booked before "
                                      "cash comes in" % (recg, rg)))
        else:
            out.append(("OK", "receivables (%.1f%%) in line with revenue (%.1f%%)" % (recg, rg)))
    invg = m.get("inventory_growth_yoy")
    if rg is not None and invg is not None and invg - rg > 15:
        out.append(("WATCH", "inventories grew %.1f%% against revenue %.1f%%: stock building up" % (invg, rg)))
    cc = m.get("cash_conversion")
    if cc is not None:
        if cc < 80:
            out.append(("CONCERNING" if cc < 50 else "WATCH",
                        "operating cash flow is %.0f%% of profit: profit is not turning into cash" % cc))
        else:
            out.append(("OK", "operating cash flow is %.0f%% of profit" % cc))
    gap = m.get("underlying_vs_statutory_gap")
    if gap is not None and abs(gap) > 10:
        out.append(("WATCH", "underlying profit differs from statutory by %+.1f%%: read what was excluded" % gap))
    ups = 0
    seen = 0
    for p in ps[-3:]:
        a, b = num(p, "npat_underlying"), num(p, "npat")
        if a is not None and b is not None:
            seen += 1
            if a > b * 1.02:
                ups += 1
    if seen >= 2 and ups >= 2:
        out.append(("CONCERNING", "underlying profit beat statutory in %d of the last %d periods: 'one-off' costs "
                                  "that keep recurring" % (ups, seen)))
    ig = m.get("intangibles_growth_yoy")
    if rg is not None and ig is not None and ig - rg > 15:
        out.append(("WATCH", "intangibles grew %.1f%% against revenue %.1f%%: costs may be capitalised, or an "
                             "acquisition: check the notes" % (ig, rg)))
    dil = m.get("share_count_change_yoy")
    if dil is not None and dil > 5:
        out.append(("WATCH", "shares on issue up %.1f%% in a year: existing holders diluted" % dil))
    fcf = m.get("free_cash_flow")
    if fcf is not None and fcf < 0 and (num(cur, "dps_cents") or 0) > 0:
        out.append(("CONCERNING", "dividend paid while free cash flow was negative: funded by debt, cash or raising"))
    nde = m.get("net_debt_ebitda")
    if nde is not None and nde > 3:
        out.append(("WATCH", "net debt is %.1f times EBITDA" % nde))
    po = m.get("payout_ratio")
    if po is not None and po > 100:
        out.append(("WATCH", "paying out %.0f%% of earnings as dividends" % po))
    if pcp is None:
        out.append(("WATCH", "no same period last year in the file: growth cannot be checked"))
    return out


# ---------------------------------------------------------------- output

LABELS = [("revenue_growth_yoy", "Revenue growth vs same period last year", "%"),
          ("npat_growth_yoy", "Statutory profit (NPAT) growth", "%"),
          ("underlying_npat_growth_yoy", "Underlying profit growth (company's own measure)", "%"),
          ("eps_growth_yoy", "Earnings per share growth", "%"),
          ("gross_margin", "Gross margin", "%"), ("operating_margin", "EBIT (operating) margin", "%"),
          ("net_margin", "Net margin", "%"), ("cash_conversion", "Operating cash flow / NPAT", "%"),
          ("fcf_margin", "Free cash flow margin", "%"), ("net_debt_ebitda", "Net debt / EBITDA", "x"),
          ("roe", "Return on equity (TTM)", "%"), ("payout_ratio", "Dividend payout ratio", "%"),
          ("share_count_change_yoy", "Change in shares on issue", "%"),
          ("pe", "Price / earnings (TTM)", "x"), ("dividend_yield", "Dividend yield (TTM)", "%"),
          ("grossed_up_yield", "Grossed-up yield (with franking credits)", "%")]


def render(res):
    m = res["metrics"]
    lines = ["%s %s: %s %s (to %s) against %s. Figures in %s %s; growth and margins [DERIVED] from the "
             "company's reported numbers." % (res["code"], res["name"], res["type"], res["period"], res["end"],
                                             res["pcp"] or "no comparable period", res["currency"], res["units"])]
    for k, label, unit in LABELS:
        if k in m:
            v = m[k]
            extra = ""
            if k in ("gross_margin", "operating_margin", "net_margin") and (k + "_pcp") in m:
                extra = " (was %.1f%%)" % m[k + "_pcp"]
            lines.append("  %-50s %8s%s%s" % (label, ("%.1f" % v) if unit == "%" else ("%.2f" % v),
                                              "%" if unit == "%" else "x", extra))
    for h2 in res["second_halves"]:
        if h2.get("revenue") is not None:
            lines.append("  %s [DERIVED, full year minus first half]: revenue %s, NPAT %s"
                         % (h2["period"], h2.get("revenue"), h2.get("npat")))
    lines.append("")
    lines.append("Forensic checks:")
    for level, text in res["flags"]:
        lines.append("  %-10s %s" % (level, text))
    if res["missing"]:
        lines.append("")
        lines.append("Not in the file (not checked): %s" % ", ".join(res["missing"]))
    if res.get("no_fx"):
        lines.append("Valuation skipped: the report is not in AUD and the file has no fx_to_aud.")
    lines.append("")
    lines.append("Education and arithmetic, not financial advice.")
    return "\n".join(lines)


def check_file(doc):
    problems = []
    if not doc.get("code"):
        problems.append("no code")
    ps = periods(doc)
    if not ps:
        problems.append("no periods")
    for p in ps:
        for k in REQUIRED:
            if num(p, k) is None:
                problems.append("%s: no %s" % (p.get("period"), k))
        src = p.get("sources") or {}
        unsourced = [k for k in FIELDS if num(p, k) is not None and k not in src]
        if unsourced and not p.get("url"):
            problems.append("%s: no source page for %s" % (p.get("period"), ", ".join(unsourced)))
        if num(p, "gross_profit") is not None and num(p, "revenue") is not None and num(p, "gross_profit") > num(p, "revenue"):
            problems.append("%s: gross profit above revenue, check the units" % p.get("period"))
    if ps and same_period_last_year(ps, ps[-1]) is None:
        problems.append("no same period last year for %s: add it to measure growth" % ps[-1].get("period"))
    return problems


def template(code):
    blank = {k: None for k in FIELDS}
    blank.update({"period": "FY2026", "type": "FY", "end": "2026-06-30", "sources": {}, "url": ""})
    prev = dict(blank, period="FY2025", end="2025-06-30")
    return {"code": code.upper(), "name": "", "currency": "AUD", "units": "m", "periods": [prev, blank]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="ASX earnings arithmetic from company reports (trader-analyst-au).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("files", nargs="+")
    a = sub.add_parser("analyse"); a.add_argument("files", nargs="+")
    a.add_argument("--price", type=float, help="AUD share price (one file); else trader/prices.json")
    a.add_argument("--prices", default="trader/prices.json")
    a.add_argument("--metrics", nargs="?", const="trader/metrics.json", metavar="PATH",
                   help="merge the results into trader/metrics.json for trader-screener")
    a.add_argument("--json", action="store_true")
    t = sub.add_parser("template"); t.add_argument("code")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "template":
            print(json.dumps(template(args.code), indent=2))
            return 0
        files = []
        for f in args.files:
            files.extend(sorted(glob.glob(f)) or [f])
        if args.cmd == "check":
            bad = 0
            for f in files:
                probs = check_file(read(f))
                print("%s: %s" % (f, "OK" if not probs else "; ".join(probs)))
                bad += bool(probs)
            return 1 if bad else 0
        prices = {}
        if os.path.exists(args.prices):
            pd = read(args.prices)
            prices = {str(k).upper().replace(".AX", ""): v for k, v in (pd.get("prices", pd) or {}).items()
                      if isinstance(v, (int, float))}
        results = []
        for f in files:
            doc = read(f)
            price = args.price if (args.price and len(files) == 1) else prices.get(str(doc.get("code", "")).upper())
            results.append(analyse(doc, price))
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print("\n\n".join(render(r) for r in results))
        if args.metrics:
            old = read(args.metrics) if os.path.exists(args.metrics) else {}
            rows = {str(r.get("ticker")).upper(): r for r in (old.get("companies", []) if isinstance(old, dict) else old)}
            for r in results:
                row = {"ticker": r["code"], "name": r["name"], "period_end": r["end"],
                       "source": "company reports (%s %s)" % (r["type"], r["period"])}
                row.update(r["metrics"])
                rows[r["code"]] = row
            doc = {"as_of": datetime.date.today().isoformat(), "companies": list(rows.values())}
            d = os.path.dirname(args.metrics)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(args.metrics, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2)
                fh.write("\n")
            print("\nUpdated %s (%d companies) for trader-screener." % (args.metrics, len(rows)))
    except AnalystError as e:
        print("facts_au.py: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
