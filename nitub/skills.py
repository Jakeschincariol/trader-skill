"""Wrappers around trader-* tools and vendor Claude skills. Each call is logged to events."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import store
from .paths import (
    EXTERNAL_SKILLS,
    ROOT,
    SKILLS,
    STATE_DIR,
    TRADER_DIR,
    VENDOR_SKILLS_DIR,
)


class SkillError(RuntimeError):
    pass


def _python() -> str:
    venv_py = ROOT / "nitub" / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def run_skill(
    name: str,
    args: list[str],
    *,
    timeout: int = 120,
    cwd: Path | None = None,
    env: dict | None = None,
    log: bool = True,
) -> dict[str, Any]:
    script = SKILLS.get(name)
    if not script or not script.exists():
        raise SkillError(f"skill tool missing: {name} ({script})")

    cmd = [_python(), str(script), *args]
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    cfg = TRADER_DIR / "config.json"
    if cfg.exists() and "SEC_USER_AGENT" not in run_env:
        try:
            ua = json.loads(cfg.read_text(encoding="utf-8")).get("sec_user_agent")
            if ua:
                run_env["SEC_USER_AGENT"] = ua
        except Exception:
            pass

    if log:
        store.append_event(
            "skill_start",
            f"Запуск {name}: {' '.join(args[:6])}",
            skill=name,
            data={"cmd": cmd[1:]},
        )

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or ROOT),
            env=run_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        if log:
            store.append_event(
                "skill_error",
                f"{name}: timeout after {timeout}s",
                skill=name,
            )
        raise SkillError(f"{name} timed out") from e

    result = {
        "skill": name,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-8000:],
        "stderr": (proc.stderr or "")[-4000:],
        "ok": proc.returncode == 0,
    }
    if log:
        store.append_event(
            "skill_end" if result["ok"] else "skill_error",
            f"{name}: exit {proc.returncode}",
            skill=name,
            data={
                "returncode": proc.returncode,
                "stdout_tail": result["stdout"][-500:],
                "stderr_tail": result["stderr"][-300:],
            },
        )
    return result


def _soft(name: str, fn, *, soft: bool = True) -> dict:
    try:
        return fn()
    except SkillError as exc:
        if soft:
            store.append_event("skill_skip", f"{name}: {exc}", skill=name)
            return {"ok": False, "skipped": True, "skill": name, "error": str(exc)}
        raise
    except Exception as exc:
        if soft:
            store.append_event("skill_skip", f"{name}: {exc}", skill=name)
            return {"ok": False, "skipped": True, "skill": name, "error": str(exc)}
        raise


def quote_holdings(write: bool = True) -> dict:
    args = ["quote", "--holdings"]
    if write:
        args.append("--write")
    return run_skill("alerts", args, timeout=90)


def quote_tickers(tickers: list[str], write: bool = True) -> dict:
    args = ["quote", *tickers]
    if write:
        args.append("--write")
    return run_skill("alerts", args, timeout=90)


def copycat_top(cik: str = "1067983", top: int = 3, out: Path | None = None) -> dict:
    target = out or (TRADER_DIR / "targets.json")
    return run_skill(
        "copycat",
        ["copy", cik, "--top", str(top), "--out", str(target)],
        timeout=180,
    )


def size_orders(write: bool = True) -> dict:
    args = ["--cents"]
    if write:
        args.append("--write")
    args.append("--json")
    return run_skill("budget", args, timeout=60)


def risk_check(write: bool = True) -> dict:
    args = ["--json"]
    if not write:
        args.append("--no-write")
    return run_skill("risk", args, timeout=60)


def news_watch(soft: bool = True) -> dict:
    def _run():
        return run_skill("news", ["--json", "--hours", "72"], timeout=120)

    return _soft("news", _run, soft=soft)


def analyst_facts(tickers: list[str], soft: bool = True) -> dict:
    if not tickers:
        store.append_event("skill_skip", "analyst: нет тикеров", skill="analyst")
        return {"ok": False, "skipped": True, "skill": "analyst"}

    metrics = TRADER_DIR / "metrics.json"

    def _run():
        return run_skill(
            "analyst",
            [*tickers, "--metrics", str(metrics)],
            timeout=240,
        )

    return _soft("analyst", _run, soft=soft)


def screener_run(soft: bool = True) -> dict:
    metrics = TRADER_DIR / "metrics.json"
    if not metrics.exists():
        store.append_event(
            "skill_skip",
            "screener: нет trader/metrics.json (сначала analyst)",
            skill="screener",
        )
        return {"ok": False, "skipped": True, "skill": "screener"}

    rules = TRADER_DIR / "rules.json"
    out = TRADER_DIR / "shortlist.json"
    args = [str(metrics), "--out", str(out), "--json", "--prices", str(TRADER_DIR / "prices.json")]
    if rules.exists():
        args.extend(["--rules", str(rules)])

    def _run():
        return run_skill("screener", args, timeout=60)

    return _soft("screener", _run, soft=soft)


def pelosi_new(*, soft: bool = True, dry_run: bool = False) -> dict:
    args = ["new", "--json"]
    if dry_run:
        args.append("--dry-run")
    state = TRADER_DIR / "state" / "pelosi.json"
    args.extend(["--state", str(state)])

    def _run():
        return run_skill("pelosi", args, timeout=180)

    return _soft("pelosi", _run, soft=soft)


def tax_report(*, soft: bool = True) -> dict:
    def _run():
        return run_skill("tax", ["report", "--json", "--lots"], timeout=60)

    return _soft("tax", _run, soft=soft)


def rebalance_check(*, write: bool = False, soft: bool = True) -> dict:
    args = ["--json"]
    if write:
        args.append("--write")

    def _run():
        r = run_skill("rebalance", args, timeout=60)
        # rebalance.py returns exit 1 when nothing is outside the band (no orders).
        if not r.get("ok") and r.get("returncode") == 1 and r.get("stdout"):
            try:
                payload = json.loads(r["stdout"])
                if isinstance(payload, dict) and "rows" in payload:
                    r["ok"] = True
                    r["no_orders"] = not bool(payload.get("orders"))
                    r["parsed"] = payload
            except json.JSONDecodeError:
                pass
        return r

    return _soft("rebalance", _run, soft=soft)


def broker_tickets(*, soft: bool = True) -> dict:
    """Paper-safe: ticket.py show only — never places live orders."""

    def _run():
        return run_skill("broker", ["show", "--json"], timeout=60)

    return _soft("broker", _run, soft=soft)


def _write_artifact(name: str, payload: dict) -> Path:
    path = STATE_DIR / "skills" / f"{name}.json"
    store.write_json(path, payload)
    return path


def skillmd_step(
    name: str,
    *,
    summary: str,
    artifact: dict | None = None,
    status: str = "ok",
) -> dict:
    """Record a SKILL.md-driven step (no runnable CLI required)."""
    meta = EXTERNAL_SKILLS.get(name) or {}
    skill_md = meta.get("skill_md")
    store.append_event(
        "skill_start",
        f"Запуск {name} (SKILL.md)",
        skill=name,
        data={"skill_md": str(skill_md) if skill_md else None, "source": meta.get("source")},
    )
    payload = {
        "skill": name,
        "status": status,
        "summary": summary,
        "skill_md": str(skill_md) if skill_md else None,
        "source": meta.get("source"),
        "ts": store.utc_now(),
        "artifact": artifact or {},
    }
    path = _write_artifact(name, payload)
    store.append_event(
        "skill_end" if status == "ok" else "skill_skip",
        f"{name}: {summary}",
        skill=name,
        data={"artifact": str(path), "status": status},
    )
    return {"ok": status == "ok", "skill": name, "artifact": str(path), "summary": summary}


def _jsonable(obj: Any) -> Any:
    """Make yfinance / datetime objects JSON-serializable."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)
    if hasattr(obj, "item"):
        try:
            return _jsonable(obj.item())
        except Exception:
            pass
    if hasattr(obj, "to_dict"):
        try:
            return _jsonable(obj.to_dict())
        except Exception:
            return str(obj)
    return str(obj)


def earnings_calendar_step(tickers: list[str], *, soft: bool = True) -> dict:
    """Vendor: earnings-calendar — try yfinance calendar for holdings (no FMP key)."""
    store.append_event(
        "skill_start",
        f"Запуск earnings-calendar: {', '.join(tickers[:8]) or '—'}",
        skill="earnings-calendar",
    )
    rows: list[dict] = []
    try:
        import yfinance as yf

        for t in tickers[:12]:
            try:
                cal = yf.Ticker(t).calendar
                if cal is None:
                    continue
                rows.append({"ticker": t, "calendar": _jsonable(cal)})
            except Exception as exc:
                rows.append({"ticker": t, "error": str(exc)})
        artifact = {
            "skill": "earnings-calendar",
            "source": "vendor/skills/claude-trading-skills/skills/earnings-calendar",
            "note": "Локальный yfinance-calendar; FMP script нужен API key — не вызывали",
            "tickers": tickers,
            "rows": rows,
            "ts": store.utc_now(),
        }
        path = _write_artifact("earnings-calendar", artifact)
        store.append_event(
            "skill_end",
            f"earnings-calendar: {len(rows)} тикеров",
            skill="earnings-calendar",
            data={"artifact": str(path)},
        )
        return {"ok": True, "skill": "earnings-calendar", "rows": len(rows), "artifact": str(path)}
    except Exception as exc:
        if soft:
            return skillmd_step(
                "earnings-calendar",
                summary=f"fallback SKILL.md ({exc})",
                artifact={"error": str(exc), "tickers": tickers},
                status="soft",
            )
        raise


def clawdfolio_risk_step(tickers: list[str], *, soft: bool = True) -> dict:
    """Vendor: clawdfolio-inspired portfolio risk snapshot via yfinance (no pip install)."""
    store.append_event(
        "skill_start",
        f"Запуск clawdfolio-risk: {', '.join(tickers[:8]) or '—'}",
        skill="clawdfolio",
    )
    try:
        import math

        import numpy as np
        import yfinance as yf

        if not tickers:
            return skillmd_step(
                "clawdfolio",
                summary="нет тикеров — только SKILL.md зарегистрирован",
                status="soft",
            )

        data = yf.download(
            " ".join(tickers[:8]),
            period="6mo",
            interval="1d",
            progress=False,
            threads=False,
            auto_adjust=True,
        )
        close = data["Close"] if data is not None and "Close" in getattr(data, "columns", []) else data
        if close is None or getattr(close, "empty", True):
            raise RuntimeError("нет ценовых рядов")

        if hasattr(close, "columns"):
            rets = close.pct_change().dropna(how="all")
            # equal-weight portfolio return
            port = rets.mean(axis=1).dropna()
        else:
            rets = close.pct_change().dropna()
            port = rets

        vol = float(port.std() * math.sqrt(252)) if len(port) else 0.0
        mean = float(port.mean() * 252) if len(port) else 0.0
        sharpe = (mean / vol) if vol else 0.0
        downside = port[port < 0]
        dvol = float(downside.std() * math.sqrt(252)) if len(downside) else 0.0
        sortino = (mean / dvol) if dvol else 0.0
        cum = (1 + port).cumprod()
        peak = cum.cummax()
        dd = (cum / peak - 1).min() if len(cum) else 0.0
        var95 = float(np.percentile(port, 5)) if len(port) else 0.0

        artifact = {
            "skill": "clawdfolio",
            "source": "vendor/skills/clawdfolio",
            "note": "Лёгкий risk snapshot по мотивам clawdfolio (без pip install пакета)",
            "tickers": tickers[:8],
            "metrics": {
                "vol_ann": round(vol, 4),
                "mean_ann": round(mean, 4),
                "sharpe": round(sharpe, 4),
                "sortino": round(sortino, 4),
                "max_drawdown": round(float(dd), 4),
                "var_95_1d": round(var95, 4),
            },
            "ts": store.utc_now(),
        }
        path = _write_artifact("clawdfolio", artifact)
        store.append_event(
            "skill_end",
            f"clawdfolio: sharpe={artifact['metrics']['sharpe']} dd={artifact['metrics']['max_drawdown']}",
            skill="clawdfolio",
            data={"artifact": str(path)},
        )
        return {"ok": True, "skill": "clawdfolio", "artifact": str(path), "metrics": artifact["metrics"]}
    except Exception as exc:
        if soft:
            return skillmd_step(
                "clawdfolio",
                summary=f"fallback SKILL.md ({exc})",
                artifact={"error": str(exc)},
                status="soft",
            )
        raise


def vendor_skillmd_batch(tickers: list[str]) -> list[dict]:
    """Register remaining SKILL.md-driven vendor skills + light runnable ones."""
    rows: list[dict] = []

    # runnable-ish / light
    rows.append(
        {
            "name": "earnings-calendar",
            "result": _fmt(earnings_calendar_step(tickers, soft=True)),
            "status": "ok",
            "group": "vendor",
        }
    )
    rows.append(
        {
            "name": "clawdfolio",
            "result": _fmt(clawdfolio_risk_step(tickers, soft=True)),
            "status": "ok",
            "group": "vendor",
        }
    )

    # pure SKILL.md steps with artifacts pointing at cloned docs
    for name, blurb in (
        (
            "drawdown-circuit-breaker",
            "circuit-breaker gate (SKILL.md + scripts; paper: зафиксирован шаг)",
        ),
        (
            "market-environment-analysis",
            "режим рынка (SKILL.md); артефакт в state/skills/",
        ),
        (
            "exposure-coach",
            "экспозиция портфеля (SKILL.md); артефакт в state/skills/",
        ),
        (
            "earnings-preview",
            "pre-earnings briefing (finance-skills); SKILL.md-driven",
        ),
        (
            "company-valuation",
            "DCF/relative valuation (finance-skills); SKILL.md-driven",
        ),
    ):
        r = skillmd_step(
            name,
            summary=blurb,
            artifact={
                "tickers": tickers,
                "vendor_root": str(VENDOR_SKILLS_DIR),
                "skill_md": str((EXTERNAL_SKILLS.get(name) or {}).get("skill_md") or ""),
            },
        )
        rows.append(
            {
                "name": name,
                "result": r.get("summary") or blurb,
                "status": "ok" if r.get("ok") else "soft",
                "group": "vendor",
            }
        )
    return rows


def _fmt(result: dict) -> str:
    if result.get("skipped"):
        return f"skip ({result.get('error') or 'soft'})"
    if result.get("summary"):
        return str(result["summary"])
    if result.get("ok"):
        extra = result.get("metrics") or {}
        if extra:
            return f"ok · sharpe={extra.get('sharpe')} dd={extra.get('max_drawdown')}"
        if "rows" in result:
            return f"ok · rows={result['rows']}"
        return "ok"
    return f"exit/soft ({result.get('returncode')})"
