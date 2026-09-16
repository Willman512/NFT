# Forge

A small office of bots that runs on your computer, is handed about $50, and
has one standing instruction: **earn your keep or be shut down.**

You talk to the manager. The manager runs everyone else.

```
  🧭  Del Ramsay     Office Manager   runs the standup, breaks ties, talks to you
  🪙  Ines Okonjo    Treasurer        owns the books, sets budgets, eliminates failures
  📣  Rory Vance     Head of Sales    picks what gets made and what it costs
  🔍  Tam Beckett    Researcher       reads the record, says what it actually supports
  🛠️  Juno Park      Operator         runs the ventures and reports what came out
```

---

## Read this first

Three things are true, and the office is built around them rather than
around wishful thinking.

**$50 does not compound into a living on its own.** What this gives you is a
machine that runs cheap experiments, keeps honest books, and kills what does
not work — without you having to watch it.

**Some steps are illegal or impossible to automate, and Forge does not fake
them.** Amazon KDP has no publishing API and its terms forbid scripted
uploads. So the office produces a finished, print-ready book and a
copy-and-paste listing sheet, then queues a task for *you*. Five minutes of
clicking. Nothing counts as revenue until real money actually lands and you
tell the books so.

**The memecoin trader is very likely to lose money, and it knows it.** It runs
on paper and cannot touch real funds until its own recorded results pass a
gate you did not write. The researcher will say so out loud. If it keeps
losing, the treasurer eliminates it — which is the point of building it this
way rather than arguing about it.

---

## Install

```bash
git clone <this repo> forge && cd forge
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .

forge init        # writes forge.yaml
forge office      # opens http://127.0.0.1:8765/
```

That is the whole setup. No API key, no wallet, no network needed to start.

Optional extras:

```bash
pip install -e ".[claude]"    # the staff talk like people instead of terse reports
pip install -e ".[preview]"   # forge preview, to see colouring pages as PNGs
pip install -e ".[live]"      # only if the trader will ever touch real money
```

---

## The office

`forge office` serves a local page: the floor with five desks, the ventures
and how close each is to paying for itself, the tasks waiting on you, the
live activity feed, and your wallet along the bottom.

Click anyone to talk to them. Every answer is generated from the actual
ledger — if a number appears in a reply, it came out of the books.

Without `ANTHROPIC_API_KEY` set, the staff speak in a terse scripted voice
and everything works. With a key set, the same decisions get narrated
conversationally by Claude. **Only the prose changes.** The office decides
what it decides either way, which is why the language model is never load
bearing.

### What actually happens at a standup

Not theatre — each step has teeth:

1. **Tam** reads the ledger and states what the record supports, including
   when the sample is too small to mean anything.
2. **Rory** picks the next book's theme and price, rotating the catalogue.
   If finished books are piling up unpublished, he stops production — a
   fourth unsold book does not sell the first three.
3. **Ines** applies the survival rule to every venture and sets every
   budget. She can eliminate a venture, and nobody can overrule her.
4. **Del** resolves all of that into the list of ventures that run.
5. **Juno** runs them and reports what came out and what broke.

---

## The survival rule

Every venture starts on **probation** with a grace period. After that it has
to show it has earned more than it cost, plus a little:

```
income >= expense + min_profit_usd
```

Miss it and you get a **strike**. Three strikes and the venture is
**eliminated** — disabled permanently, its capital recalled to the treasury,
and a tombstone written saying why. Clear the bar and it is **tenured**, and
the treasurer will fund it more.

A venture that costs nothing to run cannot fail this test, which is exactly
why the colouring-book factory is built to cost nothing.

You can overrule the office: `forge venture revive memecoin`, or the button
on its card in the UI.

---

## The two ventures

### Colouring books — the one that can pay for itself

Generates complete, print-ready books: procedurally drawn mandalas, mazes
with solutions, stained-glass rose windows, botanical pages and Truchet
ribbons. Vector line art, so it is crisp at any size, tiny on disk, costs
nothing to produce, and raises no question about whose work it is.

Each book comes out as a folder containing:

- `interior.pdf` — single sided, blank backs so markers do not bleed
  through, gutter alternating so nothing vanishes into the spine
- `cover.pdf` — full wrap, spine width computed from the final page count,
  barcode zone left clear
- `metadata.json` — title, subtitle, description, seven keywords, categories
- `UPLOAD.md` — the copy-and-paste sheet for KDP

See a sample before committing to anything: `forge preview`.

### Memecoins — the one on trial

Momentum trading on Solana, via public endpoints. Paper by default. Hard
caps on position size, total exposure, daily loss and hold time; stop loss,
take profit and a trailing stop on every position; a vanished price is
booked as a total loss, because that is what a rug is.

Before it can touch real money **all** of these must be true:

- `mode: live` in your config, and
- `i_understand_the_risk: true`, and
- its paper record passes the graduation gate (60+ closed trades over 14+
  days at positive expectancy), and
- a spendable wallet is configured.

Ask it any time: it will list exactly what is still standing in the way.

The honest position on this venture: a momentum bot reading public data has
no known edge on this market, and fees, spread and rug rate all run against
you. It is in the swarm so the treasurer can judge it on evidence. The live
execution path is written against Jupiter's documented API and has **never
been run against mainnet by its author** — if you enable it, test it with an
amount you would shrug at losing.

---

## The wallet

Your wallet sits along the bottom of the office. **Watch-only is the
default and the recommendation**: paste your public address into
`forge.yaml`, and the office can see the balance while having no ability
whatsoever to move it. Keys stay in Phantom, or wherever you already keep
them.

```yaml
office:
  wallet_address: "your-public-address"
```

If you ever want the trader to place real orders it needs its own hot
wallet, holding only what you can afford to lose:

```bash
forge wallet --create-hot ~/.forge/hot-wallet.json
```

That writes a `solana-keygen`-format key file with mode 600, so you can
import it into a normal wallet and take the money out whenever you like.

---

## Money in

Royalties land in your Amazon account, not here. When one actually arrives,
tell the books — until you do, that book counts as a cost and the treasurer
will treat it accordingly:

```bash
forge earned coloringbook 12.40 --memo "KDP royalty, March"
```

Or use the **Money in** box in the office. This is deliberate. A ledger that
books imaginary revenue is worse than no ledger.

---

## Commands

| Command | What it does |
|---|---|
| `forge office` | the visual office (the main one) |
| `forge standup` | hold one standup in the terminal |
| `forge run` | standups forever, no web UI, for a service manager |
| `forge status` | what the books say |
| `forge ask banker "what has the trader cost us?"` | talk to one of the staff |
| `forge tasks` / `--done ID` | what is waiting on you |
| `forge earned <venture> <amount>` | book money that actually arrived |
| `forge venture revive <name>` | overrule an elimination |
| `forge wallet` | show the wallet, or create a hot one |
| `forge preview` | render sample colouring pages as PNGs |
| `forge selftest` | check the live trading endpoints without spending |

---

## Keeping it open

Forge only earns while it is running. `service/` has a launchd plist, a
systemd user unit and a Windows startup script — edit the paths and the
office comes back after a reboot. See `service/README.md`.

A sleeping laptop is not an office. For genuinely perpetual, use a machine
that stays awake.

---

## How it is put together

```
forge/
  core/        ledger (double entry), survival rule, orchestrator, config
  staff/       the five personas, their brains, the standup
  ventures/    coloringbook (art, KDP layout), memecoin (risk, paper book)
  wallet/      Solana, watch-only by default
  web/         the local office UI
```

The whole business is one SQLite file at `.forge/forge.db` — books, staff
memory, chat history, task queue. Back that up and you have backed up the
company.

The books are real double entry: every transaction is a set of signed
postings that must sum to zero, and `check_invariant()` runs after every
cycle. Transactions carry a `ref`, so a bot that crashes and retries cannot
bill you twice.

```bash
pip install -e ".[dev]" && pytest      # 127 tests, no network required
```

---

## What Forge will not do for you

- Upload to KDP, or any other service whose terms forbid automation.
- Count money that has not arrived.
- Tell you a venture is doing well when it is not.
- Promise that $50 becomes anything at all.
