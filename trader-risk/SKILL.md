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
  before /trader-broker, always. Has an ASX mode for CMC Invest in AUD: ASX
  codes, whole shares, price steps, the $500 parcel, CMC brokerage, T+2,
  Australian CGT and wash-sale warnings, per-trade risk to a stop, and an
  importer for the CMC holdings CSV ("check my ASX orders", "import my CMC
  holdings").
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

## ASX mode (CMC Invest, Australia)

Set `"market": "asx"` in `trader/config.json` when the user trades ASX shares
or ETFs (for example through CMC Invest) in Australian dollars. If there is no
config yet, write this one and go through each value with the user in plain
words (budget, buffer, cap, minimum and maximum order, brokerage):

```json
{"market": "asx", "broker": "cmc", "budget": 5000, "cash_buffer_pct": 10,
 "max_position_pct": 25, "max_order": 5000, "min_order": 500,
 "max_orders_per_day": 10, "fractional": false, "blocked": [],
 "asx_allow_codes": [],
 "brokerage": {"free_first_buy_upto": 1000, "min": 11, "pct": 0.11},
 "max_fee_pct": 2, "max_risk_per_trade_pct": null, "require_stop": false,
 "rebalance_band_pct": 5, "order_type": "market", "time_in_force": "day",
 "allow_options": false, "allow_margin": false, "allow_short": false}
```

What changes, and where each rule comes from (checked 2026-10-21; tell the
user to confirm anything that matters, rules change):

| rule | what the skill does | source |
| --- | --- | --- |
| whole shares | qty only, whole numbers; no dollar (notional) orders | CMC buys ASX shares in units |
| ASX codes | `BHP.AX`, `ASX:BHP` become `BHP`; 3-character codes pass; 4-6 characters (BGBL, ETPMPM) only once listed in `asx_allow_codes`, because 4-6 characters can also be options, warrants, rights or partly paid shares | asx.com.au, understanding ASX ticker codes |
| marketable parcel | a new holding (or a top-up of one under $500) must reach $500 | CommSec and Selfwealth help pages |
| price steps | limit and stop prices on 0.1c (under 10c), 0.5c (10c to $2), 1c ($2 and up) | asx.com.au, ASX equities trading |
| brokerage | CMC standard: first buy of each stock per day up to $1,000 free; otherwise $11 or a percentage, whichever is higher. CMC's own page says 0.11 %, Finder and Snowball say 0.10 %: ask the user to confirm and set `brokerage.pct` | cmcmarkets.com, finder.com.au, snowball-invest.com.au |
| settlement | sales settle T+2, so buys funded by a sale in the same batch get a note | ASX T+2 settlement calendar 2026 |
| CGT discount | an Australian-resident individual halves the gain on a parcel held at least 12 months, counting neither the buy day nor the sale day; the sale date is the trade date | ato.gov.au, CGT discount |
| wash sales | no fixed window: selling at a loss and buying back soon after can have the loss cancelled (Part IVA) | ATO ruling TR 2008/1 |
| validity | `day`, `gtc`, or `gtd` with an `expire_date` | CMC order expiry guide |

Holdings that are not in AUD (crypto, overseas shares) count in the account
value but are never traded. The orders are tickets the user types into CMC
themselves; nothing in this pack can reach a CMC account, and trader-broker
is not used.

Prices: `trader/prices.json` must hold a real AUD price for every code. The
CMC holdings export gives the last price of what the user holds (see
`cmc_import.py` in trader-risk and trader-rebalance); codes they do not hold
yet are added by hand from CMC or the ASX site. Say how old the prices are.
Never guess one.

In this skill, ASX mode also checks: whole-share qty, the ASX code, price
steps on `limit_price`, `stop_price`, `stop_loss` and `take_profit`, the $500
parcel, brokerage inside the cash check (and a warning when the fee is over
`max_fee_pct` of the order), `day` / `gtc` / `gtd` validity. Warnings (not
blocks): an estimate of the capital gain on each sale from the lots in
`trader/portfolio.json`, with which parcels get the 50 % discount and any
lot that qualifies within 120 days; a buy within 30 days of a recorded loss
sale of the same code (`trader/state/tax.json`); a buy that leans on sale
money that only settles T+2; a sale that leaves less than $500. Small sales
are not blocked by `min_order` (it is a buy rule on the ASX).

Trade risk (any market): put `"stop_loss"` (and optionally `"take_profit"`)
on a buy to see the loss if the stop is hit, brokerage both ways included, as
a percent of the account, and the reward-to-risk ratio. With
`"max_risk_per_trade_pct": 3` a buy risking more than 3 % of the account is
blocked; with `"require_stop": true` a buy with no stop is blocked.

Import the user's CMC holdings first (the export has no cash and no cost
base, so ask for the cash available to trade, and never invent lots):

```bash
python3 "${CLAUDE_SKILL_DIR}/cmc_import.py" Holdings.csv --cash 1200 --dry-run
python3 "${CLAUDE_SKILL_DIR}/cmc_import.py" Holdings.csv --cash 1200 --only BHP,CSL   # one sleeve only
python3 "${CLAUDE_SKILL_DIR}/check.py"
```

If the user keeps a long-term core and a trading sleeve apart, import only
the sleeve with `--only` (or `--exclude` the core) so the cap and the buffer
are worked out on the right money.

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
