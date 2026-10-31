"""Resolved paths for the NITUB platform."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NITUB_DIR = Path(__file__).resolve().parent
STATE_DIR = NITUB_DIR / "state"
SESSIONS_DIR = ROOT / "paper-trading"
TRADER_DIR = ROOT / "trader"
VENDOR_DIR = ROOT / "vendor"
VENDOR_SKILLS_DIR = VENDOR_DIR / "skills"
CLAUDE_SKILLS_DIR = ROOT / ".claude" / "skills"

# Core trader-* Python CLIs (11)
SKILLS = {
    "copycat": ROOT / "trader-copycat" / "edgar13f.py",
    "news": ROOT / "trader-news" / "news.py",
    "budget": ROOT / "trader-budget" / "size.py",
    "pelosi": ROOT / "trader-pelosi" / "ptr.py",
    "analyst": ROOT / "trader-analyst" / "facts.py",
    "screener": ROOT / "trader-screener" / "screen.py",
    "risk": ROOT / "trader-risk" / "check.py",
    "broker": ROOT / "trader-broker" / "ticket.py",
    "tax": ROOT / "trader-tax" / "lots.py",
    "alerts": ROOT / "trader-alerts" / "alerts.py",
    "rebalance": ROOT / "trader-rebalance" / "rebalance.py",
}

CORE_SKILL_NAMES = [
    "alerts",
    "copycat",
    "news",
    "pelosi",
    "analyst",
    "screener",
    "budget",
    "risk",
    "broker",
    "tax",
    "rebalance",
]

# Vendor / Claude skills (cloned under vendor/skills, linked in .claude/skills)
EXTERNAL_SKILLS = {
    "earnings-calendar": {
        "source": "tradermonty/claude-trading-skills",
        "skill_md": VENDOR_SKILLS_DIR
        / "claude-trading-skills"
        / "skills"
        / "earnings-calendar"
        / "SKILL.md",
    },
    "drawdown-circuit-breaker": {
        "source": "tradermonty/claude-trading-skills",
        "skill_md": VENDOR_SKILLS_DIR
        / "claude-trading-skills"
        / "skills"
        / "drawdown-circuit-breaker"
        / "SKILL.md",
    },
    "market-environment-analysis": {
        "source": "tradermonty/claude-trading-skills",
        "skill_md": VENDOR_SKILLS_DIR
        / "claude-trading-skills"
        / "skills"
        / "market-environment-analysis"
        / "SKILL.md",
    },
    "exposure-coach": {
        "source": "tradermonty/claude-trading-skills",
        "skill_md": VENDOR_SKILLS_DIR
        / "claude-trading-skills"
        / "skills"
        / "exposure-coach"
        / "SKILL.md",
    },
    "earnings-preview": {
        "source": "himself65/finance-skills",
        "skill_md": VENDOR_SKILLS_DIR
        / "finance-skills"
        / "plugins"
        / "market-analysis"
        / "skills"
        / "earnings-preview"
        / "SKILL.md",
    },
    "company-valuation": {
        "source": "himself65/finance-skills",
        "skill_md": VENDOR_SKILLS_DIR
        / "finance-skills"
        / "plugins"
        / "market-analysis"
        / "skills"
        / "company-valuation"
        / "SKILL.md",
    },
    "clawdfolio": {
        "source": "YichengYang-Ethan/clawdfolio",
        "skill_md": VENDOR_SKILLS_DIR / "clawdfolio" / "SKILL.md",
    },
}

VENDOR_SKILL_NAMES = list(EXTERNAL_SKILLS.keys())

MODE_ENV = "NITUB_MODE"
DEFAULT_MODE = "paper"


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    (TRADER_DIR / "state").mkdir(parents=True, exist_ok=True)


def mode() -> str:
    return (os.environ.get(MODE_ENV) or DEFAULT_MODE).strip().lower()
