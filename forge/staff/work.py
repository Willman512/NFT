"""Breaking an accepted idea up, and doing the pieces.

Be clear about what this office can and cannot do, because the difference
is the whole design. It cannot open an Etsy shop, phone a supplier or post
on your behalf. What it can do is the part a good staff spends most of its
time on: turn a sentence into a plan, name the assumption that matters,
write the thing down, and hand you a short list of the steps that genuinely
need a person.

So an assignment produces written work. Anything that needs the world
outside becomes a question addressed to you, and the assignment waits.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from ..core.money import cents_to_str
from .ideas import (
    BLOCKED,
    DOING,
    DONE,
    KIND_ASK,
    KIND_AUDIENCE,
    KIND_BUDGET,
    KIND_BUILD,
    KIND_RESEARCH,
    Assignment,
    Idea,
)
from .vetting import Signals, Verdict, _first_step, _largest_price_cents


def _sentence(text: str, limit: int = 90) -> str:
    """Capitalise the first letter only — ``str.capitalize`` would lowercase
    the rest, which turns Etsy into etsy."""
    trimmed = text[:limit].strip()
    return trimmed[:1].upper() + trimmed[1:]


@dataclass
class Plan:
    """One row of the delegation: who does what, and in what order."""

    owner: str
    kind: str
    title: str
    detail: str
    seq: int


def plan_assignments(idea: Idea, verdicts: dict[str, Verdict], sig: Signals) -> list[Plan]:
    """Split an accepted idea across the desks.

    The order is the order a sensible person would work in: find out whether
    anyone wants it before deciding what to charge, and decide what to charge
    before building it.
    """
    plans: list[Plan] = []

    plans.append(Plan(
        owner="researcher", kind=KIND_RESEARCH, seq=10,
        title="Test the riskiest assumption",
        detail=_detail_of(verdicts.get("researcher")),
    ))

    plans.append(Plan(
        owner="salesman", kind=KIND_AUDIENCE, seq=20,
        title="Name the buyer and the offer",
        detail=_detail_of(verdicts.get("salesman")),
    ))

    plans.append(Plan(
        owner="banker", kind=KIND_BUDGET, seq=30,
        title="Set the budget and the stop",
        detail=_detail_of(verdicts.get("banker")),
    ))

    plans.append(Plan(
        owner="maker", kind=KIND_BUILD, seq=40,
        title=_sentence(_first_step(sig)),
        detail=_detail_of(verdicts.get("maker")),
    ))

    # Anything only the user can answer becomes its own piece of work, so it
    # shows up on the board rather than being buried in a review.
    for i, question in enumerate(_questions_for_user(verdicts)):
        plans.append(Plan(
            owner="manager", kind=KIND_ASK, seq=5 + i,
            title=question[:90],
            detail="Only you can answer this one. The work behind it waits until you do.",
        ))

    return plans


def _detail_of(verdict: Verdict | None) -> str:
    if verdict is None:
        return ""
    lines = [verdict.headline, *verdict.points]
    return "\n".join(f"- {line}" for line in lines if line)


def _questions_for_user(verdicts: dict[str, Verdict], limit: int = 3) -> list[str]:
    """The few questions worth interrupting someone for."""
    seen: list[str] = []
    # Round-robin so one talkative desk does not fill the whole list.
    pools = [list(v.questions) for v in verdicts.values()]
    while pools and len(seen) < limit:
        for pool in pools:
            if pool and len(seen) < limit:
                question = pool.pop(0)
                if question not in seen:
                    seen.append(question)
        pools = [p for p in pools if p]
    return seen


# ----------------------------------------------------------------------
@dataclass
class WorkResult:
    """What came of one person sitting down with one assignment."""

    note: str
    status: str
    blocked_on: str = ""
    question: str = ""
    follow_ups: list[Plan] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"note": self.note, "status": self.status, "blocked_on": self.blocked_on}


def do_work(assignment: Assignment, idea: Idea, sig: Signals, purse_cents: int) -> WorkResult:
    """One pass at an assignment.

    First pass produces the substance and moves it to ``doing``; the second
    reviews it and closes it. Two passes rather than one so the board is
    legible while it happens, rather than everything appearing finished at
    the first standup.
    """
    if assignment.status == DONE:
        return WorkResult("Already finished.", DONE)

    first_pass = assignment.status != DOING
    if not first_pass:
        return _close(assignment, idea, sig)

    if assignment.kind == KIND_ASK:
        return WorkResult(
            note=f"Put to you and waiting: {assignment.title}",
            status=BLOCKED,
            blocked_on="you",
            question=assignment.title,
        )

    body = _substance(assignment.kind, idea, sig, purse_cents)
    return WorkResult(note=body, status=DOING)


def _close(assignment: Assignment, idea: Idea, sig: Signals) -> WorkResult:
    closing = {
        KIND_RESEARCH: "Test written up. Until someone actually runs it we are still guessing, "
                       "but at least we now know what we are guessing about.",
        KIND_AUDIENCE: "Buyer and offer are written down. If they turn out to be wrong, they "
                       "are at least wrong in a specific way we can correct.",
        KIND_BUDGET: "Budget and stop are set. I will hold us to the stop.",
        KIND_BUILD: "First step is specced out and in the brief. Say the word and I will take it.",
    }
    return WorkResult(note=closing.get(assignment.kind, "Done."), status=DONE)


def _substance(kind: str, idea: Idea, sig: Signals, purse_cents: int) -> str:
    if kind == KIND_RESEARCH:
        return _research(idea, sig)
    if kind == KIND_AUDIENCE:
        return _audience(idea, sig)
    if kind == KIND_BUDGET:
        return _budget(idea, sig, purse_cents)
    if kind == KIND_BUILD:
        return _build(idea, sig)
    return "Nothing to add."


def _research(idea: Idea, sig: Signals) -> str:
    if not sig.names_audience:
        assumption = "that anybody wants this at all"
        steps = [
            "Search for people complaining about the problem in public, this week.",
            "Ten real complaints is a yes. Two is a no. Nothing is a loud no.",
            "Write down where you found them — that is also the marketing channel.",
        ]
    elif sig.prices:
        assumption = f"that they will pay {sig.prices[0]} rather than just say it is a nice idea"
        steps = [
            f"Put the offer in front of ten of them at {sig.prices[0]}, before building it.",
            "Count the ones who try to pay, not the ones who say they would.",
            "One payer in ten is enough to continue. Zero is the answer.",
        ]
    elif sig.platforms:
        assumption = f"that we can get any distribution on {sig.platforms[0]}"
        steps = [
            f"Look at the top twenty results on {sig.platforms[0]} for this.",
            "Note how old they are and how many reviews they have.",
            "If the top of the page is stale, that is the opening.",
        ]
    else:
        assumption = "that the thing we would build is the thing they would want"
        steps = [
            "Describe the finished thing to five people in one sentence.",
            "Count how many ask a follow-up question unprompted.",
            "Questions are interest. Politeness is not.",
        ]
    lines = [f"Riskiest assumption: {assumption}.", "How I would check it:"]
    lines += [f"  {i}. {s}" for i, s in enumerate(steps, 1)]
    return "\n".join(lines)


def _audience(idea: Idea, sig: Signals) -> str:
    who = ("Someone already described in the idea" if sig.names_audience
           else "Nobody named yet — this is the gap")
    where = (f"{sig.platforms[0]}, where they are already looking" if sig.platforms
             else "Unknown. Until we know, there is no way to reach anyone")
    price = sig.prices[0] if sig.prices else "unpriced"
    lines = [
        f"Buyer: {who}.",
        f"Where we reach them: {where}.",
        f"Price: {price}.",
        "The offer, in one line: " + _offer_line(idea, sig),
        "What they do instead today is the real competitor, not another product.",
    ]
    return "\n".join(lines)


def _offer_line(idea: Idea, sig: Signals) -> str:
    subject = idea.title.rstrip(".")
    price = f" for {sig.prices[0]}" if sig.prices else ""
    return f"“{subject}”{price} — needs rewriting by whoever knows the buyer best."


def _budget(idea: Idea, sig: Signals, purse_cents: int) -> str:
    ceiling = min(max(purse_cents // 3, 0), 2000)
    asking = _largest_price_cents(sig)
    lines = [
        f"Cap on this idea: {cents_to_str(ceiling)}. That is a third of what is "
        f"uncommitted, which is as much as one untested thing should ever get.",
        f"Stop rule: if it has not produced a single sale or a clear signal by the cap, "
        f"we stop and I write it off.",
    ]
    if asking:
        lines.append(f"The idea names {cents_to_str(asking)}. "
                     + ("That fits." if asking <= ceiling else "That does not fit — it needs a smaller version."))
    else:
        lines.append("No cost named in the idea, so the cap is the only number we have. "
                     "Tell me what one unit costs and nets and I will replace it with a real one.")
    if sig.recurring:
        lines.append("Recurring revenue also means a recurring bill. Whatever we host, "
                     "I want the monthly number before we sign up to it.")
    return "\n".join(lines)


def _build(idea: Idea, sig: Signals) -> str:
    lines = [f"First step: {_first_step(sig)}."]
    if sig.automatable:
        lines.append("Most of this looks repeatable, so I would build the smallest script "
                     "that makes one real example rather than designing the whole pipeline.")
    if sig.skills:
        lines.append("It needs " + ", ".join(sig.skills) + ". If that is not in the office, "
                     "it is a cost, not a step.")
    if sig.platforms:
        lines.append(f"I can take it up to the point of uploading to {sig.platforms[0]}. "
                     f"The account is yours, so the last click is yours.")
    if sig.physical:
        lines.append("There is a physical step here that I cannot do at all. "
                     "Worth deciding early who does.")
    lines.append("I have written this into the brief so it does not live only in this note.")
    return "\n".join(lines)


# ----------------------------------------------------------------------
def write_brief(out_dir: Path, idea: Idea, reviews: list, assignments: list) -> Path:
    """Regenerate the idea's brief. One file, always current.

    This is the thing you can actually read, forward, or act on — the rest
    of the office is a way of keeping it honest.
    """
    folder = out_dir / "ideas" / f"{idea.id:03d}-{idea.slug}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "brief.md"

    parts = [
        f"# {idea.title}",
        "",
        f"*{idea.status.replace('_', ' ')}* · raised by {idea.source}",
        "",
        "## The idea, as you put it",
        "",
        textwrap.indent(idea.body.strip(), "> "),
        "",
    ]

    if idea.decision:
        parts += ["## What the office decided", "", idea.decision, ""]

    if reviews:
        parts += ["## What each of them said", ""]
        for review in reviews:
            parts.append(f"### {review.persona} — {review.stance}")
            parts.append("")
            parts.append(f"**{review.headline}**")
            parts.append("")
            for point in review.points:
                parts.append(f"- {point}")
            if review.questions:
                parts.append("")
                parts.append("Open questions:")
                for question in review.questions:
                    parts.append(f"- {question}")
            parts.append("")

    if assignments:
        parts += ["## The work", ""]
        for a in assignments:
            mark = {"done": "x", "doing": "~", "blocked": "!", "todo": " "}.get(a.status, " ")
            parts.append(f"- [{mark}] **{a.owner}** — {a.title}"
                         + (f" _(waiting on {a.blocked_on})_" if a.blocked_on else ""))
            for note in a.notes:
                parts.append(textwrap.indent(note, "      "))
            parts.append("")

    parts += [
        "---",
        "",
        "Written by the office. Nothing in here has been tested against the world; "
        "it is a plan, not a result.",
        "",
    ]
    path.write_text("\n".join(parts))
    return path
