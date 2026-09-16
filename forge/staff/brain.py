"""How the staff turn decisions into words.

The important split: the office works out *what* is true and what it has
decided, on its own, from the ledger. A brain only decides *how that is
said*. So the scripted brain and the Claude-backed brain make exactly the
same business decisions — swapping them changes the prose and nothing else,
and the office is fully usable with no API key and no network.
"""

from __future__ import annotations

import json
import os
import random
from abc import ABC, abstractmethod
from typing import Any

from ..core.log import get_logger
from ..core.money import cents_to_str
from .roles import Persona

log = get_logger("staff.brain")

#: Short replies by design; these are desk conversations, not essays.
CHAT_MAX_TOKENS = 700
REMARK_MAX_TOKENS = 300
REVIEW_MAX_TOKENS = 900
DEFAULT_MODEL = "claude-opus-5"


class Brain(ABC):
    name = "brain"
    #: Shown in the UI so you always know who is actually talking.
    label = "brain"

    @property
    def available(self) -> bool:
        return True

    @abstractmethod
    def render(self, persona: Persona, substance: dict[str, Any]) -> str:
        """Turn a structured finding into one line for the activity feed."""

    @abstractmethod
    def reply(
        self,
        persona: Persona,
        question: str,
        context: dict[str, Any],
        history: list[dict[str, str]],
    ) -> str:
        """Answer the user, in character, grounded in ``context``."""

    def review(
        self,
        persona: Persona,
        idea_text: str,
        scripted: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Form an opinion on an idea.

        ``scripted`` is the office's own deterministic reading, and it is
        the floor: a brain that cannot do better returns it unchanged.
        """
        return scripted


# ----------------------------------------------------------------------
# scripted
# ----------------------------------------------------------------------
class ScriptedBrain(Brain):
    """No model, no key, no network. Deterministic and always available.

    It reads the same structured facts the Claude brain would and states
    them plainly. Less personable, equally correct.
    """

    name = "scripted"
    label = "scripted (no API key needed)"

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    # -- feed lines -----------------------------------------------------
    def render(self, persona: Persona, substance: dict[str, Any]) -> str:
        kind = substance.get("kind", "note")
        handler = getattr(self, f"_render_{kind}", None)
        if handler:
            return handler(persona, substance)
        return substance.get("summary") or "Nothing to report."

    def _render_finding(self, persona: Persona, s: dict) -> str:
        return s["summary"]

    def _render_verdict(self, persona: Persona, s: dict) -> str:
        v, name, reason = s["verdict"], s["venture"], s["reason"]
        if v == "eliminate":
            return f"{name} is finished. It {reason}. Capital is back in the treasury."
        if v == "strike":
            return f"{name} gets a strike: it {reason}."
        if v == "promote":
            return f"{name} stays: it {reason}."
        if v == "already_dead":
            return f"{name} was already eliminated; leaving it alone."
        return f"{name}: {reason}."

    def _render_budget(self, persona: Persona, s: dict) -> str:
        if s["cents"] <= 0:
            return f"No capital for {s['venture']} this cycle: {s['reason']}."
        return f"{s['venture']} gets {cents_to_str(s['cents'])} to work with — {s['reason']}."

    def _render_pitch(self, persona: Persona, s: dict) -> str:
        return (
            f"Next one should be {s['theme']} at ${s['price']:.2f}. {s['reason']}"
        )

    def _render_decision(self, persona: Persona, s: dict) -> str:
        running = s.get("running") or []
        if not running:
            return f"Nothing runs this cycle. {s.get('reason', '')}".strip()
        return f"Running {', '.join(running)} this cycle. {s.get('reason', '')}".strip()

    def _render_result(self, persona: Persona, s: dict) -> str:
        return s["summary"]

    # -- ideas ---------------------------------------------------------
    @staticmethod
    def _short(title: str, limit: int = 46) -> str:
        """Feed lines quote the idea constantly; a whole sentence is too much."""
        title = title.strip().rstrip(".")
        return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"

    def _render_review(self, persona: Persona, s: dict) -> str:
        lead = {"for": "I'm for it", "against": "I'm against it", "unsure": "I'm not sure yet"}
        text = (f"On “{self._short(s['idea'])}” — "
                f"{lead.get(s['stance'], 'I have a view')}. {s['headline']}")
        if s.get("points"):
            text += " " + s["points"][0]
        return text

    def _render_call(self, persona: Persona, s: dict) -> str:
        word = {
            "accepted": "We're taking it on",
            "parked": "Parking it for now",
            "declined": "We're passing on it",
        }.get(s["status"], s["status"])
        return f"“{self._short(s['idea'])}”: {word}. {s['reason']}"

    def _render_handout(self, persona: Persona, s: dict) -> str:
        who = ", ".join(f"{owner} ({n})" for owner, n in s["split"].items())
        return f"Split “{self._short(s['idea'])}” between {who}. First up: {s['first']}"

    def _render_work(self, persona: Persona, s: dict) -> str:
        head = s["title"]
        first_line = (s.get("note") or "").strip().splitlines()
        tail = f" — {first_line[0]}" if first_line else ""
        if s["status"] == "blocked":
            return f"{head}: waiting on {s.get('blocked_on') or 'someone'}.{tail}"
        if s["status"] == "done":
            return f"Finished: {head}.{tail}"
        return f"Working on {head}.{tail}"

    def _render_delivered(self, persona: Persona, s: dict) -> str:
        return (f"“{self._short(s['idea'])}” is as far as we can take it without you. "
                f"The brief is at {s['brief']}.")

    # -- conversation ---------------------------------------------------
    def reply(
        self,
        persona: Persona,
        question: str,
        context: dict[str, Any],
        history: list[dict[str, str]],
    ) -> str:
        q = question.lower()
        book = context.get("treasury", {})
        lines: list[str] = []

        def money_summary() -> str:
            return (
                f"Seed {cents_to_str(book.get('seed_cents', 0))}, "
                f"now {cents_to_str(book.get('equity_cents', 0))} "
                f"({cents_to_str(book.get('net_pnl_cents', 0))} net). "
                f"{cents_to_str(book.get('cash_cents', 0))} of that is uncommitted."
            )

        if _any(q, "money", "profit", "balance", "cost", "earn", "revenue", "doing", "how are we", "p&l", "pnl"):
            lines.append(money_summary())
            for v in context.get("ventures", []):
                lines.append(
                    f"- {v['name']}: {cents_to_str(v['income_cents'])} in, "
                    f"{cents_to_str(v['expense_cents'])} out, "
                    f"{'pays for itself' if v['paid_for_itself'] else 'not paying for itself yet'}"
                    f" ({v['survival_state']})."
                )
        elif _any(q, "kill", "elimin", "fire", "shut", "survive", "probation"):
            for v in context.get("ventures", []):
                lines.append(f"- {v['name']}: {v['survival_state']}, {v['survival_reason'] or 'no ruling yet'}.")
            if not lines:
                lines.append("Nothing has been judged yet.")
        elif _any(q, "wallet", "address", "sol", "crypto", "withdraw", "deposit"):
            w = context.get("wallet", {})
            if not w.get("address"):
                lines.append("No wallet is configured. Put your address in forge.yaml under wallet.address.")
            else:
                usd = f" (about ${w['usd']:,.2f})" if w.get("usd") is not None else ""
                lines.append(f"Wallet {w['address']} holds {w.get('sol', 0):.4f} SOL{usd}.")
                lines.append("It is watch-only, so nothing here can move it." if w.get("mode") == "watch"
                             else f"Mode is {w.get('mode')}.")
        elif _any(q, "sell", "price", "theme", "market", "buyer", "keyword"):
            hint = context.get("sales", {})
            lines.append(
                f"Next book is set to {hint.get('theme', 'whatever comes up')} "
                f"at ${hint.get('price', 0):.2f}. {hint.get('reason', '')}".strip()
            )
            lines.append(f"{context.get('open_tasks', 0)} finished book(s) are waiting on you to upload.")
        elif _any(q, "trade", "memecoin", "coin", "token", "risk"):
            t = context.get("trader", {})
            if not t:
                lines.append("The trader is not loaded.")
            else:
                lines.append(
                    f"Mode {t.get('mode')}, {t.get('closed_trades', 0)} closed trades, "
                    f"expectancy ${t.get('expectancy_usd', 0):.4f} a trade."
                )
                blockers = t.get("live_blockers") or []
                lines.append(
                    "It cannot touch real money: " + "; ".join(blockers) + "."
                    if blockers else "It has cleared every gate for live trading."
                )
        elif _any(q, "task", "todo", "waiting", "upload", "kdp"):
            tasks = context.get("tasks", [])
            if not tasks:
                lines.append("Nothing is waiting on you.")
            for t in tasks[:5]:
                lines.append(f"- {t['title']}" + (f" ({t['artifact']})" if t.get("artifact") else ""))
        elif _any(q, "who are you", "what do you do", "your job", "help"):
            lines.append(persona.remit)
        else:
            lines.append(persona.remit)
            lines.append(money_summary())
            recent = context.get("recent", [])
            if recent:
                lines.append(f"Last thing that happened: {recent[0]['text']}")

        return "\n".join(lines).strip()


def _any(text: str, *needles: str) -> bool:
    return any(n in text for n in needles)


# ----------------------------------------------------------------------
# Claude
# ----------------------------------------------------------------------
class ClaudeBrain(Brain):
    """Real conversation, through the Anthropic API.

    Costs money per message, which the office treats as a running cost of
    the manager's desk rather than of any one venture. If a call fails for
    any reason the scripted brain answers instead, so the office never goes
    quiet because of a network problem.
    """

    name = "claude"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None, effort: str = "low"):
        self.model = model
        self.effort = effort
        self._fallback = ScriptedBrain()
        self._client = None
        self._key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    @property
    def label(self) -> str:
        return f"Claude ({self.model})"

    @property
    def available(self) -> bool:
        if not self._key:
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._key)
        return self._client

    # ------------------------------------------------------------------
    def _system(self, persona: Persona, context: dict[str, Any]) -> str:
        return (
            f"You are {persona.name}, {persona.title} at a small automated business "
            f"called Forge that the user owns and runs on their own computer.\n\n"
            f"Your remit: {persona.remit}\n"
            f"How you speak: {persona.voice}\n\n"
            "Rules that matter more than style:\n"
            "- Every number you state must come from the office state below. Never "
            "invent a figure, a trade, or a sale.\n"
            "- If the state does not answer the question, say so plainly.\n"
            "- You cannot take actions in this reply. If the user asks for something "
            "done, say who does it and what they should click or run.\n"
            "- Be honest about how badly things are going. The user is better served "
            "by a flat 'this is losing money' than by encouragement.\n"
            "- Keep it to a few sentences unless asked for detail.\n\n"
            f"Current office state (JSON):\n{json.dumps(context, default=str)[:12000]}"
        )

    def _call(self, system: str, messages: list[dict], max_tokens: int) -> str:
        import anthropic

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                # Short desk replies; low effort keeps latency and cost down.
                output_config={"effort": self.effort},
                messages=messages,
            )
        except anthropic.NotFoundError as exc:
            log.error("model %s not available: %s", self.model, exc)
            raise
        except anthropic.AuthenticationError as exc:
            log.error("Anthropic rejected the API key: %s", exc)
            raise
        except anthropic.RateLimitError as exc:
            log.warning("rate limited by Anthropic: %s", exc)
            raise
        except anthropic.APIStatusError as exc:
            log.warning("Anthropic API error %s: %s", exc.status_code, exc)
            raise
        except anthropic.APIConnectionError as exc:
            log.warning("could not reach Anthropic: %s", exc)
            raise

        if response.stop_reason == "refusal":
            return "I'd rather not answer that one."
        return "".join(b.text for b in response.content if b.type == "text").strip()

    # ------------------------------------------------------------------
    def render(self, persona: Persona, substance: dict[str, Any]) -> str:
        plain = self._fallback.render(persona, substance)
        try:
            return self._call(
                f"You are {persona.name}, {persona.title}. {persona.voice}\n"
                "Rewrite the given fact as a single short line for a team activity feed, "
                "in your own voice. Keep every number exactly as given. One sentence, "
                "two at most. No preamble, no quotes around it.",
                [{"role": "user", "content": f"Fact: {plain}\n\nStructured detail: {json.dumps(substance, default=str)[:2000]}"}],
                REMARK_MAX_TOKENS,
            ) or plain
        except Exception as exc:  # noqa: BLE001 - the feed must never stall
            log.warning("falling back to the scripted brain for a remark: %s", exc)
            return plain

    def review(
        self,
        persona: Persona,
        idea_text: str,
        scripted: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Read the idea properly, rather than pattern-matching its words.

        This is where a language model genuinely earns its place: the
        scripted reviewer can only react to words it recognises, and most
        ideas are not made of those. The hard limits stay outside this
        method — whether the office can afford something, and whether it
        needs a person to look first, are decided from the ledger and the
        signals, not from anything said here.
        """
        system = (
            f"You are {persona.name}, {persona.title} at a small business the user "
            f"owns and runs from their own computer.\n\n"
            f"What you look for in any idea: {persona.lens}\n"
            f"How you talk: {persona.voice}\n\n"
            "Someone has brought an idea to the office. Give your honest read of it "
            "from your own angle only — the others cover theirs.\n\n"
            "You are open minded by default. The question is never whether the idea is "
            "perfect, it is what the cheapest version worth trying would be. Say so "
            "plainly when something will not work, but do not reach for reasons to "
            "refuse.\n\n"
            "Reply with JSON and nothing else, in exactly this shape:\n"
            '{"stance": "for" | "against" | "unsure", '
            '"headline": "one short sentence", '
            '"points": ["two to four specific observations"], '
            '"questions": ["nothing, or up to two questions only the owner can answer"]}\n\n'
            "Be specific to this idea. Generic advice is worse than nothing.\n\n"
            f"What the office currently has: {json.dumps(_purse(context))}"
        )
        try:
            raw = self._call(
                system,
                [{"role": "user", "content": f"The idea:\n\n{idea_text}"}],
                REVIEW_MAX_TOKENS,
            )
            parsed = _parse_review(raw)
            if parsed:
                return parsed
            log.warning("could not parse a review from the model; using the scripted read")
        except Exception as exc:  # noqa: BLE001 - vetting must never stall
            log.warning("falling back to the scripted review: %s", exc)
        return scripted

    def reply(
        self,
        persona: Persona,
        question: str,
        context: dict[str, Any],
        history: list[dict[str, str]],
    ) -> str:
        messages = [
            {"role": "user" if h["speaker"] == "user" else "assistant", "content": h["text"]}
            for h in history[-12:]
        ]
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": question})
        elif messages[-1]["content"] != question:
            messages.append({"role": "user", "content": question})
        try:
            return self._call(self._system(persona, context), messages, CHAT_MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001 - always answer with something true
            log.warning("falling back to the scripted brain for a reply: %s", exc)
            plain = self._fallback.reply(persona, question, context, history)
            return f"{plain}\n\n(Answered without the language model: {type(exc).__name__}.)"


def _purse(context: dict[str, Any]) -> dict:
    treasury = context.get("treasury") or {}
    return {
        "uncommitted_cents": treasury.get("cash_cents"),
        "equity_cents": treasury.get("equity_cents"),
        "open_ideas": len(context.get("ideas") or []),
    }


def _parse_review(raw: str) -> dict[str, Any] | None:
    """Pull the JSON object out of a reply, tolerantly.

    Models sometimes wrap JSON in a fenced block or a sentence of preamble,
    so the object is located rather than assumed to be the whole string.
    """
    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None

    stance = str(data.get("stance", "")).strip().lower()
    if stance not in {"for", "against", "unsure"}:
        return None
    headline = str(data.get("headline", "")).strip()
    if not headline:
        return None

    def strings(key: str, limit: int) -> list[str]:
        value = data.get(key)
        if not isinstance(value, list):
            return []
        return [str(v).strip() for v in value if str(v).strip()][:limit]

    return {
        "stance": stance,
        "headline": headline[:240],
        "points": strings("points", 5),
        "questions": strings("questions", 3),
    }


def pick_brain(options: dict[str, Any] | None = None) -> Brain:
    """Choose the best brain available, unless the config pins one."""
    options = options or {}
    want = options.get("brain", "auto")
    model = options.get("model", DEFAULT_MODEL)

    if want == "scripted":
        return ScriptedBrain()
    if want == "claude":
        brain = ClaudeBrain(model=model)
        if not brain.available:
            log.warning(
                "brain is set to claude but ANTHROPIC_API_KEY is unset or the anthropic "
                "package is missing; using the scripted brain"
            )
            return ScriptedBrain()
        return brain

    brain = ClaudeBrain(model=model)
    return brain if brain.available else ScriptedBrain()
