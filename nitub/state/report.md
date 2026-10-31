# Paper-сессия signal-10k — отчёт

**Дата:** 2026-10-31 · **Счёт:** $10000.00 USD · **Режим:** paper only
**Тезис:** Signal-driven sleeve (skills pipeline)

Это симуляция и tooling, не финсовет. Реальных ордеров и брокеров не было.

## Итог P&L

| | USD |
| --- | ---: |
| Старт | 10000.00 |
| Cash | 1000.00 |
| Позиции (MTM) | 9000.00 |
| Equity | 10000.00 |
| Unrealized P&L | 0.00 |
| Realized P&L | 0.00 |
| P&L к старту | 0.00 |

## Позиции

| тикер | shares | cost | mark | MTM | uP&L |
| --- | ---: | ---: | ---: | ---: | ---: |
| GOOGL | 4.020154 | 1125.00 | 279.84 | 1125.00 | 0.00 |
| MSFT | 2.155544 | 1125.00 | 521.91 | 1125.00 | 0.00 |
| AAPL | 3.26437 | 1125.00 | 344.63 | 1125.00 | 0.00 |
| AXP | 3.52543 | 1125.00 | 319.11 | 1125.00 | 0.00 |
| KO | 11.957908 | 1125.00 | 94.08 | 1125.00 | 0.00 |
| BAC | 22.495501 | 1125.00 | 50.01 | 1125.00 | 0.00 |
| CB | 3.089641 | 1125.00 | 364.12 | 1125.00 | 0.00 |
| CVX | 4.776664 | 1125.00 | 235.52 | 1125.00 | 0.00 |

## Почему каждый тикер (сигналы skills)

| тикер | источники | причины |
| --- | --- | --- |
| GOOGL | analyst, copycat, drawdown-circuit-breaker, earnings-calendar, market-environment-analysis, screener | screener PASS: revenue_growth_yoy 24.7 >= 10; operating_margin 31.8 >= 25; eps_ttm 19.16 > 0; pe 14.6 <= 35; copycat 13F top#3 weight=13.29; analyst: revYoY 24.7%, opMargin 31.8%, EPS 19.16; earnings-calendar: нет ближнего отчёта (≤5д); drawdown-circuit-breaker: ok; market-environment: SPY 755.72 vs SMA50 766.77 / SMA200 725.98 → risk_on |
| MSFT | analyst, drawdown-circuit-breaker, earnings-calendar, market-environment-analysis, screener | screener PASS: revenue_growth_yoy 18.8 >= 10; operating_margin 49.8 >= 25; eps_ttm 19.15 > 0; pe 27.3 <= 35; analyst: revYoY 18.8%, opMargin 49.8%, EPS 19.15; earnings-calendar: нет ближнего отчёта (≤5д); drawdown-circuit-breaker: ok; market-environment: SPY 755.72 vs SMA50 766.77 / SMA200 725.98 → risk_on |
| AAPL | analyst, copycat, drawdown-circuit-breaker, earnings-calendar, market-environment-analysis | copycat 13F top#1 weight=23.21; analyst: EPS 8.71, revYoY 16.4%; earnings-calendar: нет ближнего отчёта (≤5д); drawdown-circuit-breaker: ok; market-environment: SPY 755.72 vs SMA50 766.77 / SMA200 725.98 → risk_on |
| AXP | analyst, company-valuation, copycat, drawdown-circuit-breaker, earnings-calendar, market-environment-analysis | copycat 13F top#2 weight=18.05; analyst: EPS 17.75, revYoY 7.8%; earnings-calendar: нет ближнего отчёта (≤5д); drawdown-circuit-breaker: ok; company-valuation: PE 18.0 — относительно дёшево; market-environment: SPY 755.72 vs SMA50 766.77 / SMA200 725.98 → risk_on |
| KO | analyst, company-valuation, copycat, drawdown-circuit-breaker, earnings-calendar, market-environment-analysis | copycat 13F top#4 weight=11.44; analyst: EPS 3.4, revYoY 7.4%; earnings-calendar: нет ближнего отчёта (≤5д); drawdown-circuit-breaker: ok; company-valuation: PE 27.7 — в разумном диапазоне; market-environment: SPY 755.72 vs SMA50 766.77 / SMA200 725.98 → risk_on |
| BAC | analyst, company-valuation, copycat, drawdown-circuit-breaker, earnings-calendar, market-environment-analysis | copycat 13F top#5 weight=9.69; analyst: EPS 4.21, revYoY 3.2%; earnings-calendar: нет ближнего отчёта (≤5д); drawdown-circuit-breaker: ok; company-valuation: PE 11.9 — относительно дёшево; market-environment: SPY 755.72 vs SMA50 766.77 / SMA200 725.98 → risk_on |
| CB | analyst, company-valuation, copycat, drawdown-circuit-breaker, earnings-calendar, market-environment-analysis | copycat 13F top#8 weight=4.11; analyst: EPS 28.53, revYoY 0.3%; earnings-calendar: нет ближнего отчёта (≤5д); drawdown-circuit-breaker: ok; company-valuation: PE 12.8 — относительно дёшево; market-environment: SPY 755.72 vs SMA50 766.77 / SMA200 725.98 → risk_on |
| CVX | analyst, company-valuation, copycat, drawdown-circuit-breaker, earnings-calendar, market-environment-analysis | copycat 13F top#6 weight=4.92; analyst: EPS 10.4, revYoY 51.4%; earnings-calendar: нет ближнего отчёта (≤5д); drawdown-circuit-breaker: ok; company-valuation: PE 22.6 — в разумном диапазоне; market-environment: SPY 755.72 vs SMA50 766.77 / SMA200 725.98 → risk_on |

## Что сделал каждый skill

| skill | результат |
| --- | --- |
| **copycat** | interval skip (cycle 2, every 3) — в pipeline |
| **alerts** | quotes обновлены |
| **news** | skipped (flag) |
| **pelosi** | interval skip (cycle 2) — в pipeline |
| **analyst** | interval skip (cycle 2) — в pipeline |
| **screener** | shortlist обновлён |
| **budget** | 8 proposed orders, budget $10000 |
| **risk** | PASS ×8 |
| **broker** | paper fills ×8 (без живого брокера) |
| **tax** | lots after fills |
| **rebalance** | в пределах band — ордеров нет (exit 1 = ok) |
| **earnings-calendar** | ok · rows=12 |
| **clawdfolio** | ok · sharpe=1.6483 dd=-0.0765 |
| **drawdown-circuit-breaker** | ok · trips=0 |
| **market-environment-analysis** | SPY 755.72 vs SMA50 766.77 / SMA200 725.98 → risk_on |
| **company-valuation** | ok · valued 12 |
| **exposure-coach** | экспозиция портфеля (SKILL.md); артефакт в state/skills/ |
| **earnings-preview** | pre-earnings briefing (finance-skills); SKILL.md-driven |
| **signals** | sleeve GOOGL←analyst+copycat+drawdown-circuit-breaker+earnings-calendar+market-environment-analysis+screener, MSFT←analyst+drawdown-circuit-breaker+earnings-calendar+market-environment-analysis+screener, AAPL←analyst+copycat+drawdown-circuit-breaker+earnings-calendar+market-environment-analysis, AXP←analyst+company-valuation+copycat+drawdown-circuit-breaker+earnings-calendar+market-environment-analysis, KO←analyst+company-valuation+copycat+drawdown-circuit-breaker+earnings-calendar+market-environment-analysis, BAC←analyst+company-valuation+copycat+drawdown-circuit-breaker+earnings-calendar+market-environment-analysis, CB←analyst+company-valuation+copycat+drawdown-circuit-breaker+earnings-calendar+market-environment-analysis, CVX←analyst+company-valuation+copycat+drawdown-circuit-breaker+earnings-calendar+market-environment-analysis |

## Риски

- Paper fills по delayed Yahoo — не реальное исполнение.
- 13F / thesis могут быть устаревшими.
- Крошечный счёт: в live комиссии съели бы долю.

Education/tooling only. Paper simulation. Not financial advice. No live broker orders.
