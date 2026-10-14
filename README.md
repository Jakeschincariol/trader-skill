# The Trader skill

11 Claude skills that copy the investors you trust, watch the news and Congress, size every trade to your budget, and never place an order without your yes.

Free, MIT, no signup, no API key. The tools are plain Python that read public
data: SEC EDGAR, the House Clerk, news feeds.

One reverse-engineers the investor you want to copy, straight from their 13F
filing. Another monitors breaking news on every stock you hold. One manages your
budget: every dollar sized, a cash buffer kept, no stock over your cap. And the
craziest one is the Pelosi skill: it reads the stock trades members of Congress
disclose, the minute the filing posts.

And no trade goes through until you say yes. Nothing in this pack can place an
order on its own. Paper trading is the default.

## Install

Paste this into Claude:

```
https://github.com/Jakeschincariol/trader-skill

install skill
```

Or as a plugin, in Claude Code:

```
/plugin marketplace add Jakeschincariol/trader-skill
/plugin install trader-skill@trader-skill
```

Claude Code namespaces plugin skills, so installed as a plugin they show up as
`/trader-skill:trader-copycat` and so on. Copy the folders instead if you want
plain `/trader-copycat`:

```bash
git clone https://github.com/Jakeschincariol/trader-skill.git
cp -r trader-skill/trader-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's
`.claude/skills/`. No Claude Code at all? Paste any single `SKILL.md` at the
top of a chat and it runs as a mode. You lose the Python tools, but the method
works.

The tools need Python 3.8 or newer. Nothing to pip install. SEC asks every
script to identify itself: put your name and email in `trader/config.json`
(`"sec_user_agent": "Your Name you@example.com"`) or in the `SEC_USER_AGENT`
environment variable.

## The eleven

| command | job | what it does |
| --- | --- | --- |
| `/trader-copycat` | Reverse Engineer | Reads an investor's 13F-HR on SEC EDGAR and rebuilds their portfolio as weights you can copy, with what they bought, added, trimmed and sold since last quarter. |
| `/trader-news` | News Monitor | Watches headlines and SEC filings (8-Ks) for every stock you hold and flags what touches them. |
| `/trader-budget` | Budget Manager | Sizes every position to your budget, keeps a cash buffer and caps any one stock. |
| `/trader-pelosi` | Pelosi Tracker | Reads Congress's stock-trade disclosures (House PTRs) as soon as they post, turns each PDF into clean rows and alerts you once per new filing. |
| `/trader-analyst` | Earnings Analyst | Pulls each company's reported numbers from SEC XBRL (revenue, net income, diluted EPS, margins, growth) and its latest earnings release. |
| `/trader-screener` | Stock Screener | Screens companies against your own rules and ranks what passes. |
| `/trader-risk` | Risk Manager | Checks every proposed order against your limits and blocks anything over them. |
| `/trader-broker` | Order Executor | Turns an approved order into the exact ticket your brokerage connector receives, and records your yes. It never places anything itself. |
| `/trader-tax` | Tax Optimizer | Tracks cost-basis lots, gains and losses, tax-loss harvesting candidates and wash sales. |
| `/trader-alerts` | Price Alerts | Rings when a price target, a stop or the opening bell hits, with the real NYSE calendar. |
| `/trader-rebalance` | Rebalancer | Brings the portfolio back to its target weights when it drifts past your band. |

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads
what the last one wrote:

```
copycat -> budget -> risk -> broker            (copy an investor)
news / pelosi / alerts                          (watch, on a schedule)
analyst -> screener -> budget -> risk -> broker (pick your own)
rebalance / tax -> risk -> broker               (maintain)
```

An example: copying Berkshire Hathaway with $5,000.

1. **`/trader-copycat`** "copy Berkshire's top 5". It finds Berkshire Hathaway
   (CIK 1067983), downloads its latest 13F-HR (Q2 2026, filed 2026-08-14) and
   reads every row: Apple 22.04 % of the reported portfolio, American Express
   17.14 %, Alphabet 12.62 % (both share classes), Coca-Cola 10.86 %, Bank of
   America 9.20 %. Since last quarter: new D.R. Horton, sold out of
   Constellation Brands. It writes `trader/targets.json`, the top 5
   renormalised: 30.67 / 23.85 / 17.56 / 15.11 / 12.80 %.
2. **`/trader-budget`** with a $5,000 budget, a 10 % cash buffer and a 25 %
   cap: Apple $1,250 (capped), American Express $1,119, Alphabet $823,
   Coca-Cola $708, Bank of America $600, $500 cash, $0 idle. It writes
   `trader/orders.json` as proposals.
3. **`/trader-risk`** checks all five against your limits: blocked tickers,
   order size, position size after the fill, the cash buffer, no options or
   margin. PASS or BLOCK, with the reason.
4. **`/trader-broker`** shows you each ticket: "Buy $1,250 of AAPL, market,
   day". You type yes for that exact order. Claude calls your brokerage
   connector (Alpaca's MCP server, paper account by default), and Claude's own
   "Allow once / Deny" card asks you one more time.
5. On a schedule: **`/trader-news`** flags headlines on what you hold,
   **`/trader-pelosi`** alerts you when a new disclosure posts, and
   **`/trader-alerts`** rings on your price levels.
6. Every quarter: **`/trader-copycat`** diffs the new 13F against the last one,
   and **`/trader-rebalance`** proposes the trades to match it again.

## The tools

All standard-library Python. The data tools read public endpoints and are
polite about it (SEC's rule: identify yourself, at most 10 requests a second).

```bash
python3 trader-copycat/edgar13f.py find buffett                        # a filer's CIK
python3 trader-copycat/edgar13f.py filings 1067983                     # its 13F-HR filings
python3 trader-copycat/edgar13f.py --help                              # holdings, diff, copy
python3 trader-news/news.py --tickers AAPL AXP GOOGL KO BAC --hours 24 # headlines + 8-Ks
python3 trader-pelosi/ptr.py index --member pelosi                     # her PTRs this year
python3 trader-pelosi/ptr.py --help                                    # new, fetch, parse
python3 trader-analyst/facts.py AAPL                                   # quarters, growth, EPS
python3 trader-screener/screen.py trader/metrics.json --rules trader/rules.json
python3 trader-budget/size.py --budget 5000 --write                    # -> trader/orders.json
python3 trader-risk/check.py                                           # PASS / BLOCK per order
python3 trader-broker/ticket.py show                                   # the tickets, READY or not
python3 trader-tax/lots.py harvest --min-loss 100                      # with wash-sale checks
python3 trader-alerts/alerts.py clock                                  # NYSE open or closed
python3 trader-rebalance/rebalance.py --band 5                         # the drift table
```

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline, on small fixtures cut from the real filings: Berkshire's
Q1 and Q2 2026 information tables, the House Clerk's 2026 index, real PTR text,
real XBRL facts and feeds.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation, it cannot see the future, and
copying an investor does not copy their results. You can lose money. Decide for
yourself, and talk to a licensed professional if you need advice.

**No trade goes through until you say yes.** Only `/trader-broker` can lead to
an order, and only after `/trader-risk` passes that exact order and you type yes
for it in the chat. Set your brokerage tool to "Needs approval" in Claude
(Customize, Connectors, tool permissions), never "Always allow", so Claude's
own "Allow once / Deny" card is a second check. No options, margin, short
selling or leverage unless you turn them on.

**Paper first.** `"paper": true` is the default in `config.example.json`, and
Alpaca's MCP server defaults to its paper account. Keys live in environment
variables, never in the chat.

**13F data is old and partial.** Funds file 45 days after each quarter ends, and
a 13F lists long positions in US-listed stocks and a few related securities:
no shorts, no cash, no foreign-listed shares. You see what they held, not what they hold today.

**Congress's trades are public, not inside information.** Members must disclose
trades over $1,000 within 45 days (the STOCK Act). The Pelosi skill reads those
public filings the minute they post, with the amounts as the ranges they are
filed in. It has no access to anything that is not public.

**The data comes from free public sources.** SEC EDGAR, the House Clerk, news
RSS and Yahoo Finance's chart endpoint (unofficial, and it can change). Check
anything that matters against your broker before you act on it.

**Taxes.** `/trader-tax` does the arithmetic on lots and wash-sale windows. It
is not tax advice.

## Files

```
trader-copycat/    SKILL.md, edgar13f.py, filers.json, share_classes.json, targets.example.json
trader-news/       SKILL.md, news.py
trader-budget/     SKILL.md, size.py, config.example.json, targets.example.json
trader-pelosi/     SKILL.md, ptr.py
trader-analyst/    SKILL.md, facts.py, metrics.example.json
trader-screener/   SKILL.md, screen.py, rules.example.json
trader-risk/       SKILL.md, check.py
trader-broker/     SKILL.md, ticket.py, claude_desktop_config.example.json
trader-tax/        SKILL.md, lots.py, portfolio.example.json
trader-alerts/     SKILL.md, alerts.py, alerts.example.json
trader-rebalance/  SKILL.md, rebalance.py
tests/             unittest suite + offline fixtures
```

MIT. Made by [Jake Schincariol](https://opusjake.ai).
