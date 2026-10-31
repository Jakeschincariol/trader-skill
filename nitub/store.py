"""Unified paper state: portfolio.json, ledger.md, events.jsonl."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import SESSIONS_DIR, STATE_DIR, ensure_dirs

PORTFOLIO = "portfolio.json"
LEDGER = "ledger.md"
EVENTS = "events.jsonl"
REPORT = "report.md"
NEWS = "news.json"
SKILLS = "skills.json"
ANALYSIS = "analysis.json"
META = "meta.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def append_event(
    kind: str,
    summary: str,
    *,
    data: dict | None = None,
    skill: str | None = None,
    state_dir: Path | None = None,
) -> dict:
    ensure_dirs()
    root = state_dir or STATE_DIR
    root.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": utc_now(),
        "kind": kind,
        "summary": summary,
    }
    if skill:
        event["skill"] = skill
    if data:
        event["data"] = data
    path = root / EVENTS
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def clear_events(state_dir: Path | None = None) -> None:
    path = (state_dir or STATE_DIR) / EVENTS
    if path.exists():
        path.write_text("", encoding="utf-8")


def read_events(state_dir: Path | None = None, limit: int = 200) -> list[dict]:
    path = (state_dir or STATE_DIR) / EVENTS
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def write_ledger(portfolio: dict, path: Path | None = None) -> str:
    fills = portfolio.get("fills") or []
    positions = portfolio.get("positions") or []
    session = portfolio.get("session", "active")
    lines = [
        f"# Paper ledger — {session}",
        "",
        f"**Режим:** {portfolio.get('mode', 'paper')} only (симуляция). Живой брокер не подключался.",
        f"**Валюта:** {portfolio.get('currency', 'USD')}. "
        f"**Стартовый капитал:** ${portfolio.get('starting_cash', 0):.2f}",
        f"**Цены:** {portfolio.get('price_source', 'yahoo')} · as_of {portfolio.get('as_of', '—')}",
        "",
        "## Сделки (fills)",
        "",
        "| время | id | сторона | тикер | qty | цена | cost | статус |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for f in fills:
        lines.append(
            "| {asof} | {id} | {side} | {sym} | {qty} | {price:.2f} | {cost:.2f} | {status} |".format(
                asof=(f.get("ts") or portfolio.get("as_of") or "")[:10],
                id=f.get("id", ""),
                side=f.get("side", ""),
                sym=f.get("symbol", ""),
                qty=f.get("qty", 0),
                price=float(f.get("price") or 0),
                cost=float(f.get("notional") or 0),
                status=f.get("status", "paper_simulated"),
            )
        )
    lines += [
        "",
        "## Счёт",
        "",
        "| статья | USD |",
        "| --- | ---: |",
        f"| Стартовый cash | {portfolio.get('starting_cash', 0):.2f} |",
        f"| Cash | {portfolio.get('cash', 0):.2f} |",
        f"| Позиции (MTM) | {portfolio.get('positions_market_value', 0):.2f} |",
        f"| Equity | {portfolio.get('equity', 0):.2f} |",
        f"| Unrealized P&L | {portfolio.get('unrealized_pnl', 0):.2f} |",
        f"| Realized P&L | {portfolio.get('realized_pnl', 0):.2f} |",
        f"| P&L к старту | {portfolio.get('total_pnl_vs_start', 0):.2f} |",
        "",
        "## Mark-to-market",
        "",
        "| тикер | shares | cost | mark | MTM | uP&L |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for p in positions:
        lines.append(
            "| {t} | {s} | {c:.2f} | {m:.2f} | {mv:.2f} | {u:.2f} |".format(
                t=p.get("ticker"),
                s=p.get("shares"),
                c=float(p.get("cost_basis") or 0),
                m=float(p.get("mark") or 0),
                mv=float(p.get("market_value") or 0),
                u=float(p.get("unrealized_pnl") or 0),
            )
        )
    lines += [
        "",
        "Education/tooling only. Paper simulation. Not financial advice.",
        "",
    ]
    text = "\n".join(lines)
    out = path or (STATE_DIR / LEDGER)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return text


def publish_state(
    portfolio: dict,
    *,
    report: str | None = None,
    news: list | dict | None = None,
    skills: list | dict | None = None,
    analysis: dict | None = None,
    archive: bool = True,
) -> Path:
    """Write active state under nitub/state and optionally archive to paper-trading/."""
    ensure_dirs()
    write_json(STATE_DIR / PORTFOLIO, portfolio)
    write_ledger(portfolio, STATE_DIR / LEDGER)
    if report is not None:
        (STATE_DIR / REPORT).write_text(report, encoding="utf-8")
    if news is not None:
        write_json(STATE_DIR / NEWS, news)
    if skills is not None:
        write_json(STATE_DIR / SKILLS, skills)
    if analysis is not None:
        write_json(STATE_DIR / ANALYSIS, analysis)

    session = portfolio.get("session") or "active"
    meta = {
        "session": session,
        "updated_at": utc_now(),
        "mode": portfolio.get("mode", "paper"),
        "state_dir": str(STATE_DIR),
        "archive_dir": str(SESSIONS_DIR / session) if archive else None,
    }
    write_json(STATE_DIR / META, meta)

    if archive:
        dest = SESSIONS_DIR / session
        dest.mkdir(parents=True, exist_ok=True)
        for name in (
            PORTFOLIO,
            LEDGER,
            EVENTS,
            REPORT,
            NEWS,
            SKILLS,
            ANALYSIS,
            META,
            "skills_catalog.json",
            "runner.json",
        ):
            src = STATE_DIR / name
            if src.exists():
                shutil.copy2(src, dest / name)
        skills_dir = STATE_DIR / "skills"
        if skills_dir.is_dir():
            dest_skills = dest / "skills"
            if dest_skills.exists():
                shutil.rmtree(dest_skills)
            shutil.copytree(skills_dir, dest_skills)
        # pointer for tools/humans
        write_json(SESSIONS_DIR / "current.json", {"session": session, "path": str(dest)})
    return STATE_DIR


def load_portfolio(state_dir: Path | None = None) -> dict | None:
    return read_json((state_dir or STATE_DIR) / PORTFOLIO)
