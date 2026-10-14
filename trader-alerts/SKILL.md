---
name: trader-alerts
description: >-
  Price Alerts. Rings when a price target, a stop or the opening bell hits:
  above / below / stop rules, percent moves from the previous close, and the
  NYSE opening bell, each ringing once so you are not spammed. Knows NYSE
  hours with the 2026 and 2027 holidays and 1:00 p.m. early closes, fetches
  free delayed quotes (Yahoo chart JSON, Stooq CSV) one ticker at a time, and
  writes trader/prices.json for the other skills. Use when the user says
  "alert me when", "tell me if AAPL hits", "set a stop", "price target",
  "watch my stocks", "is the market open", "when does the market open",
  "get me prices", or to refresh prices before sizing, risk or tax.
argument-hint: "[ticker] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to. An alert is
information, never an order: when a stop rings, nothing is sold. If the user
wants to act, the normal path runs (size, risk, their yes).

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                         # NYSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote AAPL KO --write         # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write      # portfolio + targets tickers
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol AAPL --above 350
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol BAC --stop 50 --note "my stop"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol KO --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                         # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset aapl-above-350
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes, merged in, for trader-budget, trader-risk, trader-tax and trader-rebalance |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `opening_bell` | the NYSE is open | once per trading day |

## Quotes

- `yahoo` (default): `query1.finance.yahoo.com/v8/finance/chart/<TICKER>`, an
  unofficial free JSON endpoint, one request per ticker, half a second apart.
- `stooq`: `stooq.com/q/l/?s=aapl.us&f=sd2t2ohlcv&h&e=csv`, Stooq's free CSV.
  It answered 404 when this was written (2026-10-13); the parser stays for
  when it is back. Use `--source stooq` to try it.
- Free quotes are delayed. They are fine for alerts and for checking sizes,
  not for timing an order. The broker's price is the one that fills.
- If both are down, read prices from the broker connector (Alpaca
  `get_stock_snapshot`, IBKR `get_price_snapshot`) and write them into
  `trader/prices.json` as `{"as_of": "...", "prices": {"AAPL": 341.27}}`.

## The NYSE clock

Regular session 9:30 a.m. to 4:00 p.m. New York time, closed on weekends and
on these holidays, from nyse.com/markets/hours-calendars (read 2026-10-13):

- 2026: Jan 1, Jan 19, Feb 16, Apr 3 (Good Friday), May 25, Jun 19, Jul 3
  (Independence Day observed), Sep 7, Nov 26, Dec 25. Early close 1:00 p.m. on
  Nov 27 and Dec 24.
- 2027: Jan 1, Jan 18, Feb 15, Mar 26 (Good Friday), May 31, Jun 18
  (Juneteenth observed), Jul 5 (Independence Day observed), Sep 6, Nov 25,
  Dec 24 (Christmas observed). Early close 1:00 p.m. on Nov 26.

Daylight saving time is worked out in the tool, so it needs no time zone
files. For any other year it says the holiday list is unknown.

## Steps

1. Ask what to watch, in the user's words ("tell me if Apple drops under
   $320"), and add the rule. Confirm it back: ticker, type, level.
2. Run `check` now, and on a schedule if the user wants (a Claude scheduled
   task or cron, every 15 minutes in market hours is plenty). Each run fetches
   only the tickers in the rules and rings each alert once.
3. Tell the user what rang in one line each. If they want to act, send them
   to sizing and `/trader-risk`. Never act for them.

## Example: the reel's portfolio

After the copy portfolio is bought, `add --opening-bell`, `add --symbol AAPL
--above 350`, and `add --symbol BAC --stop 50`. On 2026-10-13 AAPL closed at
$341.27 and BAC at $53.96, so `check` that evening prints "Nothing hit" and
exits 1. At 9:30 the next morning the opening bell rings once.

## What it never does

It never places, cancels or changes an order, never claims a quote is
real time, and never predicts where a price is going.

## Safety

- Education and tooling, not financial advice. An alert is a fact about a price, not a signal to trade.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
