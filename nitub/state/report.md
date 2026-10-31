# Paper-сессия skills-smoke — отчёт

**Дата:** 2026-10-31 · **Счёт:** $100.00 USD · **Режим:** paper only
**Тезис:** Research winner: copycat_static (copycat_hold)

Это симуляция и tooling, не финсовет. Реальных ордеров и брокеров не было.

## Итог P&L

| | USD |
| --- | ---: |
| Старт | 100.00 |
| Cash | 10.00 |
| Позиции (MTM) | 90.00 |
| Equity | 100.00 |
| Unrealized P&L | 0.00 |
| Realized P&L | 0.00 |
| P&L к старту | 0.00 |

## Позиции

| тикер | shares | cost | mark | MTM | uP&L |
| --- | ---: | ---: | ---: | ---: | ---: |
| AAPL | 0.10446 | 36.00 | 344.63 | 36.00 | 0.00 |
| MSFT | 0.051733 | 27.00 | 521.91 | 27.00 | 0.00 |
| GOOGL | 0.096484 | 27.00 | 279.84 | 27.00 | 0.00 |

## Что сделал каждый skill

| skill | результат |
| --- | --- |
| **research** | winner copycat_static → targets (AAPL, MSFT, GOOGL) |
| **alerts** | quotes обновлены |
| **copycat** | 13F CIK 1067983 top-3 → copycat-ref.json |
| **news** | ok |
| **pelosi** | PTR index checked |
| **analyst** | metrics ← AAPL, GOOGL, MSFT |
| **screener** | shortlist обновлён |
| **budget** | 3 proposed orders, budget $100 |
| **risk** | PASS ×3 |
| **broker** | paper fills ×3 (без живого брокера) |
| **tax** | lots after fills |
| **rebalance** | rebalance soft/skip |
| **earnings-calendar** | fallback SKILL.md (Object of type date is not JSON serializable) |
| **clawdfolio** | ok · sharpe=0.8035 dd=-0.1434 |
| **drawdown-circuit-breaker** | circuit-breaker gate (SKILL.md + scripts; paper: зафиксирован шаг) |
| **market-environment-analysis** | режим рынка (SKILL.md); артефакт в state/skills/ |
| **exposure-coach** | экспозиция портфеля (SKILL.md); артефакт в state/skills/ |
| **earnings-preview** | pre-earnings briefing (finance-skills); SKILL.md-driven |
| **company-valuation** | DCF/relative valuation (finance-skills); SKILL.md-driven |

## Риски

- Paper fills по delayed Yahoo — не реальное исполнение.
- 13F / thesis могут быть устаревшими.
- Крошечный счёт: в live комиссии съели бы долю.

Education/tooling only. Paper simulation. Not financial advice. No live broker orders.
