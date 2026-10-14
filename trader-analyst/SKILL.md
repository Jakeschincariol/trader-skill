---
name: trader-analyst
description: >-
  The Earnings Analyst. Reads a company's reported numbers straight from SEC
  EDGAR (XBRL company facts from its 10-Qs and 10-Ks, plus the latest earnings
  8-K and its press release) and sums up what changed: revenue, gross,
  operating and net margins, net income and diluted EPS, quarter by quarter,
  each against the same quarter a year earlier, with the trailing twelve
  months. Writes a metrics file the screener reads. Use when the user says
  "how did Apple's earnings look", "analyze the earnings", "what changed this
  quarter", "is revenue growing", "check the 10-Q", "compare margins", or
  "run the analyst on my holdings".
argument-hint: "[tickers]"
---

# trader-analyst

Earnings season moves stocks, and the press release is written to put the
best number first. This skill reads the numbers the company actually filed
with the SEC, lines them up quarter by quarter, and says plainly what changed.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" --help
```

It needs the SEC User-Agent (`sec_user_agent` in `trader/config.json`, or
`SEC_USER_AGENT`): "Your Name you@example.com" with the user's real name and
email. It refuses to call SEC without one.

## What it reads and writes

- Reads `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` (every
  number the company has tagged in its filings), the company's submissions
  list (for the newest 10-Q or 10-K and the newest 8-K with item 2.02,
  "Results of Operations") and that 8-K's folder to find the press release
  (the EX-99.1 exhibit).
- Writes `trader/metrics.json` with `--metrics`: one row per company (TTM
  revenue, net income and diluted EPS, the latest margins and growth) for
  `/trader-screener`. `metrics.example.json` in this folder is a real run
  for AAPL, AXP, GOOGL, KO and BAC on 2026-10-13.

## The rules

- **Filed numbers first.** The table comes from XBRL facts the company filed.
  Use the press release for context (guidance, segment colour), and say which
  numbers came from where.
- **Compare like with like.** Growth is against the same quarter a year
  earlier. Quarter-on-quarter changes are shown but many businesses are
  seasonal (Apple's December quarter is always its biggest).
- **Say what is derived.** Companies do not file a Q4 10-Q. The fourth
  quarter is the 10-K year minus the three quarters inside it and is marked
  "derived"; derived EPS is approximate.
- **Say when the data lags.** SEC's facts API can trail a filing. When the
  newest 10-Q is later than the newest quarter in the facts, the tool prints a
  NOTE with the link: read the 10-Q itself before summarising.
- **Flag one-offs.** When net income is above operating income, gains outside
  the business (investment marks, tax items) are inside it. The tool says so.
  Banks and insurers have no gross margin: blank, not zero.

## Steps

1. Run it for the companies in question (or every ticker in
   `trader/targets.json`):

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/facts.py" AAPL
   python3 "${CLAUDE_SKILL_DIR}/facts.py" AAPL AXP GOOGL KO BAC --metrics trader/metrics.json
   ```

   `--quarters 8` shows two years, `--json` everything machine-readable,
   `--cik` a company without a ticker.
2. Open the latest earnings press release (the EX-99 link) and the 10-Q when
   the numbers need context, and read them.
3. Write the summary: one line on the quarter (revenue, EPS, growth), one on
   margins, one on anything unusual (a one-off, a derived quarter, data that
   lags), and the links. Plain words, real numbers, no adjectives the numbers
   do not support.

## Example: the reel's top holding

`facts.py AAPL` (2026-10-13): quarter ending 2026-06-27 (fiscal Q3 2026),
revenue $109.42B, +16.4% against a year earlier, net income $29.79B (+27.1%),
diluted EPS $2.02 (+28.7%); gross margin 50.1% (+0.8 points on the quarter
before); last four quarters $466.8B revenue and $8.71 EPS. Press release:
the EX-99.1 of the 8-K filed 2026-07-30. For Coca-Cola the tool notes that
SEC's facts stop at 2026-04-03 while the 10-Q for 2026-07-03 is already filed,
so the summary reads that 10-Q directly.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell. It reports what
  the company filed.
- Never invents a number the filings do not have, or estimates a quarter it
  cannot derive.
- Never calls SEC without the User-Agent, and stays well under 10 requests a
  second.

## Safety

Education and tooling, not financial advice. This skill only reads filings.
Nothing places an order except `/trader-broker`, after `/trader-risk` passes
and the user types an explicit yes for that order.
