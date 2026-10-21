#!/usr/bin/env python3
"""ASX announcement triage for trader-news-au. Standard library only, no
network: it never visits asx.com.au (whose terms bar robots and scrapers).

The announcements come from alert emails in the user's own Gmail, which
Claude reads with the Gmail connector; this tool builds the Gmail search,
then sorts what Claude found: what kind of announcement each one is, how much
it usually matters, which holdings it touches, and what is new since the last
check.

    python3 news_au.py query                         # the Gmail search for your holdings
    python3 news_au.py query --days 7 --codes BHP CSL
    python3 news_au.py triage items.json             # classify + new-only, newest first
    python3 news_au.py triage items.json --mark      # ... and remember them as shown
    python3 news_au.py kind "Change of Director's Interest Notice"

items.json is a list Claude writes from the emails:
    [{"id": "<gmail message id or link>", "title": "...", "code": "BHP",
      "date": "2026-10-21", "price_sensitive": true, "link": "...",
      "source": "Market Index alert"}]

Codes come from --codes, else trader/portfolio.json, else trader/targets.json.
Company names to search for come from "names" in trader/config.json
({"BHP": "BHP Group"}); add them, because many alerts carry the name, not
the code.
"""

import argparse
import datetime
import json
import os
import re
import sys

# (kind, importance 3 high / 2 medium / 1 low, why it matters, patterns)
KINDS = [
    ("halt", 3, "trading stopped or restarted: news is pending or just out",
     [r"trading halt", r"suspension from (official )?quotation", r"voluntary suspension", r"reinstatement to quotation"]),
    ("results", 3, "reported results: compare with last year and with guidance",
     [r"appendix 4[de]\b", r"half[- ]year (results|report|financial)", r"full[- ]year (results|report)",
      r"preliminary final report", r"annual report", r"\bresults? (announcement|presentation)", r"\b[hf]y\d{2} results"]),
    ("guidance", 3, "the company changed what it expects: often moves the price",
     [r"guidance", r"trading update", r"market update", r"earnings (update|downgrade|upgrade)", r"outlook"]),
    ("regulator", 3, "ASX asked the company to explain something",
     [r"price query", r"aware letter", r"response to asx", r"asx query", r"\bspeeding ticket\b"]),
    ("risk", 3, "possible loss of value: read it in full",
     [r"impairment", r"write[- ]?(down|off)", r"restatement", r"administrat(ion|or)", r"receivers?",
      r"going concern", r"delist", r"removal from (the )?official list", r"class action", r"material litigation"]),
    ("deal", 3, "a takeover, merger or big purchase or sale",
     [r"takeover", r"scheme (booklet|implementation|meeting)", r"bidder'?s statement", r"target'?s statement",
      r"\bmerger\b", r"acquisition", r"divestment", r"\bdisposal\b", r"binding (agreement|offer)", r"non-binding"]),
    ("capital", 3, "new shares being issued: can dilute existing holders",
     [r"placement", r"capital raising", r"entitlement offer", r"rights issue", r"share purchase plan", r"\bspp\b",
      r"proposed issue of securities", r"appendix 3b\b"]),
    ("director trade", 2, "a director bought or sold their own shares",
     [r"change of director'?s interest", r"appendix 3y\b", r"initial director'?s interest", r"appendix 3x\b",
      r"final director'?s interest", r"appendix 3z\b"]),
    ("substantial holder", 2, "a 5%+ holder bought or sold",
     [r"substantial holder", r"substantial holding", r"form 60[345]\b"]),
    ("people", 2, "a change at the top",
     [r"\bceo\b", r"\bcfo\b", r"managing director", r"chief executive", r"chief financial", r"resignation",
      r"appointment", r"retirement of", r"leadership"]),
    ("quarterly", 2, "the quarter's activity and cash",
     [r"quarterly (activities|cash ?flow|report|update)", r"appendix 4c\b", r"appendix 5b\b"]),
    ("dividend", 2, "dividend or distribution: amount and dates",
     [r"dividend", r"distribution", r"\bdrp\b"]),
    ("buy-back", 2, "the company is buying back its own shares",
     [r"buy[- ]?back", r"appendix 3[cefh]\b"]),
    ("meeting", 1, "meeting papers and votes",
     [r"annual general meeting", r"\bagm\b", r"\begm\b", r"notice of meeting", r"results of meeting",
      r"chair'?s address", r"proxy form"]),
    ("presentation", 1, "slides or a briefing: usually repeats what was released",
     [r"presentation", r"investor day", r"webcast", r"conference call"]),
    ("fund", 1, "routine fund figures (ETFs, LICs)",
     [r"daily fund update", r"net tangible asset", r"\bnta\b", r"\bnav\b", r"monthly (report|update)",
      r"portfolio holdings", r"units on issue"]),
    ("admin", 1, "routine paperwork",
     [r"application for quotation", r"appendix 2a\b", r"cleansing (notice|statement)", r"notification of (cessation|issue)",
      r"appendix 3g\b", r"change of (address|company secretary|registered office)"]),
]
COMPILED = [(k, imp, why, [re.compile(p, re.I) for p in pats]) for k, imp, why, pats in KINDS]


class NewsError(Exception):
    pass


def kind(title):
    """First matching kind, in the order above (halts and results win)."""
    t = title or ""
    for k, imp, why, pats in COMPILED:
        if any(p.search(t) for p in pats):
            return {"kind": k, "importance": imp, "why": why}
    return {"kind": "other", "importance": 1, "why": "read the title; not a standard type"}


def read_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise NewsError("%s is not valid JSON: %s" % (path, e))


def code(x):
    s = str(x or "").strip().upper()
    if s.startswith("ASX:"):
        s = s[4:]
    for suf in (".AX", ".ASX"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s


def my_codes(codes, portfolio, targets):
    if codes:
        return [code(c) for c in codes]
    out = []
    for p in (portfolio or {}).get("positions", []):
        if str(p.get("currency") or "AUD").upper() == "AUD":
            out.append(code(p.get("ticker") or p.get("symbol")))
    if not out and targets:
        w = targets.get("weights", targets)
        rows = [{"ticker": k} for k in w] if isinstance(w, dict) else w
        out = [code(r.get("ticker") or r.get("symbol")) for r in rows]
    return [c for c in dict.fromkeys(out) if c]


def gmail_query(codes, names=None, senders=None, days=2):
    """A Gmail search string: recent mail that names a code or company."""
    names = names or {}
    terms = []
    for c in codes:
        terms.append(c)
        if names.get(c):
            terms.append('"%s"' % names[c])
    q = "newer_than:%dd" % int(days)
    if senders:
        q += " from:(%s)" % " OR ".join(senders)
    if terms:
        q += " (%s)" % " OR ".join(terms)
    return q


def touches(item, codes, names):
    hit = set()
    c = code(item.get("code"))
    if c in codes:
        hit.add(c)
    text = " ".join(str(item.get(k) or "") for k in ("title", "summary"))
    for cc in codes:
        if re.search(r"\b%s\b" % re.escape(cc), text):
            hit.add(cc)
        n = names.get(cc)
        if n and n.lower() in text.lower():
            hit.add(cc)
    return sorted(hit)


def triage(items, codes, names, seen):
    out, skipped = [], 0
    for it in items:
        key = str(it.get("id") or it.get("link") or (it.get("code"), it.get("title"), it.get("date")))
        if key in seen:
            skipped += 1
            continue
        k = kind(it.get("title"))
        imp = k["importance"]
        if it.get("price_sensitive"):
            imp = max(imp, 3)
        row = dict(it)
        row.update({"key": key, "kind": k["kind"], "why": k["why"], "importance": imp,
                    "touches": touches(it, codes, names)})
        out.append(row)
    out.sort(key=lambda r: str(r.get("date") or ""), reverse=True)         # newest first ...
    out.sort(key=lambda r: (-r["importance"], 0 if r["touches"] else 1))   # ... within importance
    return out, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description="ASX announcement triage from Gmail alerts (trader-news-au).")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--targets", default="trader/targets.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--state", default="trader/state/news-au.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("query", help="print the Gmail search for your holdings")
    q.add_argument("--codes", nargs="*")
    q.add_argument("--days", type=int, default=2)
    t = sub.add_parser("triage", help="classify and sort the items Claude collected")
    t.add_argument("items")
    t.add_argument("--codes", nargs="*")
    t.add_argument("--mark", action="store_true", help="remember them so they are not shown again")
    t.add_argument("--json", action="store_true")
    k = sub.add_parser("kind", help="what kind of announcement a title is")
    k.add_argument("title")
    a = ap.parse_args(argv)
    try:
        cfg = read_json(a.config, {}) or {}
        names = {code(c): n for c, n in (cfg.get("names") or {}).items()}
        if a.cmd == "kind":
            r = kind(a.title)
            print("%s (importance %d): %s" % (r["kind"], r["importance"], r["why"]))
            return 0
        codes = my_codes(getattr(a, "codes", None), read_json(a.portfolio), read_json(a.targets))
        if a.cmd == "query":
            if not codes:
                raise NewsError("no codes: pass --codes, or import holdings into trader/portfolio.json")
            print(gmail_query(codes, names, cfg.get("news_senders"), a.days))
            return 0
        items = read_json(a.items)
        if not isinstance(items, list):
            raise NewsError("%s should be a JSON list of announcements" % a.items)
        state = read_json(a.state, {}) or {}
        seen = state.get("seen", {})
        rows, skipped = triage(items, codes, names, seen)
        if a.json:
            print(json.dumps({"items": rows, "already_shown": skipped}, indent=2))
        else:
            if not rows:
                print("Nothing new (%d already shown)." % skipped)
            for r in rows:
                flag = "!" if r["importance"] >= 3 else ("*" if r["importance"] == 2 else " ")
                print("%s %-10s %-6s %-18s %s" % (flag, str(r.get("date") or "")[:10], code(r.get("code")),
                                                 r["kind"], r.get("title", "")))
                print("    %s%s" % (r["why"], ("; touches " + ", ".join(r["touches"])) if r["touches"] else ""))
            if skipped and rows:
                print("(%d already shown before)" % skipped)
        if a.mark:
            now = datetime.date.today()
            for r in rows:
                seen[r["key"]] = now.isoformat()
            cutoff = (now - datetime.timedelta(days=60)).isoformat()
            state["seen"] = {k2: v for k2, v in seen.items() if v >= cutoff}
            d = os.path.dirname(a.state)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(a.state, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
                fh.write("\n")
    except NewsError as e:
        print("news_au.py: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
