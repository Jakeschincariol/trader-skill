---
name: trader-news-au
description: >-
  The Australian News Monitor. Watches ASX company announcements for the
  stocks and ETFs you hold, using announcement alert emails in your own Gmail
  (company investor-relations lists or an alert service), never by scraping
  the ASX website. Sorts each announcement by type (trading halt, results,
  guidance, ASX price query, capital raising, director trade, substantial
  holder, takeover, impairment, dividend, routine fund update), says why that
  type usually matters, flags price-sensitive ones and the holdings they
  touch, shows only what is new since the last check, and reads the
  important ones in full before summarising. Use when the user says "any ASX
  news on my stocks", "check my announcements", "did anything get released",
  "any director trades", "is BHP in a trading halt", "morning ASX check", or
  "watch my holdings".
argument-hint: "[codes]"
---

# trader-news-au

A holding can change in a minute: a trading halt, a profit downgrade, a
capital raising at a discount, a director selling. On the ASX, companies must
release that news as an announcement first. This skill reads those
announcements from alert emails the user already receives, sorts them, and
tells the user only what is new and what matters.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/news_au.py" query                  # the Gmail search for your holdings
python3 "${CLAUDE_SKILL_DIR}/news_au.py" query --days 7 --codes BHP CSL
python3 "${CLAUDE_SKILL_DIR}/news_au.py" triage items.json --mark
python3 "${CLAUDE_SKILL_DIR}/news_au.py" kind "Change of Director's Interest Notice"
```

## Why Gmail, not the ASX website

The ASX website's terms of use bar robots, scrapers and automated monitoring,
and allow announcements for personal use only. So this skill never fetches
asx.com.au on a schedule or in bulk. It reads alert emails in the user's own
Gmail through the Gmail connector. If the Gmail connector is not connected,
say so and offer to suggest it; do not fall back to scraping.

Ways the user can get alerts by email (they choose; never sign them up):

- each company's own investor-relations email list (on its investor page);
- an alert service that emails ASX announcements for a watchlist (for
  example Market Index alerts; check its plan and terms);
- their broker's alerts, if it offers them.

Put the senders in `trader/config.json` once known, and company names too,
because many alerts use the name, not the code:

```json
{"news_senders": ["alerts@example.com", "investors@bhp.com"],
 "names": {"BHP": "BHP Group", "CSL": "CSL Limited", "VAS": "Vanguard Australian Shares"}}
```

## Steps

1. Codes: `--codes`, else the AUD holdings in `trader/portfolio.json`, else
   `trader/targets.json`. Run `news_au.py query` to get the Gmail search.
2. Search Gmail with that query (the Gmail connector's thread search), and
   open each matching message. For every announcement in it, write one item
   to a scratch `items.json`: `id` (message id plus title), `title`, `code`,
   `date`, `price_sensitive` (true when the alert marks it), `link`,
   `source`. One email can hold several announcements.
3. Run `news_au.py triage items.json --mark`. It drops what was shown before
   (kept 60 days), labels each item, and sorts: importance 3 (`!`), then 2
   (`*`), then 1; holdings first; newest first.
4. Before telling the user what an important item says, read it: the full
   email, or the PDF it links to, opened once because the user asked about
   it (personal use). If you could not read it, say so; never summarise a
   title as if it were the content.
5. Report in plain words: what happened, which holding, why that type usually
   matters, and anything that touches their rules (a stop level, a sell
   plan). Routine items (fund updates, paperwork) go in one line at the end.

## How to read the main types

| type | what to say |
| --- | --- |
| halt | trading is paused until news is released (usually 2 trading days at most); nothing can be bought or sold meanwhile |
| results | compare with the same period last year and with any guidance; trader-analyst-au does the numbers |
| guidance | the company changed its forecast: say the old and new figures |
| regulator | ASX queried an unusual price move or disclosure: quote the company's answer |
| capital | new shares (placement, entitlement offer, SPP): give the price and discount and how many new shares versus existing |
| director trade | Appendix 3Y: who, bought or sold, how many, on market or not; one sale is not a signal by itself |
| substantial holder | someone crossed or moved within 5 %: who and which way |
| deal | takeover or scheme: offer price, premium, conditions, timetable |
| risk | impairment, administration, going concern: read it in full first |

## What it never does

It never scrapes or polls the ASX website, never signs the user up for
anything, never sends an email, never places an order, and never treats a
headline as a fact without reading the announcement.

## Safety

- Education and tooling, not financial advice. An announcement is information, not a reason to trade.
- Nothing in this skill places an order: any trade goes through trader-budget or trader-rebalance, then trader-risk, then the user's own yes and their own entry in CMC.
- It reads only the user's own email, only for this purpose, and stores only titles, dates and message ids in `trader/state/news-au.json`.
