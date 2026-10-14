---
name: trader-risk
description: >-
  Risk Manager. Checks every proposed order against your limits before it can
  reach the broker and blocks anything over them: blocked tickers, max order
  size, minimum order, max position percent after the fill, the cash buffer,
  margin, short sales, options and crypto (off unless allowed), malformed
  orders, duplicates and a daily order limit. Prints PASS or BLOCK with the
  reason for each order and records the verdicts so trader-broker only shows
  an order that passed. Use when the user says "check my orders", "is this
  safe", "risk check", "does this break my rules", "can I place this", or
  before /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate between a proposal and the broker. Every order
written by trader-budget, trader-rebalance or trader-tax passes through it.
A BLOCK is final for that order: it does not go to the user for a yes, and
it does not go to the broker.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"             # trader/ files; writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write  # look without recording
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders |
| `trader/config.json` | the limits (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings; if missing, a fresh account funded with the budget |
| `trader/prices.json` | needed to size share-count orders and value holdings |
| `trader/state/broker.json` | what was placed today, for duplicates and the daily limit |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

## The checks

Orders are checked in file order, as if each passed order had filled, so a
batch that is fine order by order but breaks a rule together is caught.

| check | blocks when |
| --- | --- |
| format | no symbol, side not buy or sell, both or neither of qty and notional, a notional order that is not market + day (Alpaca's rule), a limit order with no limit price |
| asset | an option symbol while `allow_options` is false; crypto or anything that is not a US stock or ETF ticker |
| blocked | the ticker is on the `blocked` list |
| size | under `min_order_usd` or over `max_order_usd` |
| position | the stock would be over `max_position_pct` of the account after the fill |
| cash | the buy would leave less than the cash buffer, or need margin (`allow_margin` false) |
| short | a sell of more than you hold (`allow_short` false) |
| duplicate | the same symbol and side twice in one batch, or already placed today |
| daily limit | more than `max_orders_per_day` placed today |

Warnings do not block, but say them to the user: a LIVE account, a number
sent as a number instead of a string, an advanced (bracket) order.

## Steps

1. Make sure `trader/prices.json` is fresh if any order is a share count or
   any holding needs a value (trader-alerts: `alerts.py quote --holdings --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order. For a BLOCK, give
   the reason and the fix (a smaller amount, a different stock, or a rule the
   user may decide to change). Never change a limit yourself.
4. If the orders change in any way, run the check again. trader-broker matches
   each order to its verdict by fingerprint, so an edited order is not ready
   until it passes again, and a verdict from yesterday does not count today.

## Example: the reel

The five buys from trader-budget (AAPL $1,250, AXP $1,119, GOOGL $823, KO
$708, BAC $600) on a fresh $5,000 paper account with the example config:

```
PASS  #1 buy AAPL $1,250
PASS  #2 buy AXP $1,119
PASS  #3 buy GOOGL $823
PASS  #4 buy KO $708
PASS  #5 buy BAC $600
5 passed, 0 blocked. Cash after the passed orders: $500 (paper account).
```

AAPL lands exactly on the 25% cap ($1,250 of $5,000), which is allowed. A
sixth order, another $100 of AAPL, is blocked three ways: it would dip into
the $500 cash buffer, put AAPL over its 25% cap, and repeat AAPL in the same
batch.

## What it never does

It never places an order, never edits an order to make it pass, never
loosens a limit, and never treats a PASS as the user's yes. A PASS only means
the order is allowed to be shown to the user.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
