"""The office itself.

You bring an idea. Everyone reads it through their own lens, the manager
turns four opinions into one call, and if it is worth trying the work gets
split across the desks and picked up over the following standups.

The personalities are not decoration. The banker's affordability call and
the caution flags are hard rules computed from the ledger and the words of
the idea, and no amount of eloquence from a language model can overturn
them. Everything softer — what the risk actually is, who would buy it, what
to build first — is genuinely open, and that is where a real mind helps.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import Config
from ..core.humanqueue import HumanQueue
from ..core.ledger import Ledger
from ..core.log import get_logger
from ..core.money import cents_to_str, usd
from ..core.orchestrator import Orchestrator
from ..core.survival import SurvivalOfficer, SurvivalRules, Verdict as SurvivalVerdict
from ..core.venture import HumanTask
from ..wallet import Wallet
from . import roles
from .brain import Brain, pick_brain
from .ideas import (
    ACCEPTED,
    BLOCKED,
    DELIVERED,
    DOING,
    DONE,
    IN_PROGRESS,
    KIND_ASK,
    LIVE_STATUSES,
    OPEN_STATUSES,
    PROPOSED,
    VETTING,
    Idea,
    IdeaBook,
)
from .memory import KIND_ALERT, KIND_DECISION, KIND_NOTE, KIND_RESULT, KIND_RULING, Memory
from .vetting import Verdict, decide, read_idea, review_idea
from .work import do_work, plan_assignments, write_brief

log = get_logger("office")

#: Desks that read every idea. The manager synthesises rather than reviews.
REVIEWERS = ["researcher", "salesman", "banker", "maker"]


@dataclass
class StandupReport:
    cycle: int
    vetted: list[dict] = field(default_factory=list)
    worked: list[dict] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)
    verdicts: dict[str, str] = field(default_factory=dict)
    remarks: list[dict] = field(default_factory=list)
    halted: bool = False

    def to_dict(self) -> dict:
        return {
            "cycle": self.cycle,
            "vetted": self.vetted,
            "worked": self.worked,
            "ran": self.ran,
            "blocked": self.blocked,
            "verdicts": self.verdicts,
            "remarks": self.remarks,
            "halted": self.halted,
        }


class Office:
    def __init__(
        self,
        cfg: Config,
        *,
        ledger: Ledger | None = None,
        brain: Brain | None = None,
        orchestrator: Orchestrator | None = None,
    ):
        self.cfg = cfg
        self.ledger = ledger or Ledger(cfg.ledger_path)
        self.options = cfg.office.as_options()
        self.survival = SurvivalOfficer(self.ledger, _survival_rules(self.options))
        self.orch = orchestrator or Orchestrator(cfg, ledger=self.ledger, survival=self.survival)
        # Share one connection so the whole business is a single file.
        self.ledger = self.orch.ledger
        self.memory = Memory(self.ledger)
        self.queue = HumanQueue(self.ledger)
        self.ideas = IdeaBook(self.ledger)
        self.brain = brain or pick_brain(self.options)
        self.wallet = Wallet(
            address=cfg.office.wallet_address,
            keyfile=cfg.office.wallet_keyfile,
            rpc_url=cfg.office.rpc_url or "https://api.mainnet-beta.solana.com",
            allow_spend=False,  # the office view never needs spend rights
        )

    # ------------------------------------------------------------------
    # bringing an idea in
    # ------------------------------------------------------------------
    def propose(self, body: str, *, title: str = "", source: str = "you") -> Idea:
        """Put something in front of the staff. Anything at all."""
        idea = self.ideas.propose(body, title=title, source=source)
        self._say(
            "manager",
            {"kind": "finding", "summary":
                f"“{idea.title}” is in. I'll put it to the others and come back "
                f"with what we think and who is doing what."},
            self.memory.cycle,
            kind=KIND_NOTE,
        )
        log.info("idea %d proposed: %s", idea.id, idea.title)
        return idea

    def vet(self, idea: Idea, cycle: int = 0) -> dict:
        """Run one idea past every desk, then have the manager call it."""
        self.ideas.set_status(idea.id, VETTING)
        signals = read_idea(idea.text)
        purse = self.ledger.cash
        context = self.context(include_wallet=False)

        verdicts: dict[str, Verdict] = {}
        for key in REVIEWERS:
            scripted = review_idea(key, idea.text, purse)
            shaped = self.brain.review(
                roles.get(key), idea.text, scripted.to_dict(), context
            )
            verdict = Verdict(
                stance=shaped.get("stance", scripted.stance),
                headline=shaped.get("headline", scripted.headline),
                points=shaped.get("points", scripted.points),
                questions=shaped.get("questions", scripted.questions),
            )
            # Two things a persuasive reviewer is not allowed to change: what
            # we can afford, and whether a person should look first.
            if key == "banker" and scripted.stance == "against":
                verdict = scripted
            verdicts[key] = verdict

            self.ideas.add_review(
                idea.id, key, verdict.stance, verdict.headline,
                verdict.points, verdict.questions,
            )
            self._say(
                key,
                {"kind": "review", "idea": idea.title, "stance": verdict.stance,
                 "headline": verdict.headline, "points": verdict.points},
                cycle,
            )

        call = decide(verdicts, signals, purse)
        self.ideas.set_status(idea.id, call.status, call.reason)
        self._say(
            "manager",
            {"kind": "call", "idea": idea.title, "status": call.status, "reason": call.reason},
            cycle,
            kind=KIND_DECISION,
        )

        if call.status == ACCEPTED:
            self._hand_out(idea, verdicts, signals, cycle)

        self._refresh_brief(idea)
        return {"idea_id": idea.id, "status": call.status, "reason": call.reason}

    def _hand_out(self, idea: Idea, verdicts: dict[str, Verdict], signals, cycle: int) -> None:
        """Split an accepted idea across the desks."""
        plans = plan_assignments(idea, verdicts, signals)
        split: dict[str, int] = {}
        for plan in plans:
            assignment = self.ideas.assign(
                idea.id, plan.owner, plan.title,
                kind=plan.kind, detail=plan.detail, seq=plan.seq,
            )
            split[plan.owner] = split.get(plan.owner, 0) + 1
            if plan.kind == KIND_ASK:
                # A manager asks all their questions in one go rather than
                # remembering a new one every morning.
                self.ideas.update_assignment(
                    assignment.id, status=BLOCKED, blocked_on="you",
                    note=f"Put to you and waiting: {plan.title}",
                )
                self._ask_the_user(idea, assignment.id, plan.title)
        self.ideas.set_status(idea.id, IN_PROGRESS)
        first = min(plans, key=lambda p: p.seq)
        self._say(
            "manager",
            {"kind": "handout", "idea": idea.title, "split": split,
             "first": f"{first.owner} on “{first.title}”"},
            cycle,
            kind=KIND_DECISION,
        )

    # ------------------------------------------------------------------
    # doing the work
    # ------------------------------------------------------------------
    def work_board(self, cycle: int) -> list[dict]:
        """Everyone picks up their next piece and does one pass at it."""
        done: list[dict] = []
        for key in roles.STANDUP_ORDER:
            assignment = self.ideas.next_for(key)
            if assignment is None or assignment.status == BLOCKED:
                # Blocked work sits on the desk so you can see it, but nobody
                # picks at it until whatever it is waiting on arrives.
                continue
            idea = self.ideas.idea(assignment.idea_id)
            if idea is None or idea.status not in LIVE_STATUSES:
                continue

            signals = read_idea(idea.text)
            result = do_work(assignment, idea, signals, self.ledger.cash)
            self.ideas.update_assignment(
                assignment.id,
                status=result.status,
                blocked_on=result.blocked_on,
                note=result.note,
            )
            self._say(
                key,
                {"kind": "work", "title": assignment.title, "status": result.status,
                 "note": result.note, "blocked_on": result.blocked_on},
                cycle,
                kind=KIND_ALERT if result.status == BLOCKED else KIND_RESULT,
            )
            if result.question:
                self._ask_the_user(idea, assignment.id, result.question)
            done.append({
                "assignment_id": assignment.id, "owner": key,
                "status": result.status, "idea_id": idea.id,
            })
            self._refresh_brief(idea)
            self._maybe_finish(idea, cycle)
        return done

    def _ask_the_user(self, idea: Idea, assignment_id: int, question: str) -> None:
        self.queue.add(HumanTask(
            title=question,
            body=(f"From the work on “{idea.title}”.\n\n"
                  f"Answer it in the office, or run:\n"
                  f"  forge answer {assignment_id} \"your answer\""),
            venture=f"idea:{idea.id}",
            payoff_cents=0,
            key=f"idea:{idea.id}:ask:{assignment_id}",
        ))

    def answer(self, assignment_id: int, text: str) -> dict:
        """Answer a question the office put to you, and unblock the work."""
        assignment = self.ideas.assignment(assignment_id)
        if assignment is None:
            raise KeyError(f"no assignment {assignment_id}")
        text = text.strip()
        if not text:
            raise ValueError("an answer needs some words in it")

        self.ideas.update_assignment(
            assignment_id, status=DONE, blocked_on="", note=f"You said: {text}"
        )
        for task in self.queue.open_tasks(limit=100):
            if task.title == assignment.title and task.venture == f"idea:{assignment.idea_id}":
                self.queue.resolve(task.id, "done")

        idea = self.ideas.idea(assignment.idea_id)
        if idea is not None:
            self._say(
                "manager",
                {"kind": "finding", "summary":
                    f"You answered “{assignment.title}” — noted against “{idea.title}”."},
                self.memory.cycle,
            )
            self._refresh_brief(idea)
            self._maybe_finish(idea, self.memory.cycle)
        return {"assignment_id": assignment_id, "status": DONE}

    def _maybe_finish(self, idea: Idea, cycle: int) -> None:
        """An idea is delivered when every piece of it is."""
        assignments = self.ideas.assignments(idea_id=idea.id)
        if not assignments or any(a.status != DONE for a in assignments):
            return
        current = self.ideas.idea(idea.id)
        if current is None or current.status == DELIVERED:
            return
        self.ideas.set_status(idea.id, DELIVERED)
        brief = self._refresh_brief(current)
        self._say(
            "manager",
            {"kind": "delivered", "idea": idea.title, "brief": str(brief)},
            cycle,
            kind=KIND_RULING,
        )
        self.queue.add(HumanTask(
            title=f"Read what we worked out about “{idea.title}”",
            body=(f"Everything we can do without you is done.\n\n{brief}\n\n"
                  f"If you want it taken further, tell the manager what changed."),
            venture=f"idea:{idea.id}",
            artifact=str(brief),
            key=f"idea:{idea.id}:brief",
        ))

    def _refresh_brief(self, idea: Idea) -> Path:
        current = self.ideas.idea(idea.id) or idea
        return write_brief(
            self.cfg.out_dir,
            current,
            self.ideas.reviews(idea.id),
            self.ideas.assignments(idea_id=idea.id),
        )

    # ------------------------------------------------------------------
    # the standup
    # ------------------------------------------------------------------
    def standup(self, *, force: bool = False, dry_run: bool = False) -> StandupReport:
        cycle = self.memory.next_cycle()
        report = StandupReport(cycle=cycle)
        log.info("standup #%d", cycle)

        if self.orch.halted:
            report.halted = True
            self._say(
                "manager",
                {"kind": "finding", "summary":
                    f"We are halted: {self.ledger.get_state('treasury:halt_reason', '')}. "
                    f"Nothing moves until you resume us."},
                cycle,
                kind=KIND_ALERT,
            )
            return report

        if dry_run:
            self._say("manager", {"kind": "finding",
                                  "summary": "Dry run — we talked, nothing was changed."}, cycle)
            report.remarks = self._remarks(cycle)
            return report

        # 1. Anything new gets read before anything else gets done.
        for idea in self.ideas.ideas(status=PROPOSED):
            report.vetted.append(self.vet(idea, cycle))

        # 2. Everyone picks up their next piece.
        report.worked = self.work_board(cycle)

        # 3. Ventures, if any are configured. Most offices start with none.
        if self.orch.ventures:
            self._run_ventures(cycle, force, report)

        if not report.vetted and not report.worked and not report.ran:
            self._say(
                "manager",
                {"kind": "finding", "summary":
                    "Nothing on the board. Bring us something and we'll get to work."},
                cycle,
            )

        report.remarks = self._remarks(cycle)
        self.ledger.check_invariant()
        return report

    def _run_ventures(self, cycle: int, force: bool, report: StandupReport) -> None:
        for name in list(self.orch.ventures):
            verdict, reason = self.survival.review(name)
            report.verdicts[name] = verdict.value
            if verdict in {SurvivalVerdict.STRIKE, SurvivalVerdict.ELIMINATE, SurvivalVerdict.PROMOTE}:
                self.survival.apply(name, verdict, reason)
                self._say(
                    "banker",
                    {"kind": "verdict", "verdict": verdict.value, "venture": name, "reason": reason},
                    cycle,
                    kind=KIND_RULING,
                )

        result = self.orch.run_cycle(force=force)
        for run in result.runs:
            if not run.ran:
                if run.skipped_reason:
                    report.blocked[run.name] = run.skipped_reason
                continue
            report.ran.append(run.name)
            self.survival.note_tick(run.name)
            if run.error:
                self._say(
                    "maker",
                    {"kind": "result", "summary":
                        f"{run.name} fell over. Last line: "
                        f"{run.error.strip().splitlines()[-1][:160]}"},
                    cycle, kind=KIND_ALERT,
                )
            elif run.result is not None:
                for note in run.result.notes:
                    self._say("maker", {"kind": "result", "summary": f"{run.name}: {note}"},
                              cycle, kind=KIND_RESULT)

    def _remarks(self, cycle: int) -> list[dict]:
        return [r.to_dict() for r in self.memory.feed(limit=60) if r.cycle == cycle][::-1]

    # ------------------------------------------------------------------
    # what everyone can see
    # ------------------------------------------------------------------
    def context(self, *, include_wallet: bool = True) -> dict[str, Any]:
        snap = self.orch.snapshot()
        ventures = []
        for v in snap["ventures"]:
            st = self.survival.status(v["name"])
            ventures.append({
                **v,
                "survival_state": st.state,
                "survival_strikes": st.strikes,
                "survival_reason": st.reason,
                "in_grace": self.survival.in_grace(v["name"]),
            })

        ideas = []
        for idea in self.ideas.ideas(limit=40):
            ideas.append({
                **idea.to_dict(),
                "tally": self.ideas.tally(idea.id),
                "progress": self.ideas.progress(idea.id),
                "reviews": [r.to_dict() for r in self.ideas.reviews(idea.id)],
                "assignments": [a.to_dict() for a in self.ideas.assignments(idea_id=idea.id)],
            })

        desks = {}
        for key in roles.STANDUP_ORDER:
            current = self.ideas.next_for(key)
            desks[key] = current.to_dict() if current else None

        return {
            "treasury": {
                "seed_cents": snap["seed_cents"],
                "cash_cents": snap["cash_cents"],
                "equity_cents": snap["equity_cents"],
                "net_pnl_cents": snap["net_pnl_cents"],
                "floor_cents": snap["floor_cents"],
                "halted": snap["halted"],
                "halt_reason": snap["halt_reason"],
            },
            "ideas": ideas,
            "open_ideas": len([i for i in ideas if i["status"] in OPEN_STATUSES]),
            "desks": desks,
            "ventures": ventures,
            "open_tasks": snap["open_tasks"],
            "tasks": [
                {
                    "id": t.id, "title": t.title, "body": t.body, "venture": t.venture,
                    "artifact": t.artifact, "url": t.url, "payoff_cents": t.payoff_cents,
                }
                for t in self.queue.open_tasks(limit=10)
            ],
            "wallet": self.wallet.snapshot() if include_wallet else {},
            "recent": [r.to_dict() for r in self.memory.feed(limit=8)],
            "cycle": self.memory.cycle,
            "brain": self.brain.label,
            "staff": [roles.PERSONAS[k].to_dict() for k in roles.STANDUP_ORDER],
        }

    # ------------------------------------------------------------------
    def ask(self, persona_key: str, question: str) -> dict:
        persona = roles.get(persona_key)
        self.memory.add_chat(persona_key, "user", question)
        history = [m.to_dict() for m in self.memory.thread(persona_key)]
        answer = self.brain.reply(persona, question, self.context(), history)
        self.memory.add_chat(persona_key, "staff", answer)
        return {"persona": persona_key, "question": question, "answer": answer, "ts": time.time()}

    def _say(self, persona_key: str, substance: dict, cycle: int, kind: str = KIND_NOTE) -> None:
        persona = roles.get(persona_key)
        text = self.brain.render(persona, substance)
        self.memory.say(persona_key, text, kind=kind, meta=substance, cycle=cycle)

    def resolve_income_target(self, name: str) -> str | None:
        """Work out what a payment should be booked against.

        Money can arrive from a venture that runs on a schedule, or from an
        idea that never became one. Both are legitimate; ``4``, ``idea:4``
        and ``coloringbook`` all resolve here so you never have to remember
        which kind of thing you are looking at.
        """
        name = (name or "").strip()
        if not name:
            return None
        if name in self.orch.ventures or name in self.ledger.ventures():
            return name

        raw = name[5:] if name.lower().startswith("idea:") else name
        if raw.isdigit() and self.ideas.idea(int(raw)) is not None:
            return f"idea:{int(raw)}"
        return None

    def income_targets(self) -> list[str]:
        """Everything a payment could be booked against, for error messages."""
        targets = sorted(set(self.orch.ventures) | set(self.ledger.ventures()))
        targets += [f"idea:{i.id}" for i in self.ideas.ideas(limit=20)]
        return targets

    def record_income(self, venture: str, amount_usd: float, memo: str) -> int:
        """Book real money that arrived from outside the office."""
        cents = usd(amount_usd)
        self.ledger.record_revenue(venture, cents, memo)
        self.memory.say(
            "banker",
            f"Booked {cents_to_str(cents)} against {venture}: {memo}.",
            kind=KIND_RULING,
            cycle=self.memory.cycle,
        )
        return cents

    def close(self) -> None:
        self.orch.close()


def _survival_rules(options: dict) -> SurvivalRules:
    return SurvivalRules(
        grace_ticks=int(options.get("grace_ticks", 8)),
        grace_hours=float(options.get("grace_hours", 24)),
        min_profit_cents=usd(options.get("min_profit_usd", 1.0)),
        max_strikes=int(options.get("max_strikes", 3)),
    )
