"""Signal-driven sleeve selection from skills artifacts (not mega-cap hardcode)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import store
from .paths import STATE_DIR, TRADER_DIR

# Popular mega names may appear only if skills independently select them.
# They must never be the silent default universe / sole selection path.
MEGA_CAP_DEFAULTS = frozenset(
    {"AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA"}
)


def _load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _norm(t: str | None) -> str | None:
    if not t:
        return None
    return str(t).strip().upper().replace(".", "-")


def discovery_universe(
    *,
    copycat_path: Path | None = None,
    max_n: int = 16,
) -> list[str]:
    """Build a screenable universe from copycat 13F (+ prior shortlist), not mega hardcode."""
    path = copycat_path or (TRADER_DIR / "state" / "copycat-ref.json")
    if not path.exists():
        path = TRADER_DIR / "targets.json"

    ordered: list[str] = []
    seen: set[str] = set()

    def add(t: str | None, *, allow_mega: bool = True) -> None:
        n = _norm(t)
        if not n or n in seen:
            return
        if n in MEGA_CAP_DEFAULTS and not allow_mega:
            return
        seen.add(n)
        ordered.append(n)

    cc = _load(path, {}) or {}
    for w in cc.get("weights") or []:
        add(w.get("ticker"))
    for s in cc.get("skipped") or []:
        add(s.get("ticker"))

    # Prior screener survivors (signal residue), if any
    short = _load(TRADER_DIR / "shortlist.json", {}) or {}
    for row in short.get("passed") or short.get("shortlist") or []:
        add(row.get("ticker") if isinstance(row, dict) else row)

    # Pelosi PTR rows if the last check stored parsed reports
    pelosi = _load(TRADER_DIR / "state" / "pelosi.json", {}) or {}
    for key in ("reports", "new", "trades", "rows"):
        for item in pelosi.get(key) or []:
            if isinstance(item, dict):
                add(item.get("ticker") or item.get("symbol"))

    # Cap: prefer non-mega first so analyst budget isn't all household names
    non_mega = [t for t in ordered if t not in MEGA_CAP_DEFAULTS]
    mega = [t for t in ordered if t in MEGA_CAP_DEFAULTS]
    mixed = non_mega + mega
    out = mixed[:max_n]
    store.append_event(
        "signal_discovery",
        f"Discovery universe ×{len(out)} from copycat/shortlist/pelosi "
        f"(non-mega {len([t for t in out if t not in MEGA_CAP_DEFAULTS])})",
        data={"tickers": out, "source": str(path)},
    )
    return out


def _screener_passed() -> list[dict]:
    short = _load(TRADER_DIR / "shortlist.json", {}) or {}
    rows = short.get("passed") or short.get("shortlist") or []
    out = []
    for r in rows:
        if isinstance(r, dict) and r.get("ticker"):
            out.append(r)
        elif isinstance(r, str):
            out.append({"ticker": r, "why": [], "score": 0})
    return out


def _copycat_map(path: Path | None = None) -> dict[str, dict]:
    p = path or (TRADER_DIR / "state" / "copycat-ref.json")
    if not p.exists():
        p = TRADER_DIR / "targets.json"
    cc = _load(p, {}) or {}
    m: dict[str, dict] = {}
    for i, w in enumerate(cc.get("weights") or []):
        t = _norm(w.get("ticker"))
        if t:
            m[t] = {**w, "rank": i + 1, "in_top": True}
    for s in cc.get("skipped") or []:
        t = _norm(s.get("ticker"))
        if t and t not in m:
            m[t] = {**s, "in_top": False, "rank": None}
    return m


def _metrics_map() -> dict[str, dict]:
    data = _load(TRADER_DIR / "metrics.json", {}) or {}
    return {
        _norm(c.get("ticker")): c
        for c in (data.get("companies") or [])
        if _norm(c.get("ticker"))
    }


def _vendor_artifact(name: str) -> dict:
    return _load(STATE_DIR / "skills" / f"{name}.json", {}) or {}


def _earnings_risk(ticker: str) -> tuple[bool, str | None]:
    """True if earnings within ~5 calendar days (event-risk exclude)."""
    art = _vendor_artifact("earnings-calendar")
    from datetime import date, datetime, timedelta

    today = date.today()
    for row in art.get("rows") or []:
        if _norm(row.get("ticker")) != ticker:
            continue
        cal = row.get("calendar") or {}
        ed = cal.get("Earnings Date")
        dates: list[date] = []
        if isinstance(ed, list):
            for x in ed:
                try:
                    dates.append(date.fromisoformat(str(x)[:10]))
                except ValueError:
                    pass
        elif isinstance(ed, str):
            try:
                dates.append(date.fromisoformat(ed[:10]))
            except ValueError:
                pass
        for d in dates:
            delta = (d - today).days
            if 0 <= delta <= 5:
                return True, f"earnings через {delta}д ({d.isoformat()})"
    return False, None


def _drawdown_block(ticker: str) -> tuple[bool, str | None]:
    art = _vendor_artifact("drawdown-circuit-breaker")
    rows = (art.get("artifact") or art).get("per_ticker") or art.get("per_ticker") or []
    for row in rows:
        if _norm(row.get("ticker")) != ticker:
            continue
        dd = row.get("max_drawdown_6mo")
        if dd is not None and float(dd) <= -0.28:
            return True, f"drawdown 6mo {float(dd):.1%} (circuit)"
    return False, None


def _valuation_note(ticker: str, metrics: dict | None) -> str | None:
    art = _vendor_artifact("company-valuation")
    rows = (art.get("artifact") or art).get("rows") or art.get("rows") or []
    for row in rows:
        if _norm(row.get("ticker")) == ticker:
            return row.get("note") or row.get("summary")
    if metrics:
        pe = metrics.get("pe")
        if pe is not None:
            try:
                return f"PE≈{float(pe):.1f} (analyst/metrics)"
            except (TypeError, ValueError):
                pass
    return None


def _market_env() -> dict:
    art = _vendor_artifact("market-environment-analysis")
    return (art.get("artifact") or art).get("regime") or art.get("regime") or {}


def build_signal_targets(
    *,
    write: bool = True,
    copycat_path: Path | None = None,
    max_positions: int = 8,
    min_signals: int = 1,
) -> dict:
    """
    Select sleeve from screener + skill signals.

    Primary gate: screener shortlist (passed filters).
    Enrich with copycat / analyst / earnings / drawdown / valuation / market env.
    Never invent mega-cap defaults when signals are empty — return cash sleeve.
    """
    passed = _screener_passed()
    copycat = _copycat_map(copycat_path)
    metrics = _metrics_map()
    regime = _market_env()
    risk_off = bool(regime.get("risk_off"))

    selected: list[dict] = []
    rejected: list[dict] = []
    considered: set[str] = set()

    def _vendor_gates(t: str, sources: list[str], reasons: list[str]) -> str | None:
        """Return reject reason or None if ok; mutates sources/reasons on success."""
        earn_block, earn_why = _earnings_risk(t)
        if earn_block:
            return earn_why or "earnings risk"
        sources.append("earnings-calendar")
        reasons.append("earnings-calendar: нет ближнего отчёта (≤5д)")

        dd_block, dd_why = _drawdown_block(t)
        if dd_block:
            return dd_why or "drawdown trip"
        sources.append("drawdown-circuit-breaker")
        reasons.append("drawdown-circuit-breaker: ok")

        m = metrics.get(t)
        val = _valuation_note(t, m)
        if val:
            sources.append("company-valuation")
            reasons.append(f"company-valuation: {val}")

        sources.append("market-environment-analysis")
        if risk_off:
            reasons.append(
                f"market-environment: risk_off ({regime.get('summary') or 'SPY weak'})"
            )
        else:
            reasons.append(
                f"market-environment: {regime.get('summary') or 'neutral/risk_on'}"
            )
        return None

    # --- Primary: screener shortlist ---
    for row in passed:
        t = _norm(row.get("ticker"))
        if not t:
            continue
        considered.add(t)
        reasons: list[str] = []
        sources: list[str] = ["screener"]
        reasons.append(
            "screener PASS: " + "; ".join(str(x) for x in (row.get("why") or [])[:4])
            if row.get("why")
            else "screener PASS"
        )

        cc = copycat.get(t)
        if cc and cc.get("in_top"):
            sources.append("copycat")
            reasons.append(
                f"copycat 13F top#{cc.get('rank')} weight={cc.get('weight')}"
            )
        elif cc:
            sources.append("copycat")
            reasons.append("copycat 13F (outside top slice, still in filing)")

        m = metrics.get(t)
        if m:
            sources.append("analyst")
            bits = []
            if m.get("revenue_growth_yoy") is not None:
                bits.append(f"revYoY {m['revenue_growth_yoy']}%")
            if m.get("operating_margin") is not None:
                bits.append(f"opMargin {m['operating_margin']}%")
            if m.get("eps_ttm") is not None:
                bits.append(f"EPS {m['eps_ttm']}")
            reasons.append("analyst: " + (", ".join(bits) if bits else "metrics ok"))

        reject_why = _vendor_gates(t, sources, reasons)
        if reject_why:
            filt = "earnings-calendar" if "earnings" in reject_why.lower() else "drawdown-circuit-breaker"
            rejected.append({"ticker": t, "why": reject_why, "filter": filt})
            store.append_event(
                "signal_reject",
                f"{t}: {reject_why}",
                skill=filt,
                data={"ticker": t, "why": reject_why},
            )
            continue

        if len(set(sources)) < min_signals:
            rejected.append({"ticker": t, "why": "мало независимых сигналов", "filter": "signals"})
            continue

        score = float(row.get("score") or 0) + (2.0 if cc and cc.get("in_top") else 0.0) + 3.0
        if risk_off:
            score *= 0.85
        selected.append(
            {
                "ticker": t,
                "name": row.get("name") or (m or {}).get("name") or (cc or {}).get("name") or t,
                "score": score,
                "sources": sorted(set(sources)),
                "reasons": reasons,
            }
        )

    # --- Secondary: copycat top + analyst quality (не mega-hardcode; 13F сигнал) ---
    for t, cc in sorted(
        copycat.items(),
        key=lambda kv: (kv[1].get("rank") is None, kv[1].get("rank") or 99),
    ):
        if t in considered or not cc.get("in_top"):
            continue
        considered.add(t)
        m = metrics.get(t)
        if not m:
            rejected.append({"ticker": t, "why": "нет analyst metrics", "filter": "analyst"})
            continue
        eps = m.get("eps_ttm")
        rev = m.get("revenue_growth_yoy")
        if eps is None or float(eps) <= 0:
            rejected.append({"ticker": t, "why": "analyst: EPS≤0/missing", "filter": "analyst"})
            continue
        if rev is not None and float(rev) < 0:
            rejected.append({"ticker": t, "why": f"analyst: revYoY {rev}% < 0", "filter": "analyst"})
            continue

        reasons = [
            f"copycat 13F top#{cc.get('rank')} weight={cc.get('weight')}",
            f"analyst: EPS {eps}"
            + (f", revYoY {rev}%" if rev is not None else ""),
        ]
        sources = ["copycat", "analyst"]
        reject_why = _vendor_gates(t, sources, reasons)
        if reject_why:
            rejected.append({"ticker": t, "why": reject_why, "filter": "vendor"})
            store.append_event(
                "signal_reject",
                f"{t}: {reject_why}",
                data={"ticker": t, "why": reject_why},
            )
            continue

        # Не пускаем «голый» mega без второго не-copycat сигнала уже есть analyst+vendor
        score = 1.5 + max(0, 6 - int(cc.get("rank") or 6)) * 0.15
        if risk_off:
            score *= 0.85
        selected.append(
            {
                "ticker": t,
                "name": cc.get("name") or (m or {}).get("name") or t,
                "score": score,
                "sources": sorted(set(sources)),
                "reasons": reasons,
            }
        )

    # Sort by score desc; optionally shrink sleeve in risk-off
    selected.sort(key=lambda r: (-float(r["score"]), r["ticker"]))
    cap = max_positions
    if risk_off:
        cap = min(cap, max(2, max_positions // 2))
    selected = selected[:cap]

    if not selected:
        targets = {
            "source": "nitub signal sleeve (empty — нет прошедших фильтров)",
            "as_of": store.utc_now(),
            "method": "skills pipeline: screener + news/analyst/pelosi/copycat/vendor",
            "strategy_id": "signal_skills",
            "family": "signal_driven",
            "weights": [],
            "signals": [],
            "rejected": rejected,
            "regime": regime,
            "note": "Лучше 0 позиций, чем тупой mega-cap sleeve без сигналов.",
        }
        store.append_event(
            "signal_sleeve",
            "Пустой sleeve: ни один тикер не прошёл screener+vendor фильтры "
            "(mega-cap hardcode запрещён)",
            data={"rejected": rejected[:12], "regime": regime},
        )
        if write:
            store.write_json(TRADER_DIR / "targets.json", targets)
            store.write_json(STATE_DIR / "signals.json", targets)
        return targets

    n = len(selected)
    pct = round(100.0 / n, 2)
    weights = []
    for i, row in enumerate(selected):
        w = pct if i < n - 1 else round(100.0 - pct * (n - 1), 2)
        weights.append(
            {
                "ticker": row["ticker"],
                "name": row["name"],
                "weight": w,
                "pct_of_13f": w,
                "sources": row["sources"],
                "reasons": row["reasons"],
                "score": row["score"],
            }
        )
        store.append_event(
            "signal_select",
            f"{row['ticker']}: " + " | ".join(row["reasons"][:3]),
            data={
                "ticker": row["ticker"],
                "sources": row["sources"],
                "reasons": row["reasons"],
                "score": row["score"],
            },
        )

    targets = {
        "source": "nitub signal sleeve (screener + skills)",
        "as_of": store.utc_now(),
        "method": "skills pipeline: screener shortlist + analyst/copycat/pelosi/news/vendor filters",
        "strategy_id": "signal_skills",
        "family": "signal_driven",
        "weights": weights,
        "signals": [
            {
                "ticker": w["ticker"],
                "sources": w["sources"],
                "reasons": w["reasons"],
            }
            for w in weights
        ],
        "rejected": rejected,
        "regime": regime,
    }
    store.append_event(
        "signal_sleeve",
        "Sleeve: " + ", ".join(f"{w['ticker']}←{','.join(w['sources'][:3])}" for w in weights),
        data={"tickers": [w["ticker"] for w in weights], "n": n},
    )
    if write:
        store.write_json(TRADER_DIR / "targets.json", targets)
        store.write_json(STATE_DIR / "signals.json", targets)
    return targets
