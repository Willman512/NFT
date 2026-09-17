# Forge

A small office that runs on your computer. Five people work there. You bring
them an idea — any idea — and they read it, argue about it, and split the work
up between themselves.

```
  🧭  Del Ramsay     Office Manager   runs the standup, breaks ties, talks to you
  🪙  Ines Okonjo    Treasurer        what it costs, what we risk, when we stop
  📣  Rory Vance     Head of Sales    who buys it and why they'd choose it
  🔍  Tam Beckett    Researcher       what it assumes, and the cheapest way to check
  🛠️  Juno Park      Operator         whether it can be built, and the first step
```

The office starts empty. It has no products, no opinions about what you should
be doing, and no list of approved business types. It waits for you.

---

## Install

```bash
git clone <this repo> forge && cd forge
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .

forge init        # writes forge.yaml
forge office      # opens http://127.0.0.1:8765/
```

No API key, no wallet, no network needed. Type an idea into the box and watch
what happens — literally: the office is a room you can see, with five people in
it who walk about, sit down to work, gather round the table when they meet, and
come and stand at your desk when they need an answer.

Optional:

```bash
pip install -e ".[claude]"    # the staff read properly instead of pattern-matching
pip install -e ".[preview]"   # forge preview, for the bundled colouring-book example
pip install -e ".[live]"      # only if something will ever touch a real wallet
```

---

## The room

Everything on screen comes from real state. Nobody paces about to look busy:

| What you see | What it means |
|---|---|
| Someone at their desk, typing, green ring | that piece of work is in progress |
| Someone standing at **YOU** with an amber **?** | they are blocked on an answer from you |
| Everyone round the table, "STANDUP" | a standup just ran; it holds for a few seconds so you can read it |
| A speech bubble | the last thing that person actually said, straight from the feed |
| Cards in the **IDEAS IN** tray | ideas on the board, coloured by status |
| The arc on the wall clock | how much of the wait until the next standup has gone |
| Someone idle at their desk | nothing on their desk — the office really is quiet |

Tap anyone to talk to them.

## What happens to an idea

```
   you write it
        │
        ▼
    proposed ──► vetting ──► accepted ──► in progress ──► delivered
                     │                         │
                     ├──► parked               └──► questions back to you
                     └──► declined
```

**Everyone reads it.** Each desk gives a stance (for, against, unsure), a
headline, a few specific observations, and any questions only you can answer.
They are looking at genuinely different things — Ines at the downside, Rory at
the buyer, Tam at the assumption, Juno at the first step.

**Del makes the call.** Four opinions become one of three outcomes. The bias is
deliberately toward trying: the question is never whether an idea is perfect,
it is what the cheapest version worth testing would be.

**Then it gets split up.** An accepted idea becomes assignments owned by named
people, in a sensible order — find out whether anyone wants it before deciding
what to charge, decide what to charge before building it. They work their piece
over the following standups.

**Anything only you can answer comes back to you.** Those questions sit on the
board as blocked work and in your task list. Answer one and the work moves on.

When every piece is done the idea is delivered, and there is a brief at
`out/ideas/NNN-slug/brief.md` — the idea, what each of them said, the decision,
and all the work, in one file you can read or send to someone.

### An example, start to finish

```
$ forge idea "Run a Saturday repair cafe where people bring broken
              small appliances and we fix them for a donation"

Idea 1: Run a Saturday repair cafe where people bring broken small appliances…

  ACCEPTED — Taking it as a small test. The cost of finding out is lower
  than the cost of arguing about it.

  🔍 Tam Beckett    for      Untested, but the test is cheap.
       - The riskiest assumption is that the thing we would make is the
         thing they would want.
       - Cheapest way to check it: describe it to five people and see
         whether they ask for it or change the subject.

  🪙 Ines Okonjo    for      Cheap enough to try inside a $16.66 cap.
       - Nothing here obviously costs money up front, which is the best
         kind of idea to try.
       - I would cap the whole experiment at $16.66 and stop if it has
         not shown anything by then.

  Who has what:
    [!] manager     What would have to be true for this to be obviously worth doing?
    [!] manager     Where would someone come across this?
    [ ] researcher  Test the riskiest assumption
    [ ] salesman    Name the buyer and the offer
    [ ] banker      Set the budget and the stop
    [ ] maker       Write a one-page description of the finished thing
```

---

## Getting to it from anywhere

By default the office listens on localhost and nothing else can reach it.

**On your own network** — your phone on the same wifi:

```bash
forge office --lan
```

It prints a link with a key in it. Open that on the phone once and it stays
signed in for a month; add it to your home screen and it behaves like an app.
Lost the link? `forge office --show-link`.

Two things to be straight about. The key is in the link, so **treat the link
like a password** — anyone who has it can talk to your staff, spend from the
budget and see your wallet. And it is plain HTTP, so it is only as private as
the network you are on.

**From actually anywhere** — a coffee shop, another country — do not port
forward. Put it behind a tunnel, which gives you TLS and an address that works
from everywhere without opening anything on your router:

```bash
# Tailscale: private to your own devices, nothing public
tailscale serve 8765

# Cloudflare Tunnel: a public https URL, still protected by the token
cloudflared tunnel --url http://localhost:8765
```

Either way the token still applies, so the tunnel is not the only thing
standing between your office and the internet.

---

## Open minds, with two brakes

The staff will take on almost anything. Two things they will not do on their
own, and no amount of persuasion changes either:

**They will not spend money the office does not have.** If an idea names a
number bigger than the purse, Ines says so and it is parked until there is a
smaller version of it. This is computed from the ledger, not from anyone's
opinion — including the language model's.

**They will not walk you into something you should look at yourself.** An idea
touching crypto, gambling, medical or legal claims, someone else's copyright,
or scraping a site that may forbid it gets parked with a note, not a lecture.
Tell them you have read the rules and they will start.

Everything else gets a small test. A vague idea is parked with a request for a
few more sentences rather than a refusal.

---

## Talking to them

Click anyone on the floor, or from the terminal:

```bash
forge ask manager "which of these is actually worth my weekend?"
forge ask banker  "what have we committed to so far?"
forge ask researcher "what's the weakest assumption on the board?"
```

Every answer is generated from the actual state of the office. If a number
appears in a reply, it came out of the books.

### The language model is optional, and it is not load bearing

With no `ANTHROPIC_API_KEY`, the office runs on a scripted brain: it reads your
idea for structure it can recognise — prices, platforms, whether it needs
capital, whether it is automatable — and reasons from that. It works offline
and costs nothing.

With a key set, the staff read the idea properly rather than pattern-matching
its words, which is a real difference on ideas that do not use the obvious
vocabulary. What does **not** change is the two brakes above: affordability and
the caution flags are decided from the ledger and from the idea's own words, so
a persuasive model cannot talk the office into either.

---

## Ventures: what an idea becomes

Most of the time an idea produces a plan, and you go and do it. Sometimes it
produces something that should run on its own schedule forever. That is a
*venture*, and a venture has one rule:

> Pay for yourself and clear a little profit, or be eliminated.

Every venture starts on probation with a grace period. After that it must show
`income >= expense + min_profit`. Miss it and it takes a strike; three strikes
and it is eliminated — disabled permanently, its capital recalled, a tombstone
written saying why. You can overrule that (`forge venture revive <name>`), but
the office will not.

Two worked examples ship with it, both **off by default**:

- **coloringbook** — generates complete print-ready KDP books (procedural
  mandalas, mazes with solutions, rose windows, botanicals) plus listing
  metadata. Costs nothing to run. Publishing stays a queued task for you:
  KDP has no API and its terms forbid scripted uploads.
- **memecoin** — momentum trading on Solana with hard caps on size, exposure,
  daily loss and hold time. Paper only, and it refuses to touch real money
  until its own recorded results pass a gate. Its live path has never been run
  against mainnet by its author.

Turn one on in `forge.yaml` if you want it. Otherwise ignore them.

---

## Money

There is a real double-entry ledger underneath, because "is this worth it"
needs an answer rather than a feeling. It starts with whatever you put in
`treasury.seed_usd` and it only counts money that actually arrived:

```bash
forge earned idea:1 40.00 --memo "first three repair cafe donations"
```

Nothing books itself. A ledger that counts imaginary revenue is worse than no
ledger.

Your wallet sits along the bottom of the office. **Watch-only is the default
and the recommendation** — paste a public address into `forge.yaml` and the
office can see the balance while having no ability to move it.

---

## Commands

| Command | What it does |
|---|---|
| `forge office` | the visual office (the main one) |
| `forge office --lan` | …and let your phone reach it |
| `forge office --show-link` | print the link with its key |
| `forge idea "..."` | put an idea to the staff |
| `forge ideas` | what is on the board |
| `forge idea show 3` | one idea: every review, every piece of work |
| `forge answer 7 "..."` | answer a question they put to you |
| `forge standup` | hold one standup in the terminal |
| `forge run` | standups forever, no web UI, for a service manager |
| `forge status` | what the books say |
| `forge ask <who> "..."` | talk to one of them |
| `forge tasks` | everything waiting on you |
| `forge earned <what> <amount>` | book money that actually arrived |
| `forge wallet` | show the wallet, or create a hot one |

---

## Keeping it open

The office only thinks while it is running. `service/` has a launchd plist, a
systemd user unit and a Windows startup script — edit the paths and it comes
back after a reboot. See `service/README.md`.

A sleeping laptop is not an office.

---

## How it is put together

```
forge/
  staff/       ideas, vetting, delegation, the five personas, their brains
  core/        ledger (double entry), survival rule, orchestrator, config
  ventures/    the two bundled examples, both off by default
  wallet/      Solana, watch-only by default
  web/         the office UI — scene.js draws the room, auth.py guards it
```

The room is plain SVG with CSS animation, built in one file with no framework
and no build step, so it loads instantly and works with the network down. It
respects `prefers-reduced-motion`: if you have that set, everybody stands
still and the state is shown without the movement.

The whole business is one SQLite file at `.forge/forge.db` — ideas, reviews,
assignments, books, chat history, task queue. Back that up and you have backed
up the company.

```bash
pip install -e ".[dev]" && pytest      # 189 tests, no network required
```

---

## What it will not do

- Open accounts, post, buy, ship, or sign anything on your behalf.
- Count money that has not arrived.
- Tell you an idea is good when nobody in the room thinks so.
- Do the part that needs a person, or pretend that part does not exist.

What it does is the part a good staff spends most of its time on: turn a
sentence into a plan, name the assumption that matters, write it down, and hand
you a short list of what only you can do next.
