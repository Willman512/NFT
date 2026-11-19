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
python prospect.py discover --states OH,KY --profile flatbed      # find new shippers from EPA plant data
python prospect.py plan                  # prospects needed for ~95% odds of a customer + daily quota
python prospect.py today --drafts        # the day's plan: follow-ups due, new GOs with call openers + emails
python prospect.py email acme-steel.com --phone "(513) 555-0000"   # next email in the sequence
python prospect.py outcome acme-steel.com contacted
```

Set your phone once so every draft is signed: `export TQL_PHONE="(513) 555-0000"` (Windows: `set TQL_PHONE=...`).

Starter leads are in `leads/`. Load them with `python prospect.py add leads/seed-2026-11-19.csv leads/seed-2026-11-19-batch2.csv`, then run `cycle`.

A leads CSV needs a `url` (or `website` / `domain`) column. The `name`, `source` and `notes` columns are optional. Google redirect links are unwrapped, and duplicates are dropped by domain.

## Finding leads automatically (`discover`)

```bash
python prospect.py discover --states GA,AL,FL --profile flatbed,reefer
python prospect.py discover --states OH --naics 332312,327390     # exact industry codes
python prospect.py discover --unmatched                           # companies found without a website -> CSV call list
```

`discover` pulls every **active plant** in your states and industries from the EPA's public ECHO database. Factories, sawmills, precast and food plants are almost all registered there. It then:
1. Groups the plants by company, so a company with 5 plants ranks above one with 1.
2. Skips companies already in the pipeline.
3. Finds each company's website by trying likely domains, and accepts a site only if it shows the company's name **and** one of its plant cities or its state.
4. Sends the verified ones through research and scoring.

The government industry code becomes its own scoring signal, which is more reliable than website wording. It's resumable and never re-checks a company it has already processed, so run it weekly. Profiles: `flatbed` (steel, precast, lumber, trusses, machinery, trailers), `reefer` (frozen, meat, poultry, dairy, produce), `dryvan` (snacks, bakeries, beverages, packaging, plastics). See `pipeline/discover.py` to add codes.

## The customer plan (`plan`)

No list can make a shipper say yes. What the pipeline *can* do is make the odds close to certain if you follow the plan:

- The chance a fully worked prospect (a call plus 3 emails) becomes a customer starts at a cautious **2% benchmark** (an assumption, set in `policy.json`). Each result you log updates that estimate with your real numbers.
- From that rate it works out how many prospects give a **95%** chance of at least one new customer: 149 at 2%, because 1 − 0.98¹⁴⁹ ≈ 95%.
- It spreads that number over 20 working days. `today` sizes your list to that daily quota and shows how you're tracking.
- If the queue is too short to hit the plan, it tells you to run `discover`.

## The daily loop

```
add leads ─► research ─► score ─► filter ─► queue ─► you call/email ─► log outcome ─┐
   ▲                        ▲                                                      │
   │                        └──────────── learn (new weights + new terms) ◄────────┘
   └──── suggest (lookalike searches from what's winning) ◄────────────────────────┘
```

1. `python prospect.py cycle --learn --retry-errors` each morning.
2. `python prospect.py today --drafts`: follow-ups due first, then new GOs. **Call first** using the opener, then send the email.
   Read the ⚑ flags and do the CRM ownership check before every first contact.
3. Log every result: `python prospect.py outcome <domain> <outcome>`. Each email or call is `contacted`.
4. Follow-ups are automatic. Touch 2 comes up 3 days after touch 1 and touch 3 comes up 7 days after touch 2 (`followup_days`), and `email <domain>` drafts the right one.
   After touch 3, `today` reminds you to log `no_response`.

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

## Verdict rules

- **GO**: the fit score clears the GO bar and there are no caps.
- **MAYBE**: the score is in the middle, or a cap applies: broker/3PL, overseas-only, stale site, unreadable (JavaScript-only) site.
- **SKIP**: only for companies with **no** freight evidence at all, or a service business. A real shipper with a thin website stays MAYBE, because missing a customer costs more than a minute of checking.

Other commands: `research --all` re-crawls every uncontacted lead (after you tune `icp.json`), and `remove <domain>` deletes a lead entered by mistake.

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
