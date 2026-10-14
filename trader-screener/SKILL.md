---
name: trader-screener
description: >-
  The Stock Screener. Checks a list of companies against the user's own rules
  (revenue growth, margins, EPS, P/E, earnings yield, sector, anything in the
  metrics file), says exactly which rule each company passes or fails and by
  how much, and ranks the ones that pass into a shortlist. Reads the metrics
  /trader-analyst writes from SEC filings, or any CSV. Use when the user says
  "screen for stocks", "find me companies that", "filter my watchlist",
  "which of these pass my rules", "shortlist candidates", "rank these
  stocks", or "growing companies under 30 times earnings".
argument-hint: "[metrics file] [--rules rules.json]"
---

# trader-screener

A screen turns "I want profitable companies that are still growing and not
too expensive" into rules a computer can check, and shows its work: every
pass and every fail comes with the number and the threshold.

Tool in this folder (standard library Python, no network):

```bash
python3 "${CLAUDE_SKILL_DIR}/screen.py" --help
```

## What it reads and writes

- Metrics: `trader/metrics.json` from `/trader-analyst` (`facts.py --metrics`),
  or any JSON list or CSV with a `ticker` column. CSV numbers may carry `$`,
  `%` and commas.
- Prices (optional): `trader/prices.json` (`{"prices": {"AAPL": 341.27}}`,
  any source; `/trader-alerts` writes it). With prices it adds `price`, `pe`
  (price / trailing EPS, only when EPS is positive) and `earnings_yield`.
- Rules: a JSON file, `rules.example.json` in this folder to start from.
- Writes `trader/shortlist.json` with `--out`: who passed, their score and
  why, who failed and why.

## The rules file

```json
{"name": "Growing, profitable, not priced for perfection",
 "rules": [{"metric": "revenue_growth_yoy", "op": ">=", "value": 10},
           {"metric": "operating_margin", "op": ">=", "value": 25},
           {"metric": "pe", "op": "<=", "value": 35}],
 "rank":  [{"metric": "revenue_growth_yoy", "order": "desc", "weight": 2},
           {"metric": "pe", "order": "asc", "weight": 1}],
 "missing": "fail"}
```

Ops: `>`, `>=`, `<`, `<=`, `==`, `!=`, `between` ([low, high]), `in` and
`not in` (a list, for tickers or sectors to leave out). `"missing": "fail"`
fails a company that lacks a metric a rule needs (banks have no operating
margin in SEC's facts); `"skip"` ignores that rule for it and says so.
Ranking scores each passing company 0 to 1 per rank metric and takes the
weighted average.

## Steps

1. Turn the user's words into rules. Ask when a threshold is vague ("cheap"
   means what P/E?). Write them to `trader/rules.json`.
2. Get metrics for the candidates: run `/trader-analyst` on them with
   `--metrics trader/metrics.json` (its `facts.py AAPL AXP GOOGL KO BAC
   --metrics trader/metrics.json`), or use the user's own CSV. Prices from
   `trader/prices.json` if P/E matters.
3. Run the screen:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/screen.py" trader/metrics.json --rules trader/rules.json \
     --prices trader/prices.json --out trader/shortlist.json
   ```

   Exit code 1 means nothing passed: say so and show the closest misses.
4. Report the shortlist with the reasons, the misses with the numbers that
   failed, and the dates the numbers are from (the latest quarter end per
   company, the price date). Suggest `/trader-analyst` on the top names before
   anything else.

## Example: the reel's five

Metrics from SEC for AAPL, AXP, GOOGL, KO and BAC, prices at the 2026-10-13
close, and `rules.example.json`:

```
Growing, profitable, not priced for perfection: 2 of 5 companies pass.
 1. PASS GOOGL  Alphabet Inc.  score 0.75   (growth 24.2, P/E 17.8)
 2. PASS KO     COCA COLA CO   score 0.25   (growth 12.1, P/E 28.3)
    FAIL AAPL   pe 39.2, needs <= 35
    FAIL BAC    operating_margin is missing
    FAIL AXP    revenue_growth_yoy 8.6, needs >= 10; operating_margin is missing
```

Then the caveats a careful analyst adds: Alphabet's latest quarter has net
income above operating income (gains outside the business), and Coca-Cola's
numbers stop at the quarter ending 2026-04-03 because SEC's facts lag its
latest 10-Q.

## What it never does

- Never calls a passing company a buy. A screen is a filter on reported
  numbers, not a recommendation.
- Never fills in a missing number or guesses a metric: missing means missing.
- Never sends anything to `/trader-budget` or an order path by itself.

## Safety

Education and tooling, not financial advice. This skill only filters numbers.
Nothing places an order except `/trader-broker`, after `/trader-risk` passes
and the user types an explicit yes for that order.
