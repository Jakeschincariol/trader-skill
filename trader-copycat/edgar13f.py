#!/usr/bin/env python3
"""13F reader for trader-copycat. Standard library only.

Reads an investor's public 13F-HR filings on SEC EDGAR and rebuilds the
portfolio as weights you can copy.

    python3 edgar13f.py find buffett                  # bundled list of well-known filers
    python3 edgar13f.py find "scion asset" --sec      # search every EDGAR filer by name
    python3 edgar13f.py filings 1067983               # its 13F-HR filings, newest first
    python3 edgar13f.py holdings 1067983 --combine    # latest quarter, share classes summed
    python3 edgar13f.py diff 1067983                  # new / sold out / added / trimmed
    python3 edgar13f.py copy 1067983 --top 5 --out trader/targets.json

    python3 edgar13f.py holdings --xml infotable.xml  # a file you downloaded yourself

Every call to SEC carries a User-Agent with your name and email, as SEC
requires (https://www.sec.gov/os/accessing-edgar-data). The tool reads it from
--user-agent, then the SEC_USER_AGENT environment variable, then
"sec_user_agent" in trader/config.json, and refuses to call SEC without one.
It waits between calls so it stays well under SEC's 10 requests a second.

What a 13F is, and is not:
  - It is filed once a quarter, up to 45 days after the quarter ends. The
    holdings are a snapshot of the last day of the quarter, so they can be
    four and a half months old by the time you read them.
  - It lists long positions in US-listed stocks (and some options and
    convertible bonds). It does not show shorts, cash, bonds, foreign stocks,
    private companies or anything bought and sold inside the quarter.
  - It is filed by the firm, not by the person the firm is known for.

Values: filings made from 2023-01-03 report value in dollars; older ones in
thousands. The tool converts older filings to dollars.

Weights: pct_of_13f is each position's value divided by the total value of
the whole information table. "copy" keeps the stock positions you pick and
scales their pct_of_13f (rounded to 2 decimals, the numbers you see in the
holdings table) so they sum to 100. Options rows (PUT/CALL) and bond rows
(PRN) are never copied: they are listed as excluded.

Offline: --offline DIR reads every URL from files in DIR instead of the
network (Archives files as DIR/<accession folder>/<file name>, everything else
as DIR/<file name>). The tests use it.
"""

import argparse
import datetime as _dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
FILERS = os.path.join(HERE, "filers.json")
CLASSES = os.path.join(HERE, "share_classes.json")

SUBMISSIONS = "https://data.sec.gov/submissions/CIK%s.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/%s/%s/"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEARCH_URL = "https://efts.sec.gov/LATEST/search-index?keysTyped=%s"
FIGI_URL = "https://api.openfigi.com/v3/mapping"
PLACEHOLDER = "you@example.com"
DOLLARS_FROM = _dt.date(2023, 1, 3)
FORMS = ("13F-HR", "13F-HR/A")

UA_HELP = (
    "SEC requires a User-Agent with your name and email on every request "
    "(https://www.sec.gov/os/accessing-edgar-data). Set it once, any of these:\n"
    "  1. add  \"sec_user_agent\": \"Your Name you@example.com\"  to trader/config.json "
    "(with YOUR name and email)\n"
    "  2. export SEC_USER_AGENT=\"Your Name you@example.com\"\n"
    "  3. pass --user-agent \"Your Name you@example.com\"")


class EdgarError(Exception):
    pass


# ---------------------------------------------------------------- fetching

def user_agent(cli=None, config_path="trader/config.json"):
    """The SEC User-Agent from the flag, the environment or the config, or None."""
    ua = (cli or os.environ.get("SEC_USER_AGENT") or "").strip()
    if not ua and config_path and os.path.isfile(config_path):
        try:
            with open(config_path, encoding="utf-8") as fh:
                ua = str(json.load(fh).get("sec_user_agent") or "").strip()
        except (OSError, ValueError):
            ua = ""
    if not ua or "@" not in ua or PLACEHOLDER in ua:
        return None
    return ua


class Fetcher(object):
    """GETs and POSTs with a User-Agent, gzip and a pause between calls."""

    def __init__(self, ua=None, offline=None, pause=0.15):
        self.ua = ua
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def local_path(self, url):
        parsed = urllib.parse.urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        if parsed.netloc == "efts.sec.gov":
            q = urllib.parse.parse_qs(parsed.query).get("keysTyped", [""])[0]
            name = "search-%s.json" % re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")
        elif "Archives" in parts and len(parts) >= 2:
            name = os.path.join(parts[-2], parts[-1])
        else:
            name = parts[-1] if parts else "index"
        return os.path.join(self.offline, name)

    def _wait(self):
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()

    def get(self, url, sec=True):
        if self.offline:
            path = self.local_path(url)
            if not os.path.isfile(path):
                raise EdgarError("offline: no file %s for %s" % (path, url))
            with open(path, "rb") as fh:
                return fh.read()
        if sec and not self.ua:
            raise EdgarError(UA_HELP)
        headers = {"User-Agent": self.ua or "trader-skill/1.0",
                   "Accept-Encoding": "gzip"}
        return self._open(urllib.request.Request(url, headers=headers), url)

    def post_json(self, url, payload, headers=None):
        if self.offline:
            path = os.path.join(self.offline, "openfigi.json")
            if not os.path.isfile(path):
                raise EdgarError("offline: no file %s for %s" % (path, url))
            with open(path, "rb") as fh:
                return json.loads(fh.read().decode("utf-8"))
        h = {"Content-Type": "application/json", "User-Agent": "trader-skill/1.0"}
        h.update(headers or {})
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=h, method="POST")
        return json.loads(self._open(req, url).decode("utf-8"))

    def _open(self, req, url):
        self._wait()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            if exc.code == 403 and "sec.gov" in url:
                raise EdgarError("SEC said 403 Forbidden for %s. Usually a missing or "
                                 "generic User-Agent, or too many requests. %s"
                                 % (url, UA_HELP))
            raise EdgarError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise EdgarError("could not reach %s: %s" % (url, exc.reason))

    def get_json(self, url, sec=True):
        try:
            return json.loads(self.get(url, sec=sec).decode("utf-8"))
        except ValueError as exc:
            raise EdgarError("%s did not return JSON: %s" % (url, exc))


# ---------------------------------------------------------------- filers

def cik10(value):
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        raise EdgarError("not a CIK: %r" % value)
    return digits.zfill(10)


def load_filers(path=FILERS):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["filers"]


def find_bundled(query, filers=None):
    """Bundled filers whose name, person or alias contains every word of query."""
    filers = load_filers() if filers is None else filers
    q = query.lower().strip()
    if re.fullmatch(r"\d{1,10}", q):
        return [f for f in filers if f["cik"] == cik10(q)]
    words = q.split()
    hits = []
    for f in filers:
        hay = " ".join([f["name"], f.get("person", "")] + f.get("aliases", [])).lower()
        if all(w in hay for w in words):
            hits.append(f)
    return hits


def search_sec(fetcher, query):
    """Every EDGAR filer whose name matches (the EDGAR company search box)."""
    data = fetcher.get_json(SEARCH_URL % urllib.parse.quote(query))
    out = []
    for hit in data.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        out.append({"cik": cik10(hit.get("_id", "0")), "name": src.get("entity", "")})
    return out


# ---------------------------------------------------------------- filings

def list_filings(submissions, forms=FORMS):
    """13F filings from a submissions JSON, newest filing first."""
    rec = submissions.get("filings", {}).get("recent", {})
    out = []
    for i, form in enumerate(rec.get("form", [])):
        if form not in forms:
            continue
        out.append({
            "form": form,
            "accession": rec["accessionNumber"][i],
            "filed": rec["filingDate"][i],
            "period": rec.get("reportDate", [""] * (i + 1))[i],
            "primary": rec.get("primaryDocument", [""] * (i + 1))[i],
        })
    out.sort(key=lambda f: (f["filed"], f["accession"]), reverse=True)
    return out


def pick_filing(filings, period=None, accession=None, before=None):
    """The original 13F-HR for a period (default: the newest), plus any amendments."""
    if accession:
        hit = [f for f in filings if f["accession"] == accession]
        if not hit:
            raise EdgarError("accession %s is not in this filer's 13F list" % accession)
        chosen = hit[0]
    else:
        originals = [f for f in filings if f["form"] == "13F-HR"]
        if period:
            originals = [f for f in originals if f["period"] == period]
        if before:
            originals = [f for f in originals if f["period"] < before]
        if not originals:
            raise EdgarError("no 13F-HR found%s" % (" for period %s" % period if period else ""))
        originals.sort(key=lambda f: (f["period"], f["filed"]), reverse=True)
        chosen = originals[0]
    amendments = [f for f in filings if f["form"] == "13F-HR/A"
                  and f["period"] == chosen["period"] and f is not chosen]
    return chosen, amendments


def archive_url(cik, accession):
    return ARCHIVE % (int(cik10(cik)), accession.replace("-", ""))


def info_table_name(index_json):
    """The information table XML inside a filing folder (index.json)."""
    names = [it["name"] for it in index_json.get("directory", {}).get("item", [])]
    xmls = [n for n in names if n.lower().endswith(".xml")
            and n.lower() not in ("primary_doc.xml", "filingsummary.xml")]
    if not xmls:
        raise EdgarError("no information table XML in this filing (files: %s). Filings "
                         "before mid 2013 are plain text and are not supported."
                         % ", ".join(names))
    for n in xmls:
        if re.search(r"info|table|13f", n, re.I):
            return n
    return xmls[0]


def value_multiplier(filed):
    """1 for dollars (filed on or after 2023-01-03), 1000 for older filings."""
    try:
        d = _dt.datetime.strptime(filed[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return 1
    return 1 if d >= DOLLARS_FROM else 1000


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_info_table(data, multiplier=1):
    """Rows of a 13F information table XML, as dicts. Values in dollars."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise EdgarError("information table is not valid XML: %s" % exc)
    rows = []
    for el in root.iter():
        if _local(el.tag) != "infoTable":
            continue
        f = {}
        for child in el.iter():
            name = _local(child.tag)
            if name not in f and child.text and child.text.strip():
                f[name] = child.text.strip()
        try:
            value = float(f.get("value", "0").replace(",", "")) * multiplier
            shares = float(f.get("sshPrnamt", "0").replace(",", ""))
        except ValueError:
            raise EdgarError("bad number in row for %s" % f.get("nameOfIssuer"))
        rows.append({
            "issuer": f.get("nameOfIssuer", ""),
            "title": f.get("titleOfClass", ""),
            "cusip": f.get("cusip", "").upper(),
            "figi": f.get("figi", ""),
            "value": value,
            "shares": shares,
            "share_type": f.get("sshPrnamtType", "SH").upper(),
            "put_call": f.get("putCall", "").upper(),
            "manager": f.get("otherManager", ""),
        })
    if not rows:
        raise EdgarError("no infoTable rows found. Is this a 13F information table?")
    return rows


def parse_cover(data):
    """tableValueTotal, tableEntryTotal and the cover facts from primary_doc.xml."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}
    want = {"tableValueTotal", "tableEntryTotal", "periodOfReport", "isAmendment",
            "amendmentType", "reportType", "otherIncludedManagersCount", "name"}
    out = {}
    for el in root.iter():
        name = _local(el.tag)
        if name in want and name not in out and el.text and el.text.strip():
            out[name] = el.text.strip()
    return out


def is_stock(p):
    return p["share_type"] == "SH" and not p["put_call"]


def aggregate(rows):
    """One position per CUSIP (and per PUT/CALL), summing the manager split rows."""
    total = sum(r["value"] for r in rows)
    groups = {}
    order = []
    for r in rows:
        key = (r["cusip"], r["put_call"], r["share_type"])
        if key not in groups:
            groups[key] = {"issuer": r["issuer"], "title": r["title"], "cusip": r["cusip"],
                           "figi": r["figi"], "put_call": r["put_call"],
                           "share_type": r["share_type"], "value": 0.0, "shares": 0.0,
                           "rows": 0}
            order.append(key)
        g = groups[key]
        g["value"] += r["value"]
        g["shares"] += r["shares"]
        g["rows"] += 1
    positions = [groups[k] for k in order]
    for p in positions:
        p["pct_of_13f"] = round(100.0 * p["value"] / total, 2) if total else 0.0
        p["cusips"] = [p["cusip"]]
    positions.sort(key=lambda p: (-p["value"], p["cusip"]))
    return positions, total


def combine_classes(positions, total):
    """Sum the share classes of one issuer (same 6-character CUSIP issuer code).

    Only stock rows are combined. The combined position keeps the ticker and
    name of its biggest class and lists every CUSIP and ticker it holds."""
    out, by_issuer = [], {}
    for p in positions:
        if not is_stock(p):
            out.append(dict(p))
            continue
        key = p["cusip"][:6]
        if key not in by_issuer:
            q = dict(p)
            q["cusips"] = [p["cusip"]]
            q["tickers"] = [p.get("ticker")] if p.get("ticker") else []
            q["classes"] = [p["title"]]
            by_issuer[key] = q
            out.append(q)
            continue
        q = by_issuer[key]
        q["value"] += p["value"]
        q["shares"] += p["shares"]
        q["rows"] += p["rows"]
        q["cusips"].append(p["cusip"])
        q["classes"].append(p["title"])
        if p.get("ticker"):
            q["tickers"].append(p["ticker"])
    for q in out:
        q["pct_of_13f"] = round(100.0 * q["value"] / total, 2) if total else 0.0
    out.sort(key=lambda p: (-p["value"], p["cusip"]))
    return out


# ---------------------------------------------------------------- tickers

SUFFIX_STATE = re.compile(r"\s*/\s*[A-Z]{2,5}\s*/?\s*$")
ABBREV = {
    "AMER": "AMERICA", "AMERN": "AMERICAN", "FINL": "FINANCIAL", "FIN": "FINANCIAL",
    "PETE": "PETROLEUM", "HLDGS": "HOLDINGS", "HLDG": "HOLDING", "HOLDG": "HOLDING",
    "INTL": "INTERNATIONAL", "GRP": "GROUP", "SVCS": "SERVICES", "SVC": "SERVICE",
    "SYS": "SYSTEMS", "MGMT": "MANAGEMENT", "INDS": "INDUSTRIES", "NATL": "NATIONAL",
    "ENTMT": "ENTERTAINMENT", "PAC": "PACIFIC", "LABS": "LABORATORIES",
    "PPTYS": "PROPERTIES", "RLTY": "REALTY", "TECHS": "TECHNOLOGIES", "TECH": "TECHNOLOGY",
    "COMMNS": "COMMUNICATIONS", "COMMUNICATN": "COMMUNICATIONS", "MFG": "MANUFACTURING",
    "ELEC": "ELECTRIC", "PHARMACEUTICALS": "PHARMACEUTICALS", "PHARMS": "PHARMACEUTICALS",
    "BANCORPORATION": "BANCORP", "RES": "RESOURCES", "SOLUTIONS": "SOLUTIONS",
    "WTR": "WATER", "MTRS": "MOTORS", "AIRLS": "AIRLINES", "CHEM": "CHEMICAL",
}
DROP = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED",
        "PLC", "LLC", "LP", "SA", "NV", "AG", "SE", "THE", "AND", "OF"}
NONCOMMON = re.compile(r"-P[A-Z]*$|-WT[A-Z]*$|-WS$|-U$|-UN$|-R$|-RT$")


def norm_name(name):
    s = (name or "").upper().replace("&", " AND ")
    for _ in range(2):
        s = SUFFIX_STATE.sub("", s)
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    words = [ABBREV.get(w, w) for w in s.split()]
    return [w for w in words if w not in DROP]


def name_keys(name):
    words = norm_name(name)
    return "".join(words), " ".join(sorted(words))


def norm_ticker(t):
    t = (t or "").strip().upper()
    return re.sub(r"[-/ ]([A-Z]{1,2})$", r".\1", t) if not NONCOMMON.search(t) else t


def class_letter(title):
    m = re.search(r"\b(?:CL|CLASS|SER|SERIES)\s*([A-Z])\b", (title or "").upper())
    return m.group(1) if m else None


def load_company_tickers(data):
    """company_tickers.json -> ordered list of (cik10, ticker, title)."""
    rows = []
    items = data.values() if isinstance(data, dict) else data
    for v in items:
        rows.append((cik10(v["cik_str"]), v["ticker"], v["title"]))
    return rows


class TickerIndex(object):
    """Name lookups over SEC's company_tickers.json."""

    def __init__(self, rows):
        self.by_cik = {}
        self.titles = {}
        self.spaceless = {}
        self.sorted_words = {}
        for cik, ticker, title in rows:
            self.by_cik.setdefault(cik, []).append(ticker)
            if cik in self.titles:
                continue
            self.titles[cik] = title
            a, b = name_keys(title)
            if a:
                self.spaceless.setdefault(a, set()).add(cik)
                self.sorted_words.setdefault(b, set()).add(cik)

    def match_cik(self, issuer):
        a, b = name_keys(issuer)
        if not a:
            return None, None
        for hits, how in ((self.spaceless.get(a), "name"), (self.sorted_words.get(b), "name")):
            if hits and len(hits) == 1:
                return next(iter(hits)), how
        # The 13F truncates or decorates names ("JEFFERIES FINANCIAL GROUP IN").
        best = []
        for key, ciks in self.spaceless.items():
            if len(key) >= 8 and len(ciks) == 1 and (a.startswith(key) or key.startswith(a)):
                best.append((len(key), next(iter(ciks))))
        if best:
            best.sort(reverse=True)
            if len(best) == 1 or best[0][0] > best[1][0]:
                return best[0][1], "name-prefix"
        return None, None

    def ticker_for(self, cik, title_of_class):
        """(ticker, needs_check) for a company and a 13F titleOfClass."""
        tickers = self.by_cik.get(cik, [])
        cands = [t for t in tickers if not NONCOMMON.search(t)] or tickers
        if not cands:
            return None, True
        if len(cands) == 1:
            return norm_ticker(cands[0]), False
        if all(t.startswith(cands[0]) for t in cands[1:]):
            return norm_ticker(cands[0]), False
        letter = class_letter(title_of_class)
        if letter:
            for t in cands:
                if t.upper().endswith(("-" + letter, "." + letter, "/" + letter)):
                    return norm_ticker(t), False
            for t in cands:
                if len(t) >= 4 and t.upper().endswith(letter):
                    return norm_ticker(t), True
        return norm_ticker(cands[0]), True


def load_overrides(paths):
    out = {}
    for path in paths:
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            table = data.get("cusips", data) if isinstance(data, dict) else {}
            for k, v in table.items():
                if re.fullmatch(r"[0-9A-Z]{9}", str(k).upper()):
                    out[str(k).upper()] = norm_ticker(v)
    return out


def figi_lookup(fetcher, cusips, api_key=None, batch=None, pause=2.5):
    """CUSIP -> ticker through OpenFIGI (US composite). Free, no key needed:
    10 CUSIPs a request and 25 requests a minute without a key."""
    batch = batch or (100 if api_key else 10)
    headers = {"X-OPENFIGI-APIKEY": api_key} if api_key else {}
    out = {}
    cusips = list(dict.fromkeys(cusips))
    for i in range(0, len(cusips), batch):
        chunk = cusips[i:i + batch]
        jobs = [{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in chunk]
        if i and not fetcher.offline:
            time.sleep(pause)
        answer = fetcher.post_json(FIGI_URL, jobs, headers=headers)
        out.update(parse_figi(chunk, answer))
    return out


def parse_figi(cusips, answer):
    out = {}
    for c, res in zip(cusips, answer):
        data = (res or {}).get("data") or []
        stocks = [d for d in data if d.get("marketSector") == "Equity"] or data
        if stocks and stocks[0].get("ticker"):
            out[c] = norm_ticker(stocks[0]["ticker"])
    return out


def pretty_name(issuer, sec_title=None):
    name = sec_title if sec_title and re.search(r"[a-z]", sec_title) else (sec_title or issuer)
    name = SUFFIX_STATE.sub("", name or "")
    name = re.sub(r",?\s+(Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?|Company|Ltd\.?|"
                  r"Limited|plc|PLC|LLC|L\.P\.|N\.V\.|S\.A\.)$", "", name.strip(), flags=re.I)
    name = re.sub(r",?\s+(INC|CORP|CO|LTD|PLC)$", "", name.strip())
    if name.isupper():
        small = {"OF", "AND", "THE", "FOR", "IN"}
        words = name.lower().split()
        name = " ".join(w if (w.upper() in small and i) else w.capitalize()
                        for i, w in enumerate(words))
    return name.strip()


def resolve_tickers(positions, index=None, overrides=None, figi=None):
    """Give every position a ticker, where it came from and whether to check it."""
    overrides = overrides or {}
    figi = figi or {}
    for p in positions:
        c = p["cusip"]
        sec_title = None
        cik = None
        if index is not None:
            cik, how = index.match_cik(p["issuer"])
            if cik:
                sec_title = index.titles.get(cik)
                p["issuer_cik"] = cik
        if c in overrides:
            p["ticker"], p["ticker_source"], p["ticker_check"] = overrides[c], "override", False
        elif c in figi:
            p["ticker"], p["ticker_source"], p["ticker_check"] = figi[c], "openfigi", False
        elif cik:
            t, check = index.ticker_for(cik, p["title"])
            p["ticker"], p["ticker_source"] = t, how
            p["ticker_check"] = bool(check or how == "name-prefix")
        else:
            p["ticker"], p["ticker_source"], p["ticker_check"] = None, "none", True
        p["name"] = pretty_name(p["issuer"], sec_title)
    return positions


# ---------------------------------------------------------------- analysis

def diff(current, prior):
    """Compare two quarters by CUSIP: NEW, SOLD OUT, ADDED, TRIMMED, UNCHANGED."""
    before = {(p["cusip"], p["put_call"]): p for p in prior}
    now = {(p["cusip"], p["put_call"]): p for p in current}
    out = []
    for key, p in now.items():
        q = before.get(key)
        if q is None:
            status, change = "NEW", p["shares"]
        elif p["shares"] > q["shares"]:
            status, change = "ADDED", p["shares"] - q["shares"]
        elif p["shares"] < q["shares"]:
            status, change = "TRIMMED", p["shares"] - q["shares"]
        else:
            status, change = "UNCHANGED", 0.0
        out.append({"status": status, "issuer": p["issuer"], "title": p["title"],
                    "cusip": p["cusip"], "ticker": p.get("ticker"), "put_call": p["put_call"],
                    "shares_before": q["shares"] if q else 0.0, "shares_after": p["shares"],
                    "change": change,
                    "change_pct": (round(100.0 * change / q["shares"], 1)
                                   if q and q["shares"] else None),
                    "value_after": p["value"], "value_before": q["value"] if q else 0.0})
    for key, q in before.items():
        if key not in now:
            out.append({"status": "SOLD OUT", "issuer": q["issuer"], "title": q["title"],
                        "cusip": q["cusip"], "ticker": q.get("ticker"),
                        "put_call": q["put_call"], "shares_before": q["shares"],
                        "shares_after": 0.0, "change": -q["shares"], "change_pct": -100.0,
                        "value_after": 0.0, "value_before": q["value"]})
    rank = {"NEW": 0, "SOLD OUT": 1, "ADDED": 2, "TRIMMED": 3, "UNCHANGED": 4}
    out.sort(key=lambda d: (rank[d["status"]], -max(d["value_after"], d["value_before"])))
    return out


def copy_weights(positions, top=None, min_pct=0.0, exclude=(), need_ticker=True):
    """Pick the stock positions to copy and scale them to 100.

    Returns (weights, skipped). Weights are pct_of_13f scaled to sum to 100,
    rounded to 2 decimals."""
    exclude = {e.upper() for e in exclude}
    chosen, skipped = [], []
    for p in positions:
        why = None
        if not is_stock(p):
            why = "%s row, not a stock you can copy" % (p["put_call"] or p["share_type"])
        elif need_ticker and not p.get("ticker"):
            why = "no ticker found (add it to trader/tickers.json or run with --figi)"
        elif p.get("ticker") and p["ticker"].upper() in exclude:
            why = "excluded by you"
        elif p["pct_of_13f"] < min_pct:
            why = "below --min-pct %.2f" % min_pct
        if why:
            skipped.append({"issuer": p["issuer"], "ticker": p.get("ticker"), "why": why})
            continue
        chosen.append(p)
    if top:
        for p in chosen[top:]:
            skipped.append({"issuer": p["issuer"], "ticker": p.get("ticker"),
                            "why": "outside the top %d" % top})
        chosen = chosen[:top]
    base = sum(p["pct_of_13f"] for p in chosen)
    if not chosen or base <= 0:
        raise EdgarError("nothing left to copy")
    weights = []
    for p in chosen:
        w = {"ticker": p["ticker"], "name": p.get("name") or p["issuer"],
             "weight": round(100.0 * p["pct_of_13f"] / base, 2),
             "pct_of_13f": p["pct_of_13f"], "value": p["value"], "shares": p["shares"],
             "cusips": p.get("cusips", [p["cusip"]])}
        if p.get("tickers") and len(p["tickers"]) > 1:
            w["classes"] = p["tickers"]
        if p.get("ticker_check"):
            w["check_ticker"] = True
        weights.append(w)
    return weights, skipped


CAVEATS = [
    "A 13F is a snapshot of the last day of the quarter, filed up to 45 days later. "
    "The investor may have sold or added since.",
    "It shows long positions in US-listed stocks only: no shorts, cash, bonds, foreign "
    "stocks, private companies or trades made inside the quarter.",
    "It is filed by the firm, not by the person it is known for.",
    "Education and tooling, not financial advice. Nothing here places an order.",
]


# ---------------------------------------------------------------- output

def _money(v):
    return "${:,.0f}".format(v)


def _shares(v):
    return "{:,.0f}".format(v) if float(v).is_integer() else "{:,.2f}".format(v)


def render_holdings(meta, positions, total, top=None, combined=False):
    out = []
    out.append("%s  13F-HR for quarter ending %s, filed %s (accession %s)" % (
        meta.get("filer", "?"), meta.get("period", "?"), meta.get("filed", "?"),
        meta.get("accession", "?")))
    out.append("Total %s across %d positions%s. Weights are %% of that total." % (
        _money(total), len(positions), ", share classes combined" if combined else ""))
    if meta.get("total_check"):
        out.append(meta["total_check"])
    out.append("")
    out.append("%3s  %-11s %-32s %16s %18s %7s" % ("#", "ticker", "issuer", "shares", "value", "%"))
    shown = positions[:top] if top else positions
    for i, p in enumerate(shown, 1):
        tick = p.get("ticker") or "?"
        if p.get("tickers") and len(p["tickers"]) > 1:
            tick = "+".join(p["tickers"])
        flag = " *" if p.get("ticker_check") else ""
        kind = "" if is_stock(p) else " [%s]" % (p["put_call"] or p["share_type"])
        out.append("%3d  %-11s %-32s %16s %18s %7.2f%s" % (
            i, tick, (p["issuer"] + kind)[:32], _shares(p["shares"]), _money(p["value"]),
            p["pct_of_13f"], flag))
    if top and len(positions) > top:
        out.append("     ... %d more" % (len(positions) - top))
    if any(p.get("ticker_check") for p in shown):
        out.append("")
        out.append("* ticker matched by name or share class guess: confirm it before you "
                   "copy, or run with --figi.")
    for note in meta.get("notes", []):
        out.append("")
        out.append(note)
    out.append("")
    out.append("Holdings as of %s. A 13F is quarterly and up to 45 days late, long US "
               "equity positions only." % meta.get("period", "the quarter end"))
    return "\n".join(out) + "\n"


def render_diff(meta, rows):
    out = ["%s: %s vs %s" % (meta.get("filer", "?"), meta.get("period", "?"),
                             meta.get("prior_period", "?")), ""]
    for status in ("NEW", "SOLD OUT", "ADDED", "TRIMMED", "UNCHANGED"):
        group = [r for r in rows if r["status"] == status]
        if not group:
            continue
        out.append("%s (%d)" % (status, len(group)))
        for r in group:
            tick = r.get("ticker") or "?"
            label = "%s %s" % (r["issuer"], r["title"])
            if status == "UNCHANGED":
                out.append("  %-7s %-40s %s shares" % (tick, label[:40], _shares(r["shares_after"])))
            elif status == "SOLD OUT":
                out.append("  %-7s %-40s %s shares (%s at the prior quarter end)" % (
                    tick, label[:40], _shares(r["shares_before"]), _money(r["value_before"])))
            else:
                pct = "" if r["change_pct"] is None else " (%+.1f%%)" % r["change_pct"]
                out.append("  %-7s %-40s %s -> %s%s" % (
                    tick, label[:40], _shares(r["shares_before"]), _shares(r["shares_after"]), pct))
        out.append("")
    out.append("Share counts at two quarter ends. Trades inside the quarter, and their "
               "prices, are not in a 13F.")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- the flow

def load_quarter(fetcher, cik, period=None, accession=None, before=None):
    """Download and parse one quarter. Returns (meta, rows, filings)."""
    sub = fetcher.get_json(SUBMISSIONS % cik10(cik))
    filings = list_filings(sub)
    if not filings:
        raise EdgarError("%s (CIK %s) has no 13F-HR filings in its recent list. Is it a "
                         "13F filer? Try: edgar13f.py find NAME --sec" % (sub.get("name"), cik10(cik)))
    chosen, amendments = pick_filing(filings, period=period, accession=accession, before=before)
    base = archive_url(cik, chosen["accession"])
    index = fetcher.get_json(base + "index.json")
    table = info_table_name(index)
    mult = value_multiplier(chosen["filed"])
    rows = parse_info_table(fetcher.get(base + table), multiplier=mult)
    meta = {"filer": sub.get("name"), "cik": cik10(cik), "period": chosen["period"],
            "filed": chosen["filed"], "accession": chosen["accession"], "form": chosen["form"],
            "info_table_url": base + table, "notes": []}
    try:
        cover = parse_cover(fetcher.get(base + "primary_doc.xml"))
    except EdgarError:
        cover = {}
    if cover.get("tableValueTotal"):
        meta["cover_total"] = float(cover["tableValueTotal"]) * mult
    if amendments:
        meta["notes"].append("Also filed for this quarter: %s. An amendment can restate the "
                             "table or add holdings that were kept confidential. Read it with "
                             "--accession." % ", ".join("%s on %s" % (a["accession"], a["filed"])
                                                       for a in amendments))
    return meta, rows, filings


def build(meta, rows, index=None, overrides=None, figi=None, combine=False):
    positions, total = aggregate(rows)
    resolve_tickers(positions, index, overrides, figi)
    if meta.get("cover_total"):
        gap = abs(meta["cover_total"] - total)
        meta["total_check"] = ("Matches the cover page total." if gap < 1000 else
                               "Cover page says %s; the rows add up to %s." % (
                                   _money(meta["cover_total"]), _money(total)))
    if combine:
        positions = combine_classes(positions, total)
    return positions, total


def ticker_index(fetcher, path=None):
    if path:
        with open(path, encoding="utf-8") as fh:
            return TickerIndex(load_company_tickers(json.load(fh)))
    return TickerIndex(load_company_tickers(fetcher.get_json(TICKERS_URL)))


def _common(ap):
    ap.add_argument("--user-agent", help="SEC User-Agent: \"Your Name you@example.com\"")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--offline", metavar="DIR", help="read every URL from files in DIR")
    ap.add_argument("--json", action="store_true", help="print JSON")


def _quarter_args(ap):
    ap.add_argument("cik", nargs="?", help="the filer's CIK (find it with: find NAME)")
    ap.add_argument("--period", help="quarter end, YYYY-MM-DD (default: the newest)")
    ap.add_argument("--accession", help="a specific filing, 0001193125-26-352200")
    ap.add_argument("--xml", help="read this information table file instead of EDGAR")
    ap.add_argument("--thousands", action="store_true",
                    help="with --xml: values are in thousands (filings before 2023)")
    ap.add_argument("--tickers", metavar="FILE",
                    help="company_tickers.json to use instead of downloading it")
    ap.add_argument("--map", default="trader/tickers.json",
                    help="your own CUSIP to ticker fixes (default trader/tickers.json)")
    ap.add_argument("--figi", action="store_true",
                    help="look up unresolved or unsure tickers on OpenFIGI")
    ap.add_argument("--figi-key", default=os.environ.get("OPENFIGI_API_KEY"))
    ap.add_argument("--no-tickers", action="store_true", help="skip ticker matching")


def _parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("find", help="find a filer's CIK by name or person")
    p.add_argument("query", nargs="+")
    p.add_argument("--sec", action="store_true", help="also search every EDGAR filer")
    _common(p)

    p = sub.add_parser("filings", help="list a filer's 13F filings")
    p.add_argument("cik")
    p.add_argument("--limit", type=int, default=8)
    _common(p)

    for name, text in (("holdings", "the positions in one quarter"),
                       ("diff", "what changed since the quarter before"),
                       ("copy", "write trader/targets.json from the top positions")):
        p = sub.add_parser(name, help=text)
        _quarter_args(p)
        _common(p)
        p.add_argument("--combine", action="store_true",
                       help="sum share classes of one company (Alphabet A + C)")
        p.add_argument("--top", type=int, help="only the N biggest positions")
        if name == "diff":
            p.add_argument("--prior-xml", help="with --xml: the prior quarter's table")
        if name == "copy":
            p.add_argument("--min-pct", type=float, default=0.0,
                           help="skip positions under this %% of the 13F")
            p.add_argument("--exclude", nargs="*", default=[], help="tickers to leave out")
            p.add_argument("--separate", action="store_true",
                           help="keep share classes apart (default: combined)")
            p.add_argument("--out", default="trader/targets.json")
            p.add_argument("--dry-run", action="store_true", help="print, do not write")
    return ap


def _fetcher(args, sec=True):
    ua = user_agent(args.user_agent, args.config)
    if sec and not ua and not args.offline:
        raise EdgarError(UA_HELP)
    return Fetcher(ua, offline=args.offline)


def _positions(args, fetcher, period=None, before=None, xml=None):
    if xml:
        with open(xml, "rb") as fh:
            rows = parse_info_table(fh.read(), multiplier=1000 if args.thousands else 1)
        meta = {"filer": os.path.basename(xml), "period": args.period or "?", "filed": "?",
                "accession": "?", "notes": []}
        filings = []
    else:
        if not args.cik:
            raise EdgarError("give a CIK, or --xml FILE")
        meta, rows, filings = load_quarter(fetcher, args.cik, period=period,
                                           accession=None if before else args.accession,
                                           before=before)
    index = None
    if not args.no_tickers:
        try:
            index = ticker_index(fetcher, args.tickers)
        except EdgarError as exc:
            meta["notes"].append("Tickers by name skipped: %s" % exc)
    overrides = load_overrides([CLASSES, args.map])
    figi = {}
    if args.figi:
        positions, _ = aggregate(rows)
        resolve_tickers(positions, index, overrides)
        unsure = [p["cusip"] for p in positions if p.get("ticker_check") or not p.get("ticker")]
        if unsure:
            figi = figi_lookup(fetcher, unsure, api_key=args.figi_key)
    return meta, rows, filings, index, overrides, figi


def main(argv=None):
    args = _parser().parse_args(argv)
    if not args.cmd:
        _parser().print_help()
        return 2
    try:
        return _run(args)
    except EdgarError as exc:
        print("edgar13f: %s" % exc, file=sys.stderr)
        return 2


def _run(args):
    if args.cmd == "find":
        query = " ".join(args.query)
        hits = [dict(f, source="bundled") for f in find_bundled(query)]
        if args.sec:
            fetcher = _fetcher(args)
            known = {h["cik"] for h in hits}
            hits += [dict(h, source="sec") for h in search_sec(fetcher, query)
                     if h["cik"] not in known]
        if args.json:
            print(json.dumps(hits, indent=2))
        elif not hits:
            print("No filer matched %r. Try --sec to search every EDGAR filer, or a "
                  "shorter name." % query)
        else:
            for h in hits:
                extra = h.get("person") or ""
                print("%s  %s%s%s" % (h["cik"], h["name"], "  (%s)" % extra if extra else "",
                                      "  [%s]" % h["status"] if h.get("status") else ""))
        return 0 if hits else 1

    fetcher = _fetcher(args, sec=not (getattr(args, "xml", None) and args.tickers
                                      and not getattr(args, "figi", False)))

    if args.cmd == "filings":
        sub = fetcher.get_json(SUBMISSIONS % cik10(args.cik))
        rows = list_filings(sub)[:args.limit]
        if args.json:
            print(json.dumps({"filer": sub.get("name"), "cik": cik10(args.cik),
                              "filings": rows}, indent=2))
        else:
            print("%s (CIK %s)" % (sub.get("name"), cik10(args.cik)))
            for f in rows:
                print("  %-9s quarter %s  filed %s  %s" % (f["form"], f["period"], f["filed"],
                                                           f["accession"]))
            if not rows:
                print("  no 13F filings")
        return 0 if rows else 1

    meta, rows, filings, index, overrides, figi = _positions(args, fetcher,
                                                             period=args.period, xml=args.xml)
    combine = args.combine or (args.cmd == "copy" and not args.separate)
    positions, total = build(meta, rows, index, overrides, figi, combine=combine)

    if args.cmd == "holdings":
        if args.json:
            print(json.dumps({"meta": meta, "total": total,
                              "positions": positions[:args.top] if args.top else positions},
                             indent=2))
        else:
            sys.stdout.write(render_holdings(meta, positions, total, top=args.top,
                                             combined=combine))
        return 0

    if args.cmd == "diff":
        if args.xml:
            if not args.prior_xml:
                raise EdgarError("with --xml, give the prior quarter as --prior-xml")
            with open(args.prior_xml, "rb") as fh:
                prior_rows = parse_info_table(fh.read(), multiplier=1000 if args.thousands else 1)
            meta["prior_period"] = "prior file"
        else:
            pmeta, prior_rows, _ = load_quarter(fetcher, args.cik, before=meta["period"])
            meta["prior_period"] = pmeta["period"]
        cur, _ = aggregate(rows)
        pri, _ = aggregate(prior_rows)
        resolve_tickers(cur, index, overrides, figi)
        resolve_tickers(pri, index, overrides, figi)
        changes = diff(cur, pri)
        if args.json:
            print(json.dumps({"meta": meta, "changes": changes}, indent=2))
        else:
            sys.stdout.write(render_diff(meta, changes))
        return 0

    # copy
    weights, skipped = copy_weights(positions, top=args.top, min_pct=args.min_pct,
                                    exclude=args.exclude)
    targets = {
        "source": "%s 13F-HR, quarter ending %s, filed %s (accession %s)" % (
            meta.get("filer"), meta.get("period"), meta.get("filed"), meta.get("accession")),
        "as_of": meta.get("period"),
        "filed": meta.get("filed"),
        "cik": meta.get("cik"),
        "method": "%s stock positions by value%s, pct_of_13f scaled to 100" % (
            "top %d" % args.top if args.top else "all",
            ", share classes combined" if combine else ""),
        "weights": weights,
        "skipped": skipped,
        "caveats": CAVEATS,
    }
    if args.json or args.dry_run:
        print(json.dumps(targets, indent=2))
    if not args.dry_run:
        folder = os.path.dirname(args.out)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(targets, fh, indent=2)
            fh.write("\n")
        if not args.json:
            print("wrote %s" % args.out)
            for w in weights:
                print("  %-7s %-28s %6.2f%%  (%.2f%% of the 13F)%s" % (
                    w["ticker"], w["name"][:28], w["weight"], w["pct_of_13f"],
                    "  check ticker" if w.get("check_ticker") else ""))
            print("Holdings as of %s, filed %s. Next: trader-budget sizes these to your "
                  "budget." % (meta.get("period"), meta.get("filed")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
