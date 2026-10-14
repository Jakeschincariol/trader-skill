---
name: trader-rebalance
description: >-
  Rebalancer. Brings the portfolio back to its target weights when it
  drifts: values every holding at current prices, works out each stock's
  drift from its target in percentage points, trades only the stocks outside
  a band (default 5 points), keeps the cash buffer and the per-stock cap,
  skips tiny trades, lists sells before buys, and writes the trades to
  trader/orders.json as proposals. Use when the user says "rebalance",
  "my portfolio drifted", "bring it back to the targets", "I added money",
  "the new 13F changed the weights", "am I overweight", or on a schedule
  (monthly or quarterly) after /trader-copycat updates the targets.
argument-hint: "[band in points]"
---

# trader-rebalance

The Rebalancer keeps the portfolio looking like the plan. Prices move, new
money arrives and a new 13F changes the weights; this skill finds the stocks
that drifted too far and proposes the trades that bring them back. It writes
proposals only: trader-risk checks them and trader-broker asks the user yes
or no for each one.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/rebalance.py"                    # the drift table, no files written
python3 "${CLAUDE_SKILL_DIR}/rebalance.py" --band 5 --write   # proposals -> trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/rebalance.py" --sell-untracked   # also sell holdings not in the targets
python3 "${CLAUDE_SKILL_DIR}/rebalance.py" --json
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/portfolio.json` | cash and positions (shares); same shape as trader-tax's `portfolio.example.json` |
| `trader/prices.json` | a price for every holding and every target (trader-alerts writes it) |
| `trader/targets.json` | the target weights (trader-copycat writes it) |
| `trader/config.json` | cash buffer, cap, min and max order, `rebalance_band_pct` |
| `trader/orders.json` | the proposals it writes with `--write`, sells first |

## How it decides

- **Account value** = cash + every holding at its price.
- **Targets**: the weights renormalised over the stock sleeve (account minus
  the cash buffer), with no stock above `max_position_pct` of the account. The
  excess is shared out, the same way trader-budget does it, so a capped stock
  is not bought back over the cap every month.
- **Drift** = current percent of the account minus target percent.
- **Band**: only stocks drifting MORE than the band trade (default 5 points).
  A trade takes the stock all the way back to its target. A small band means
  more trades; a wide band means fewer.
- **Sells first**, then buys. Buys only spend cash above the buffer; if there
  is not enough, all buys are scaled down by the same share.
- Trades under `min_order_usd` are skipped. A trade over `max_order_usd` is
  cut to the max with a note to run again after it fills.
- **Untracked** holdings (not in the targets) are left alone and listed,
  unless the user asks to sell them with `--sell-untracked`.

## Steps

1. Refresh prices: trader-alerts `alerts.py quote --holdings --write`.
2. Run `rebalance.py` and show the drift table in plain words: which stocks
   are over or under, by how many points, and what it would trade.
3. Before any sell, run trader-tax (`lots.py sell TICKER SHARES`) and show
   what the sale would realize. Selling a winner can mean a tax bill.
4. If the user wants the trades: `rebalance.py --write`, then `/trader-risk`,
   then `/trader-broker`, one yes per order.

## Example: the reel's portfolio

The copy portfolio bought on 2026-10-13 (AAPL $1,250 capped, AXP $1,119,
GOOGL $823, KO $708, BAC $600, $500 cash), valued at that day's closes, sits
on its targets: every drift is under 0.1 point, so `rebalance.py` says
"Nothing is outside the band" and exits 1. If the user adds $2,000 of cash,
the account is $7,000 and the cap becomes $1,750. AAPL is now 7.1 points under
its target and AXP 6.4, both outside the 5-point band, so it proposes buying
$499 of AAPL and $446 of AXP. GOOGL, KO and BAC are under target too, but
inside the band, so they wait. Add $5,000 instead and all five are outside
the band: five buys that leave the $1,000 buffer in cash.

## What it never does

It never places an order, never trades inside the band to chase small
differences, never sells holdings outside the targets unless asked, and
never says a rebalance will improve returns.

## Safety

- Education and tooling, not financial advice. A rebalance follows the user's own targets, not a forecast.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
