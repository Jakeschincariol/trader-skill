---
name: trader-tax-au
description: >-
  Australian Tax Optimizer. Capital gains tax arithmetic for an individual
  Australian tax resident holding ASX shares and ETFs: records each parcel
  from contract notes (cost base including brokerage, AMIT adjustments from
  ETF tax statements), shows every parcel's unrealised gain and the date it
  qualifies for the 50% CGT discount, works out what a sale would realise and
  which parcels keep the tax lowest, totals the financial year (1 July to 30
  June) the ATO way (losses before the discount, against undiscounted gains
  first, carried-forward losses), lists tax-loss candidates before 30 June
  with wash-sale cautions, and gains about to qualify for the discount. Not
  tax advice. Use when the user says "what are my capital gains", "CGT",
  "cost base", "which parcels should I sell", "will I get the discount",
  "tax-loss harvest before June", "net capital gain this year", "AMIT", "wash
  sale", or before selling any ASX holding.
argument-hint: "[code] [shares]"
---

# trader-tax-au

The Australian Tax Optimizer does the capital gains arithmetic on the user's
own parcels so nobody sells blind. It never decides what is right for the
user's tax. **This is not tax advice**: say that once, early, in every answer
that uses it, and point to a registered tax agent for anything that matters.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/cgt.py" buy VAS 10 --price 104.55 --fee 0 --date 2026-10-21
python3 "${CLAUDE_SKILL_DIR}/cgt.py" adjust VAS --lot VAS-1 --amount -3.20 --note "AMIT FY2025-26"
python3 "${CLAUDE_SKILL_DIR}/cgt.py" report
python3 "${CLAUDE_SKILL_DIR}/cgt.py" sell VAS 10 --price 110            # lowest-tax parcels first
python3 "${CLAUDE_SKILL_DIR}/cgt.py" sell VAS 10 --method fifo
python3 "${CLAUDE_SKILL_DIR}/cgt.py" sell VAS 10 --record                # only after the sale is placed
python3 "${CLAUDE_SKILL_DIR}/cgt.py" year --fy FY2026-27 --carried 400 --rate 32.5
python3 "${CLAUDE_SKILL_DIR}/cgt.py" harvest --min-loss 100 [--write]
python3 "${CLAUDE_SKILL_DIR}/cgt.py" waits --days 60
python3 "${CLAUDE_SKILL_DIR}/cgt.py" wash BHP
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/portfolio.json` | holdings with lots `{"id", "date", "shares", "cost", "adjustments"}`; same file as trader-risk and trader-rebalance (their `cmc_import.py` keeps lots when the share count matches) |
| `trader/prices.json` | current AUD prices |
| `trader/state/tax-au.json` | recorded sales by financial year, and `"carried_forward": {"FY2026-27": 400}` if the user tells you |
| `trader/orders.json` | `harvest --write` replaces it with sell proposals |

## Getting the cost base

The CMC holdings export has no cost base. Ask the user for each buy from their
contract notes (date, shares, price, brokerage) and record it with `buy`.
Never invent a date or a cost. ETF holders get an annual tax statement with
an AMIT cost base net amount: record it with `adjust` (+ raises the cost base,
- lowers it). If parcels are missing, say the estimate is incomplete.

## The rules it applies (checked on ato.gov.au, 2026-10-21)

- **Cost base**: price paid plus brokerage, adjusted by AMIT amounts.
- **CGT event date**: the trade (contract) date of the sale, not settlement.
- **50% discount**: for an Australian resident individual, on a parcel held
  at least 12 months, counting neither the day bought nor the day sold. So a
  parcel bought 2025-10-20 qualifies from a sale on 2026-10-21.
- **Choosing parcels**: allowed when the parcels can be identified. `min-tax`
  (default) sells losses first, then the smallest gain after the discount.
  Tell the user to keep their records consistent with what they choose.
- **The year**: 1 July to 30 June. Losses (this year's, then carried forward)
  come off gains before the discount, and go against the undiscounted gains
  first; the discount halves what is left of the discountable gains. A net
  capital loss is never deducted from other income; it carries forward.
- **Wash sales**: no fixed window. Selling at a loss and buying back the same
  or substantially the same asset soon after, with no real change in
  exposure, can have the loss cancelled (ATO TR 2008/1). Two ETFs on the same
  index may count as substantially the same: say so.
- **Tax dollars**: only with `--rate`, the marginal rate the user gives. Never
  look up or assume their income.

Not covered, and say so if it applies: non-residents and temporary residents,
share traders (shares as trading stock), companies, trusts and SMSFs,
employee share schemes, rights and options, scrip-for-scrip rollovers, and
foreign shares or crypto in foreign currency.

## Steps

1. Make sure the parcels exist (`report`). If not, collect them (`buy`).
2. Answer the question with the right command and show the numbers in plain
   words: which parcels, the gain, how much gets the discount, the financial
   year, any parcel that qualifies soon (`waits`).
3. Before any sale, run `sell` without `--record`. Record only after the user
   confirms the sale was placed, with the real price and brokerage.
4. Harvest proposals go to `/trader-risk`, then the user decides each one and
   places it in CMC. A tax saving is never a reason on its own to sell.

## What it never does

It never places an order, never files anything with the ATO, never assumes a
tax rate, residency or cost base, and never says a sale is a good idea.

## Safety

- Education and arithmetic, not financial advice and not tax advice. A registered tax agent decides what applies.
- Proposals only. Nothing in this skill places an order: the user says yes to each one and enters it in CMC themselves.
- No options, margin, short selling or leverage.
- Personal records stay in the user's own `trader/` folder.
