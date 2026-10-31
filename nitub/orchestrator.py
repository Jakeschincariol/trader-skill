"""Full skills pipeline for paper cycles and dashboard Start refresh."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import skills, store
from .paths import (
    CORE_SKILL_NAMES,
    STATE_DIR,
    TRADER_DIR,
    VENDOR_SKILL_NAMES,
    ensure_dirs,
)

# Heavy SEC / network skills: on refresh passes, run every N cycles.
HEAVY_EVERY = 3
HEAVY_SKILLS = {"copycat", "news", "pelosi", "analyst"}


def _load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _row(name: str, result: str, *, status: str = "ok", group: str = "core") -> dict:
    return {"name": name, "result": result, "status": status, "group": group}


def _ok_msg(r: dict, ok: str, bad: str | None = None) -> tuple[str, str]:
    if r.get("skipped"):
        return f"skip ({r.get('error') or 'soft'})", "soft"
    if r.get("ok"):
        return ok, "ok"
    return bad or f"exit {r.get('returncode')}", "error"


def target_tickers() -> list[str]:
    weights = (_load_json(TRADER_DIR / "targets.json", {}) or {}).get("weights") or []
    return [w["ticker"] for w in weights if w.get("ticker")]


def holding_tickers() -> list[str]:
    pf = _load_json(TRADER_DIR / "portfolio.json", {}) or {}
    out: list[str] = []
    for p in pf.get("positions") or []:
        t = p.get("symbol") or p.get("ticker")
        if t:
            out.append(str(t).upper())
    return out


def catalog() -> dict:
    """Full skill catalog for dashboard (core + vendor)."""
    return {
        "core": list(CORE_SKILL_NAMES),
        "vendor": list(VENDOR_SKILL_NAMES),
        "all": list(CORE_SKILL_NAMES) + list(VENDOR_SKILL_NAMES),
        "heavy": sorted(HEAVY_SKILLS),
        "heavy_every": HEAVY_EVERY,
    }


def publish_catalog(skill_rows: list[dict] | None = None) -> None:
    ensure_dirs()
    payload = catalog()
    if skill_rows is not None:
        payload["last_run"] = skill_rows
        done = [r["name"] for r in skill_rows if r.get("status") in ("ok", "soft", "skipped")]
        payload["completed"] = done
    store.write_json(STATE_DIR / "skills_catalog.json", payload)


def _should_run_heavy(name: str, *, cycle: int, force_heavy: bool) -> bool:
    if force_heavy or name not in HEAVY_SKILLS:
        return True
    # cycle 1, 4, 7… and always on cycle 0 seed
    return cycle <= 1 or (cycle % HEAVY_EVERY == 1)


def run_core_skills(
    *,
    tickers: list[str],
    cik: str = "1067983",
    top: int = 3,
    refresh_copycat: bool = True,
    copycat_out: Path | None = None,
    skip_news: bool = False,
    force_heavy: bool = True,
    cycle: int = 1,
    run_budget_risk: bool = True,
    broker_mode: str = "tickets",  # tickets | deferred
) -> list[dict]:
    """
    Invoke all 11 core trader skills (soft-fail). Heavy ones may be interval-gated
    on refresh passes, but still appear in the rows as used/interval.
    """
    rows: list[dict] = []
    uniq = sorted({t.upper() for t in tickers if t})

    # 1) alerts
    if uniq:
        q = skills.quote_tickers(uniq, write=True)
    else:
        q = skills.quote_holdings(write=True)
    msg, st = _ok_msg(q, "quotes обновлены")
    rows.append(_row("alerts", msg, status=st))

    # 2) copycat
    if _should_run_heavy("copycat", cycle=cycle, force_heavy=force_heavy):
        if refresh_copycat:
            out = copycat_out or (TRADER_DIR / "targets.json")
            cc = skills.copycat_top(cik=cik, top=top, out=out)
            msg, st = _ok_msg(
                cc,
                f"13F CIK {cik} top-{top} → {out.name}",
            )
            rows.append(_row("copycat", msg, status=st))
        else:
            store.append_event(
                "skill_start",
                "copycat: reuse targets (refresh_copycat=False)",
                skill="copycat",
            )
            store.append_event(
                "skill_end",
                "copycat: reuse trader/targets.json",
                skill="copycat",
            )
            rows.append(_row("copycat", "reuse trader/targets.json", status="ok"))
    else:
        store.append_event(
            "skill_skip",
            f"copycat: interval (каждые {HEAVY_EVERY} цикла) — числится в pipeline",
            skill="copycat",
            data={"cycle": cycle},
        )
        rows.append(
            _row(
                "copycat",
                f"interval skip (cycle {cycle}, every {HEAVY_EVERY}) — в pipeline",
                status="soft",
            )
        )

    # refresh ticker list after possible targets update (holdings + targets;
    # discovery path may pass an explicit candidate list already in uniq)
    uniq = sorted(set(uniq) | set(target_tickers()) | set(holding_tickers()))
    if uniq:
        skills.quote_tickers(uniq, write=True)

    # 3) news
    if skip_news:
        store.append_event("skill_skip", "news: skip_news=True", skill="news")
        rows.append(_row("news", "skipped (flag)", status="soft"))
    elif _should_run_heavy("news", cycle=cycle, force_heavy=force_heavy):
        n = skills.news_watch(soft=True)
        msg, st = _ok_msg(n, "ok")
        rows.append(_row("news", msg, status=st))
    else:
        store.append_event(
            "skill_skip",
            f"news: interval (каждые {HEAVY_EVERY} цикла)",
            skill="news",
            data={"cycle": cycle},
        )
        rows.append(
            _row("news", f"interval skip (cycle {cycle}) — в pipeline", status="soft")
        )

    # 4) pelosi
    if _should_run_heavy("pelosi", cycle=cycle, force_heavy=force_heavy):
        p = skills.pelosi_new(soft=True, dry_run=False)
        msg, st = _ok_msg(p, "PTR index checked")
        rows.append(_row("pelosi", msg, status=st))
    else:
        store.append_event(
            "skill_skip",
            f"pelosi: interval (каждые {HEAVY_EVERY} цикла)",
            skill="pelosi",
            data={"cycle": cycle},
        )
        rows.append(
            _row("pelosi", f"interval skip (cycle {cycle}) — в pipeline", status="soft")
        )

    # 5) analyst
    if _should_run_heavy("analyst", cycle=cycle, force_heavy=force_heavy):
        if uniq:
            a = skills.analyst_facts(uniq[:12], soft=True)
            msg, st = _ok_msg(a, f"metrics ← {', '.join(uniq[:12])}")
            rows.append(_row("analyst", msg, status=st))
        else:
            store.append_event("skill_skip", "analyst: нет тикеров (без mega hardcode)", skill="analyst")
            rows.append(_row("analyst", "skipped — нет тикеров", status="soft"))
    else:
        store.append_event(
            "skill_skip",
            f"analyst: interval (каждые {HEAVY_EVERY} цикла)",
            skill="analyst",
            data={"cycle": cycle},
        )
        rows.append(
            _row("analyst", f"interval skip (cycle {cycle}) — в pipeline", status="soft")
        )

    # 6) screener (always — local, fast)
    sc = skills.screener_run(soft=True)
    msg, st = _ok_msg(sc, "shortlist обновлён")
    rows.append(_row("screener", msg, status=st))

    # 7–8) budget + risk
    if run_budget_risk:
        sz = skills.size_orders(write=True)
        orders = _load_json(TRADER_DIR / "orders.json", {}) or {}
        n_orders = len(orders.get("orders") or orders.get("proposed") or (orders if isinstance(orders, list) else []) or [])
        msg, st = _ok_msg(sz, f"{n_orders} proposed orders")
        rows.append(_row("budget", msg, status=st))

        rk = skills.risk_check(write=True)
        risk = _load_json(TRADER_DIR / "state" / "risk.json", {}) or {}
        verdicts = risk.get("results") or risk.get("orders") or risk.get("verdicts") or []
        passed = sum(
            1
            for v in verdicts
            if (v.get("status") or v.get("verdict") or "").upper() == "PASS"
        )
        msg, st = _ok_msg(rk, f"PASS ×{passed}" if verdicts else "risk.json written")
        rows.append(_row("risk", msg, status=st))
    else:
        store.append_event("skill_skip", "budget: deferred (refresh pass)", skill="budget")
        rows.append(_row("budget", "deferred (refresh — без переписывания sleeve)", status="soft"))
        store.append_event("skill_skip", "risk: deferred (refresh pass)", skill="risk")
        rows.append(_row("risk", "deferred (refresh — без переписывания sleeve)", status="soft"))

    # 9) broker — paper tickets only
    if broker_mode == "tickets":
        br = skills.broker_tickets(soft=True)
        msg, st = _ok_msg(br, "tickets show (paper only, без live)")
        rows.append(_row("broker", msg, status=st))
    else:
        store.append_event(
            "skill_start",
            "broker: deferred to paper fills",
            skill="broker",
        )
        store.append_event(
            "skill_end",
            "broker: paper simulator fills (см. paper.py)",
            skill="broker",
        )
        rows.append(_row("broker", "deferred → paper fills", status="ok"))

    # 10) tax
    tx = skills.tax_report(soft=True)
    msg, st = _ok_msg(tx, "lots / unrealized report")
    rows.append(_row("tax", msg, status=st))

    # 11) rebalance (json only — не затираем orders в середине цикла)
    rb = skills.rebalance_check(write=False, soft=True)
    msg, st = _ok_msg(rb, "drift table (no write)")
    rows.append(_row("rebalance", msg, status=st))

    # ensure all 11 names present
    seen = {r["name"] for r in rows}
    for name in CORE_SKILL_NAMES:
        if name not in seen:
            rows.append(_row(name, "missing step — added placeholder", status="soft"))

    return rows


def run_vendor_skills(tickers: list[str]) -> list[dict]:
    return skills.vendor_skillmd_batch(tickers)


def run_full_pipeline(
    *,
    tickers: list[str] | None = None,
    cik: str = "1067983",
    top: int = 3,
    refresh_copycat: bool = True,
    copycat_out: Path | None = None,
    skip_news: bool = False,
    force_heavy: bool = True,
    cycle: int = 1,
    run_budget_risk: bool = True,
    broker_mode: str = "tickets",
    include_vendor: bool = True,
) -> list[dict]:
    """All core (+ vendor) skills → skill_rows for skills.json / dashboard."""
    ensure_dirs()
    (STATE_DIR / "skills").mkdir(parents=True, exist_ok=True)
    # Never invent mega-cap defaults as the sole universe path.
    tix = list(tickers or []) or target_tickers() or holding_tickers()
    if not tix:
        from . import signals

        tix = signals.discovery_universe(max_n=12)
    if not tix:
        store.append_event(
            "pipeline_empty",
            "Нет тикеров для pipeline (targets/holdings/discovery пусты) — без mega hardcode",
        )
    store.append_event(
        "pipeline_start",
        f"Полный pipeline skills (cycle={cycle}, heavy={'all' if force_heavy else 'interval'})",
        data={"tickers": tix, "cycle": cycle, "vendor": include_vendor},
    )
    rows = run_core_skills(
        tickers=tix,
        cik=cik,
        top=top,
        refresh_copycat=refresh_copycat,
        copycat_out=copycat_out,
        skip_news=skip_news,
        force_heavy=force_heavy,
        cycle=cycle,
        run_budget_risk=run_budget_risk,
        broker_mode=broker_mode,
    )
    if include_vendor:
        rows.extend(run_vendor_skills(tix))
    publish_catalog(rows)
    store.append_event(
        "pipeline_end",
        f"Pipeline: {len(rows)} skills ({len(CORE_SKILL_NAMES)} core + "
        f"{len(rows) - len(CORE_SKILL_NAMES)} vendor)",
        data={"names": [r["name"] for r in rows]},
    )
    return rows


def merge_skill_rows(*groups: list[dict]) -> list[dict]:
    """Last write wins per name; preserve order of first appearance."""
    order: list[str] = []
    by_name: dict[str, dict] = {}
    for group in groups:
        for row in group:
            name = row["name"]
            if name not in by_name:
                order.append(name)
            by_name[name] = row
    return [by_name[n] for n in order]
