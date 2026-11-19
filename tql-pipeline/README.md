# TQL Prospecting Pipeline

This pipeline keeps filtering out bad leads and gets better at picking good ones. You feed it company websites. It researches each site, scores the company as a freight prospect (GO / MAYBE / SKIP), and filters out anything you can't or shouldn't contact. It then learns from what actually happens: replies, quotes, loads booked, bad fits.

- **No installs.** Python 3.9+ standard library only. Runs on Windows or Mac.
- **No caps of its own.** The only limits are in `config/policy.json`, which is where TQL policy goes.
- **Never sends email.** It drafts and you send from your TQL account, so the email is logged in TQL's systems.

## Quick start

```bash
cd tql-pipeline
python prospect.py add https://acme-steel.com https://frostfarms.com   # or: add leads.csv
python prospect.py cycle                 # research → rescore → filter → show queue
python prospect.py card acme-steel.com   # full research card (verdict, snapshot, contact, sources)
python prospect.py email acme-steel.com --phone "(513) 555-0000"
python prospect.py outcome acme-steel.com contacted
```

A leads CSV needs a `url` (or `website` / `domain`) column. The `name`, `source` and `notes` columns are optional. Google redirect links are unwrapped, and duplicates are dropped by domain.

## The daily loop

```
add leads ─► research ─► score ─► filter ─► queue ─► you call/email ─► log outcome ─┐
   ▲                        ▲                                                      │
   │                        └──────────── learn (new weights + new terms) ◄────────┘
   └──── suggest (lookalike searches from what's winning) ◄────────────────────────┘
```

1. `python prospect.py cycle --learn` each morning. Add `--retry-errors` to retry sites that were down.
2. Work the `queue`, starting with GO. Read the ⚑ flags first.
3. Log every result: `python prospect.py outcome <domain> <outcome>`

| Outcome | Effect |
|---|---|
| `contacted` | Starts the re-contact cooldown (`recontact_cooldown_days`) |
| `replied`, `quote_requested`, `meeting`, `load_booked` | Training **win**; moves to your active book |
| `no_response`, `not_interested`, `bad_fit` | Training **loss**. `not_interested` waits `not_interested_cooldown_days`; `bad_fit` is removed permanently |
| `existing_customer`, `owned_by_other_rep` | Removed permanently, but counted as a light **win** (the profile fits TQL) |
| `do_not_contact` | Removed permanently and added to `config/suppression.csv` |
| `manager_declined`, `out_of_business` | Removed, not learned from |

## How it improves

- **Weights:** `learn` retrains the fit model on your outcomes. A load booked counts 3×, a meeting or quote 1.5×, a no-response 0.5×. Training is pulled toward the starting weights so a handful of results can't swing it wildly. It needs 6+ outcomes, including at least 2 wins and 2 losses.
- **Thresholds:** after 20+ outcomes, the GO and SKIP cut-offs recalibrate. The GO bar is set so about 80% of real winners still land in GO, and the SKIP bar rises only as far as the data supports.
- **New signals:** `learn` lists words that show up on winners' sites and rarely on losers'. `learn --adopt-terms 3` turns the top 3 into scoring signals. `terms --disable <word>` removes one that turns out to be noise.
- **Rescoring:** every cycle rescores every stored prospect with the newest model. No re-crawl is needed, so old leads move up or down as the model learns.
- **Scoreboard:** `stats` shows each verdict's win rate, using the verdict each prospect had *when you logged the outcome*. If GO doesn't clearly beat MAYBE, the filter isn't working yet.
- **New leads:** `suggest` writes Google search strings from your best signals and the states your wins came from.

Every model version is saved in `data/prospects.db` (`models` table), so you can always see what changed.

## Policy guardrails (`config/policy.json`)

Items marked **CONFIRM** are placeholders. Check them against TQL's written policy or with your manager. Nothing in this repo is official TQL policy.

- **CRM ownership check:** every GO/MAYBE card reminds you to check TQL's CRM for existing customers and other reps' accounts before you reach out.
- **Brokers, 3PLs and forwarders:** capped at MAYBE, with a reminder to get a manager OK first. Margins will be thin.
- **Overseas-only and stale sites:** capped at MAYBE. For stale sites, call the main line first.
- **Never crawled:** LinkedIn, Facebook, ZoomInfo and the other `blocked_domains` (terms of service). Research those by hand.
- **Polite crawling:** follows robots.txt and waits between requests. Pulls at most `max_pages_per_site` pages per site: home, about, products, contact, shipping, careers.
- **Opt-outs:** anyone who opts out goes on the suppression list permanently (CAN-SPAM / CASL).
- **Daily contact cap:** `max_new_contacts_per_day` is `null` (no cap) unless TQL policy sets one.

## Tuning

`config/icp.json` holds every signal: its regex patterns, starting weight, and plain-English label. Add a signal by adding an entry; `learn` will start weighting it. Edit `config/suppression.csv` to block domains by hand.

## Running on a schedule

- Mac/Linux cron: `0 7 * * 1-5 cd /path/tql-pipeline && python3 prospect.py cycle --learn >> outputs/cycle.log 2>&1`
- Windows Task Scheduler: run `python prospect.py cycle --learn` in this folder each weekday morning.

`python prospect.py export --format md` writes the whole queue as research cards to `outputs/`. Use `--format csv` for a spreadsheet.

## Tests

```bash
python -m unittest discover -s tests
```

The tests run the whole loop against fake company sites (no network): research, verdicts, filters, cooldowns, learning, and email rules.
