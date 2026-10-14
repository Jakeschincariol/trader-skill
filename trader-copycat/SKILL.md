---
name: trader-copycat
description: >-
  The Reverse Engineer. Reads a famous investor's public SEC filings (the
  quarterly 13F-HR, plus Form 4 if you want the insider trades) and rebuilds
  their portfolio as weights you can copy: every position, its share of the
  fund, what they bought, added, trimmed and sold since the last quarter, with
  tickers matched to every CUSIP. Then it writes trader/targets.json so the
  budget skill can size it to your money. Use when the user says "copy
  Buffett", "copy Berkshire", "what does Ackman own", "reverse engineer this
  investor", "show me their 13F", "what did they buy last quarter", "copy the
  top 5", or names any fund or famous investor.
argument-hint: "[investor, fund or CIK] [--top N]"
---

# trader-copycat

Copying an investor starts with what they are legally required to show: a
13F-HR, filed with the SEC every quarter by any investment manager with over
$100 million in US-listed stocks and other 13(f) securities. This skill reads
it straight from SEC EDGAR, rebuilds the portfolio, and turns it into target
weights for your own account.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" --help
```

SEC asks every program to identify itself with a name and email. Set it once
in `trader/config.json` (`"sec_user_agent": "Your Name you@example.com"`, with
the user's real name and email) or `export SEC_USER_AGENT=...`. The tool
refuses to call SEC without it and says how to fix it. Ask the user for it;
never invent one.

## What it writes

- `trader/targets.json`: the weights to copy, in the shape every other trader
  skill reads: `{ "source", "as_of", "weights": [ {"ticker", "name",
  "weight"} ] }`, weights in % with 2 decimals summing to 100 over the stock
  sleeve (keep the 2 decimals: budget sizing needs them), plus the
  13F share, value, CUSIPs, what was skipped and why, and the caveats.
- `trader/tickers.json` (optional, yours): CUSIP to ticker fixes the tool
  will always use first, as `{"650111107": "NYT"}`.

`targets.example.json` in this folder is the real output of the reel's run
(Berkshire, top 5).

## The rules

- **Say how old the data is, every time.** A 13F is a snapshot of the last
  day of the quarter, filed up to 45 days later. Say "holdings as of
  2026-06-30, filed 2026-08-14" in every answer that uses one.
- **Say what it cannot show.** Long US-listed positions only: no shorts, no
  cash, no bonds, no foreign shares, no private companies, nothing bought and
  sold inside the quarter, and no prices paid.
- **The firm, not the person.** Berkshire Hathaway files the 13F, not Warren
  Buffett. Greg Abel has been CEO since 2026-01-01 and Buffett became
  Chairman Emeritus on 2026-09-18. Never say "Buffett bought" when the filing
  only says "Berkshire held".
- **Every number comes from the filing.** Quote the accession number and link
  the information table. If a ticker is a guess (marked `*`), say so and fix
  it with `--figi` or `trader/tickers.json` before anything is copied.
- **Options and bonds are never copied.** PUT, CALL and PRN rows are listed as
  excluded. This pack trades stocks only unless the config allows more.

## Step 1: find the investor

```bash
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" find buffett
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" find "pershing square" --sec
```

`filers.json` lists 23 well-known filers with CIKs checked against SEC on
2026-10-13 (Berkshire, Pershing Square, Scion, Bridgewater, ARK, Duquesne,
Appaloosa, Baupost and more). `--sec` searches every EDGAR filer by name. If
a filer has stopped filing (Scion's last 13F-HR was 2025-11-03), say so.

## Step 2: read the latest quarter

```bash
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" filings 1067983
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" holdings 1067983 --combine --top 15
```

It downloads the submissions list, picks the newest original 13F-HR, finds the
information table XML in the filing folder, sums the rows each holding is
split into (Berkshire reports one stock across several subsidiaries), checks
the total against the cover page, and matches tickers: your
`trader/tickers.json` first, then `share_classes.json` (verified CUSIPs for
companies with several listed classes), then SEC's `company_tickers.json` by
name, and OpenFIGI's free CUSIP lookup with `--figi`. `--combine` adds share
classes of one company together (Alphabet A + C). If an amendment exists for
the quarter, the tool says so; read it with `--accession`.

## Step 3: what changed

```bash
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" diff 1067983
```

New positions, positions sold out, added and trimmed, by share count against
the quarter before. Share counts at two dates, not trades: say "held more
shares at June 30 than at March 31", never "bought on" a day.

## Step 4: write the targets

```bash
python3 "${CLAUDE_SKILL_DIR}/edgar13f.py" copy 1067983 --top 5 --out trader/targets.json
```

Share classes are combined by default (`--separate` keeps them apart).
`--exclude TICKER` leaves out what the user will not own, `--min-pct 1` drops
crumbs. The weights are each position's % of the 13F scaled so the chosen
ones sum to 100. Show the table, then hand off to `/trader-budget`.

Form 4 (optional): insiders and 10% owners report their trades within two
business days on Form 4. To see them, open
`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<CIK>&type=4&owner=include`
(or the filer's submissions list) and read the filings the user asks about.
They are faster than a 13F but cover one stock at a time.

## Example: the reel

"Copy Berkshire Hathaway, top 5." The latest 13F-HR is for the quarter ending
2026-06-30, filed 2026-08-14, accession 0001193125-26-352200, total
$299,253,556,246 (matches the cover page).

| # | holding | % of the 13F | copy weight |
| --- | --- | --- | --- |
| 1 | Apple (AAPL) | 22.04 | 30.67 |
| 2 | American Express (AXP) | 17.14 | 23.85 |
| 3 | Alphabet (GOOGL + GOOG combined) | 12.62 | 17.56 |
| 4 | Coca-Cola (KO) | 10.86 | 15.11 |
| 5 | Bank of America (BAC) | 9.20 | 12.80 |

Rounded, that is 30.7 / 23.9 / 17.6 / 15.1 / 12.8. Since the quarter before:
new D.R. Horton (3,564 shares), sold out of Constellation Brands, added
Alphabet, Delta, Lennar, Macy's and the New York Times, trimmed Bank of
America, Kroger, Capital One, Nucor, Ally and DaVita. Then: "These are
Berkshire's holdings as of June 30, 2026. Next, /trader-budget sizes them to
your $5,000."

## What it never does

- Never claims the investor is still holding today, or why they bought.
- Never claims returns, or that copying a fund will make money. A 13F copy
  buys months after the fund did, at today's price.
- Never places an order. It writes target weights; sizing, risk checks and
  your yes come after.
- Never scrapes past SEC's fair-access limits (10 requests a second; the tool
  stays well under).

## Safety

- Education and tooling, not financial advice. Nothing in this pack is a
  recommendation to buy or sell anything.
- Paper trading by default (`"paper": true` in `trader/config.json`).
- Nothing places an order except `/trader-broker`, and only after
  `/trader-risk` passes AND the user types an explicit yes for that order in
  the chat, AND the brokerage tool is set to "Needs approval" in Claude
  (Customize > Connectors > tool permissions) so Claude's own "Allow once /
  Deny" card appears too. Never "Always allow" an order tool.
- No options, margin, shorting or leverage unless the config allows it and
  the user confirms.
- 13F data is quarterly and up to 45 days old when it is filed. Say so.
