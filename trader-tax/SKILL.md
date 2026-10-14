---
name: trader-tax
description: >-
  Tax Optimizer. Tracks cost-basis lots for every position (FIFO, highest
  cost first, or specific lots), shows unrealized gains and losses by lot with
  short or long term, works out what a sale would realize before you sell,
  finds tax-loss harvesting candidates over a loss threshold, checks the wash
  sale rule (30 days before and after, GOOG and GOOGL as one family), and
  writes harvest sells only as proposals. Not tax advice. Use when the user
  says "what are my gains", "cost basis", "tax lots", "tax-loss harvest",
  "what would selling cost me in tax", "short or long term", "wash sale", or
  before selling anything.
argument-hint: "[ticker]"
---

# trader-tax

The Tax Optimizer does the arithmetic on the user's lots so nobody sells
blind. It never decides what is right for the user's taxes. **This is not tax
advice**: a tax professional decides what applies to them. Say that once,
early, in every answer that uses this skill.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/lots.py" report --lots              # every lot, gain or loss, term
python3 "${CLAUDE_SKILL_DIR}/lots.py" sell KO 2 --price 90.11    # what a sale would realize (FIFO)
python3 "${CLAUDE_SKILL_DIR}/lots.py" sell KO 2 --method specific --lots KO-2
python3 "${CLAUDE_SKILL_DIR}/lots.py" harvest --min-loss 100     # candidates, with wash-sale checks
python3 "${CLAUDE_SKILL_DIR}/lots.py" harvest --min-loss 100 --write
python3 "${CLAUDE_SKILL_DIR}/lots.py" wash KO                    # is buying KO today a wash sale?
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/portfolio.json` | positions with lots: `{"id", "date", "shares", "cost"}` (cost = total basis); shape: `portfolio.example.json` here |
| `trader/prices.json` | current prices (trader-alerts: `alerts.py quote --holdings --write`) |
| `trader/orders.json` | planned buys count for wash sales; `harvest --write` replaces it with sell proposals |
| `trader/state/tax.json` | the realized sales it logs with `sell --record` |

Lots come from the fills trader-broker records (`ticket.py fill`), or from the
user's broker statement. If a position has shares but no lots, ask for the
purchase dates and costs. Never invent a cost basis.

## The rules it applies (US federal, arithmetic only)

- **Term**: long term if sold more than one year after the purchase date,
  otherwise short term. `report --lots` shows the date each lot turns long term.
- **Lot methods**: `fifo` (oldest first, what most brokers do by default),
  `hifo` (highest cost first) and `specific` (named lots). The order sent to
  the broker does not carry lot ids: to sell specific lots the user must
  choose them in the broker's own tax-lot setting, or the broker uses its
  default method.
- **Wash sale**: a loss is disallowed to the extent the same or a
  substantially identical stock is bought within 30 days before or after the
  sale. The tool checks the other lots still held, planned buys in
  `trader/orders.json` and the loss sales it has logged. It cannot see an IRA,
  a spouse's account or another broker, and those count too. Say so.

## Steps

1. Refresh prices, then `lots.py report --lots`. Show gains and losses by
   stock, and which lots are short or long term.
2. Before any sell (rebalance, a stop, a change of mind), run `lots.py sell`
   with the planned shares and method and show what it would realize.
3. For harvesting: `lots.py harvest --min-loss 100` (and `--min-pct` if the
   user wants a percent threshold). For each candidate show the estimated
   loss, short and long term split, any wash-sale conflict, and the first day
   they could buy it back. Candidates with a conflict are not proposed.
4. Only if the user wants proposals: `harvest --write`, then `/trader-risk`,
   then `/trader-broker`, one yes per order.
5. After a sale fills, `lots.py sell ... --record` updates the lots and logs
   the realized result for the year.

## Example: the reel's portfolio

The copy portfolio bought on 2026-10-13 (AAPL $1,250, AXP $1,119, GOOGL $823,
KO $708, BAC $600) has one lot per stock, all short term until 2027-10-14.
If BAC later trades below its $600 basis, `harvest --min-loss 25` lists the
BAC-1 lot with its loss and "do not buy BAC again before" a date 31 days
after the sale. If the user also has a BAC buy planned in `trader/orders.json`,
it flags a wash sale instead of proposing the sell.

## What it never does

It never files anything, never gives a tax rate or a tax bill, never tells
the user what they owe, never places an order, and never proposes a harvest
that it can see would be a wash sale.

## Safety

- Not tax advice, and education and tooling, not financial advice. Check with a tax professional.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
