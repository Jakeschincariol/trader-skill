#!/usr/bin/env python3
"""Stock screener for trader-screener. Standard library only, no network.

Checks every company in a metrics file against your rules, says exactly why
each one passes or fails, and ranks the ones that pass.

    python3 screen.py trader/metrics.json --rules rules.example.json
    python3 screen.py trader/metrics.json --rules my-rules.json --prices trader/prices.json
    python3 screen.py companies.csv --rules my-rules.json --out trader/shortlist.json --json

Metrics: a JSON file ({"companies": [...]} as trader-analyst's facts.py
--metrics writes it, or a plain list of objects) or a CSV with a header row.
Each company needs a "ticker"; every other column is a metric. Numbers in a
CSV can carry $, % and thousands commas.

Prices (optional): trader/prices.json ({"prices": {"AAPL": 341.27}}). With
prices, the screener adds:
    price          the price you gave it
    pe             price / eps_ttm (only when eps_ttm is above zero)
    earnings_yield 100 * eps_ttm / price, in %

Rules (see rules.example.json):
    {"name": "...",
     "rules": [{"metric": "net_margin", "op": ">=", "value": 15}, ...],
     "rank":  [{"metric": "revenue_growth_yoy", "order": "desc", "weight": 2}, ...],
     "missing": "fail",            # or "skip": a rule on a missing metric is ignored
     "top": 10}
    ops: > >= < <= == != between (value [low, high]) in / not in (a list)

Ranking: among the companies that pass, each rank metric gives a score from 0
(worst) to 1 (best); the final score is their weighted average. A company
missing a rank metric is scored on the others and the gap is shown.

Exit code 0 when at least one company passes, 1 when none do, 2 on bad input.
A screen is a filter, not a recommendation.
"""

import argparse
import csv
import json
import os
import sys

OPS = (">", ">=", "<", "<=", "==", "!=", "between", "in", "not in")


class ScreenError(Exception):
    pass


# ---------------------------------------------------------------- inputs

def to_number(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if s == "" or s.lower() in ("na", "n/a", "none", "null", "-"):
        return None
    t = s.replace(",", "").replace("$", "").replace("%", "")
    try:
        return float(t)
    except ValueError:
        return s


def load_metrics(path):
    if not os.path.isfile(path):
        raise ScreenError("no such file: %s" % path)
    if path.lower().endswith(".csv"):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = [{(k or "").strip(): to_number(v) for k, v in r.items()}
                    for r in csv.DictReader(fh)]
    else:
        with open(path, encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except ValueError as exc:
                raise ScreenError("%s is not valid JSON: %s" % (path, exc))
        rows = data.get("companies", data.get("rows")) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ScreenError("%s: expected a list of companies or {\"companies\": [...]}" % path)
        rows = [dict(r) for r in rows]
    out = []
    for i, r in enumerate(rows):
        t = str(r.get("ticker") or r.get("symbol") or "").strip().upper()
        if not t:
            raise ScreenError("company #%d has no ticker" % (i + 1))
        r["ticker"] = t
        out.append(r)
    return out


def add_prices(rows, prices_path):
    with open(prices_path, encoding="utf-8") as fh:
        data = json.load(fh)
    prices = {k.upper(): v for k, v in (data.get("prices", data) or {}).items()}
    for r in rows:
        p = to_number(prices.get(r["ticker"]))
        if not isinstance(p, (int, float)) or p <= 0:
            continue
        r["price"] = p
        eps = to_number(r.get("eps_ttm"))
        if isinstance(eps, (int, float)):
            r["earnings_yield"] = round(100.0 * eps / p, 2)
            if eps > 0:
                r["pe"] = round(p / eps, 1)
    return data.get("as_of")


def load_rules(path):
    with open(path, encoding="utf-8") as fh:
        try:
            spec = json.load(fh)
        except ValueError as exc:
            raise ScreenError("%s is not valid JSON: %s" % (path, exc))
    check_rules(spec)
    return spec


def check_rules(spec):
    if not isinstance(spec, dict) or not isinstance(spec.get("rules", []), list):
        raise ScreenError("rules must be {\"rules\": [...]}")
    for i, r in enumerate(spec.get("rules", [])):
        if "metric" not in r or "op" not in r or "value" not in r:
            raise ScreenError("rule #%d needs metric, op and value: %r" % (i + 1, r))
        if r["op"] not in OPS:
            raise ScreenError("rule #%d: unknown op %r (use one of %s)" % (i + 1, r["op"],
                                                                          ", ".join(OPS)))
        if r["op"] == "between" and not (isinstance(r["value"], list) and len(r["value"]) == 2):
            raise ScreenError("rule #%d: between needs [low, high]" % (i + 1))
        if r["op"] in ("in", "not in") and not isinstance(r["value"], list):
            raise ScreenError("rule #%d: %s needs a list" % (i + 1, r["op"]))
    for i, k in enumerate(spec.get("rank", [])):
        if "metric" not in k or k.get("order", "desc") not in ("asc", "desc"):
            raise ScreenError("rank #%d needs a metric and order asc or desc" % (i + 1))
    if spec.get("missing", "fail") not in ("fail", "skip"):
        raise ScreenError("missing must be \"fail\" or \"skip\"")


# ---------------------------------------------------------------- screening

def _fmt(v):
    if isinstance(v, float):
        return ("%.2f" % v).rstrip("0").rstrip(".")
    if isinstance(v, list):
        return "[%s]" % ", ".join(_fmt(x) for x in v)
    return str(v)


def check(company, rule):
    """(passed, reason). passed is None when the metric is missing."""
    m, op, want = rule["metric"], rule["op"], rule["value"]
    have = company.get(m)
    if have is None or have == "":
        return None, "%s is missing" % m
    if op in ("in", "not in"):
        bag = [str(x).upper() for x in want]
        inside = str(have).upper() in bag
        ok = inside if op == "in" else not inside
        return ok, "%s %s %s %s" % (m, _fmt(have), "is in" if inside else "is not in", _fmt(want))
    if op in ("==", "!=") and not isinstance(have, (int, float)):
        ok = (str(have).upper() == str(want).upper()) == (op == "==")
        return ok, "%s %s %s %s" % (m, _fmt(have), op, _fmt(want))
    if not isinstance(have, (int, float)):
        return False, "%s is %r, not a number" % (m, have)
    if op == "between":
        lo, hi = want
        ok = lo <= have <= hi
        return ok, "%s %s %s %s to %s" % (m, _fmt(have), "is within" if ok else "is outside",
                                          _fmt(lo), _fmt(hi))
    ok = {">": have > want, ">=": have >= want, "<": have < want, "<=": have <= want,
          "==": have == want, "!=": have != want}[op]
    if ok:
        return True, "%s %s %s %s" % (m, _fmt(have), op, _fmt(want))
    return False, "%s %s, needs %s %s" % (m, _fmt(have), op, _fmt(want))


def screen(companies, spec):
    """Every company with pass/fail and reasons; passing ones ranked."""
    rules = spec.get("rules", [])
    missing = spec.get("missing", "fail")
    results = []
    for c in companies:
        fails, passes, gaps = [], [], []
        for r in rules:
            ok, why = check(c, r)
            if ok is None:
                (fails if missing == "fail" else gaps).append(why)
            elif ok:
                passes.append(why)
            else:
                fails.append(why)
        results.append({"ticker": c["ticker"], "name": c.get("name"), "passed": not fails,
                        "fails": fails, "passes": passes, "skipped": gaps, "metrics": c})
    rank(results, spec.get("rank", []))
    results.sort(key=lambda r: (not r["passed"], -(r.get("score") or 0), len(r["fails"]),
                                r["ticker"]))
    return results


def rank(results, keys):
    winners = [r for r in results if r["passed"]]
    for r in winners:
        r["score"], r["rank_parts"] = 0.0, {}
    if not keys or not winners:
        for r in winners:
            r["score"] = None
        return
    for k in keys:
        m, desc = k["metric"], k.get("order", "desc") == "desc"
        vals = [(r, r["metrics"].get(m)) for r in winners]
        vals = [(r, v) for r, v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not vals:
            continue
        ordered = sorted(v for _, v in vals)
        n = len(ordered)
        for r, v in vals:
            below = sum(1 for x in ordered if x < v)
            equal = sum(1 for x in ordered if x == v)
            pos = (below + (equal - 1) / 2.0) / (n - 1) if n > 1 else 1.0
            r["rank_parts"][m] = round(pos if desc else 1.0 - pos, 3)
    weights = {k["metric"]: float(k.get("weight", 1)) for k in keys}
    for r in winners:
        parts = r["rank_parts"]
        total = sum(weights[m] for m in parts)
        r["score"] = round(sum(parts[m] * weights[m] for m in parts) / total, 3) if total else None
        r["rank_gaps"] = [k["metric"] for k in keys if k["metric"] not in parts]


# ---------------------------------------------------------------- output

def render(results, spec, top=None, prices_as_of=None):
    name = spec.get("name") or "screen"
    passed = [r for r in results if r["passed"]]
    out = ["%s: %d of %d companies pass." % (name, len(passed), len(results))]
    if prices_as_of:
        out.append("Prices as of %s." % prices_as_of)
    out.append("")
    shown = passed[:top] if top else passed
    for i, r in enumerate(shown, 1):
        score = "" if r.get("score") is None else "  score %.2f" % r["score"]
        out.append("%2d. PASS %-6s %s%s" % (i, r["ticker"], (r.get("name") or "")[:32], score))
        for why in r["passes"]:
            out.append("        ok    %s" % why)
        for why in r["skipped"]:
            out.append("        skip  %s" % why)
        if r.get("rank_gaps"):
            out.append("        not ranked on: %s" % ", ".join(r["rank_gaps"]))
    if top and len(passed) > top:
        out.append("    ... %d more pass" % (len(passed) - top))
    failed = [r for r in results if not r["passed"]]
    if failed:
        out.append("")
        for r in failed:
            out.append("    FAIL %-6s %s" % (r["ticker"], "; ".join(r["fails"])))
    out.append("")
    out.append("A screen is a filter on reported numbers, not a recommendation. Read the "
               "filings (/trader-analyst) before anything goes near /trader-budget.")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("metrics", help="metrics JSON or CSV (trader/metrics.json)")
    ap.add_argument("--rules", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "rules.example.json"))
    ap.add_argument("--prices", help="trader/prices.json, adds price, pe and earnings_yield")
    ap.add_argument("--top", type=int, help="show only the N best that pass")
    ap.add_argument("--out", help="write the shortlist JSON here (trader/shortlist.json)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        spec = load_rules(args.rules)
        companies = load_metrics(args.metrics)
        prices_as_of = add_prices(companies, args.prices) if args.prices else None
    except (ScreenError, OSError) as exc:
        print("screen: %s" % exc, file=sys.stderr)
        return 2
    results = screen(companies, spec)
    top = args.top or spec.get("top")
    passed = [r for r in results if r["passed"]]
    shortlist = {
        "name": spec.get("name"), "rules": spec.get("rules", []), "rank": spec.get("rank", []),
        "prices_as_of": prices_as_of,
        "passed": [{"ticker": r["ticker"], "name": r.get("name"), "score": r.get("score"),
                    "why": r["passes"], "skipped": r["skipped"]}
                   for r in (passed[:top] if top else passed)],
        "failed": [{"ticker": r["ticker"], "why": r["fails"]} for r in results if not r["passed"]],
        "note": "A filter on reported numbers, not a recommendation.",
    }
    if args.json:
        print(json.dumps(shortlist, indent=2))
    else:
        sys.stdout.write(render(results, spec, top, prices_as_of))
    if args.out:
        folder = os.path.dirname(args.out)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(shortlist, fh, indent=2)
            fh.write("\n")
        if not args.json:
            print("wrote %s" % args.out)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
