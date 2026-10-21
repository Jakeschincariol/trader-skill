---
name: trader-analyst-au
description: >-
  The Australian Earnings Analyst. Reads an ASX company's own published
  results (Appendix 4D or 4E, half-year or annual report, results release,
  from the company's investor website or a PDF the user attaches), records
  each figure with the page it came from, then works out what changed against
  the same period last year: revenue, statutory and underlying profit, EPS,
  dividends with franking, margins, cash conversion, free cash flow, net
  debt, dilution, the derived second half, and P/E and grossed-up yield at the
  current price. Runs forensic checks (receivables outrunning revenue, profit
  not turning into cash, recurring one-offs, capitalised costs, dividends
  paid from debt) and writes a metrics file trader-screener reads. Use when
  the user says "how did CSL's results look", "analyse the half-year",
  "check the 4D", "is BHP's profit real", "compare margins", "update my
  watchlist numbers", or "run the analyst on my ASX holdings".
argument-hint: "[codes]"
---

# trader-analyst-au

Results day moves ASX stocks, and the results release is written to put the
best number first, often an "underlying" profit the company defines itself.
This skill reads what the company actually reported, puts statutory and
underlying side by side, compares each figure with the same period last year,
and says plainly what changed and what looks off.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/facts_au.py" template CSL > trader/reports/CSL.json
python3 "${CLAUDE_SKILL_DIR}/facts_au.py" check trader/reports/CSL.json
python3 "${CLAUDE_SKILL_DIR}/facts_au.py" analyse trader/reports/CSL.json --price 182.40
python3 "${CLAUDE_SKILL_DIR}/facts_au.py" analyse "trader/reports/*.json" --metrics   # -> trader/metrics.json
```

## Where the numbers come from

- **The company's own reports only**: Appendix 4E (full year) or 4D (half
  year), the annual or half-year financial report, and the results
  presentation for context. Find them on the company's investor relations
  website (web search "<company> investor centre results"), or use a PDF the
  user attaches. Do not crawl or bulk-download from asx.com.au: its terms bar
  automated access.
- **No university or paid database data.** Do not put figures from DatAnalysis,
  Connect4, IBISWorld, Morningstar or similar into the file or the chat:
  those licences generally bar personal trading use and sharing with AI
  tools. If the user offers such data, say why you will not use it.
- Every number gets its source in `sources` (document and page) and the
  document link in `url`. If a figure is not in the report, leave it out.
  Never estimate, never carry a figure from memory, and never copy a figure
  from a news article when the report is available.

## Filling the file

1. `template CODE` gives a skeleton with this period and the same period last
   year (an Appendix 4E or 4D shows both columns, so one document usually
   fills both). Add the previous half or year too if available: more periods
   mean better trend and forensic checks.
2. Figures in millions (`"units": "m"`) of the reporting currency. Some ASX
   companies report in US dollars (CSL, Woodside, Rio Tinto, James Hardie):
   set `"currency": "USD"` and `"fx_to_aud"` (AUD per USD, with its date in
   the sources) for valuation, or leave valuation out.
3. `npat` is statutory profit after tax attributable to members. Put the
   company's adjusted figure in `npat_underlying` only if they report one.
   EPS and DPS in cents; `franking_pct` from the dividend announcement;
   `shares_m` is shares on issue in millions; `debt` is borrowings (include
   lease liabilities only if the user wants them, and say which).
4. Run `check` and fix what it reports, then `analyse`.

## Presenting it

Follow the user's research template: a plain sentence first, then the
number, then GOOD / OKAY / CONCERNING, each compared with the company's own
last period and, when the user gives them, peers. Tag every figure:
[FACT] for a reported number (with its source), [DERIVED] for anything the
tool calculated (show the inputs), [JUDGEMENT] for your reading of it. The
forensic checks are leads, not verdicts: say what to read in the notes to
confirm or clear each one.

Prices come from `trader/prices.json` or `--price` (AUD). Say how old the
price is. P/E uses trailing twelve months EPS (the latest full year, or the
latest half plus the previous full year minus the previous half). The
grossed-up yield adds franking credits at the 30 % company rate (`"tax_rate":
0.25` in the file for a base rate entity); franking credits are only worth
their full value to an Australian resident who can use them.

## What it never does

It never invents or estimates a reported figure, never uses licensed database
data, never scrapes the ASX website, and never says to buy or sell.

## Safety

- Education and arithmetic, not financial advice. A strong set of numbers is not a reason on its own to buy.
- Nothing in this skill places an order: any trade goes through trader-budget, trader-risk and the user's own yes, entered in CMC by the user.
- Sources are public company documents, cited page by page.
