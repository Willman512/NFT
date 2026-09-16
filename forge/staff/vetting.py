"""Reading an idea, and forming an opinion about it.

Nothing here has a list of approved business types. An idea arrives as free
text and the office works out what it can from the words themselves, then
each member of staff applies their own lens to it.

The bias is deliberate and it is toward curiosity: the question is never
"can I find a reason to refuse this", it is "what is the cheapest version
of this we could try, and what would it teach us". An idea is only turned
away when it would cost more than the office has, or when it would put the
user somewhere they should not be. Everything else gets a small test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.money import cents_to_str
from .ideas import AGAINST, FOR, UNSURE

#: Places work can be sold or published. Recognising one tells us there is
#: a distribution channel, and usually that a human has to hold the account.
PLATFORMS = {
    "amazon": "Amazon", "kdp": "Amazon KDP", "etsy": "Etsy", "ebay": "eBay",
    "shopify": "Shopify", "gumroad": "Gumroad", "substack": "Substack",
    "patreon": "Patreon", "youtube": "YouTube", "tiktok": "TikTok",
    "instagram": "Instagram", "reddit": "Reddit", "twitch": "Twitch",
    "discord": "Discord", "steam": "Steam", "app store": "the App Store",
    "play store": "the Play Store", "printify": "Printify",
    "redbubble": "Redbubble", "fiverr": "Fiverr", "upwork": "Upwork",
    "udemy": "Udemy", "spotify": "Spotify", "pinterest": "Pinterest",
    "notion": "Notion", "github": "GitHub",
}

#: A trailing ``*`` means "this word or anything that starts with it", so
#: "illustrat*" catches illustration and illustrator. Everything else has to
#: match a whole word — otherwise "ship" fires on "relationship" and
#: "print" fires on "printable", which are not the same idea at all.
RECURRING = ("subscription", "subscribe*", "monthly", "per month", "weekly",
             "recurring", "membership", "saas", "retainer")
CAPITAL = ("buy", "buying", "purchase*", "inventory", "ads", "advertising", "upfront",
           "equipment", "supplies", "wholesale", "deposit", "licence fee", "license fee")
PHYSICAL = ("ship", "shipping", "package", "packaging", "warehouse", "manufactur*",
            "delivery", "postage", "print on demand", "mug", "mugs", "t-shirt",
            "t-shirts", "tshirt", "sticker", "stickers", "inventory")
SKILLS = {
    "code": "writing software", "coding": "writing software", "app": "writing software",
    "apps": "writing software", "website": "building a site", "design*": "design work",
    "illustrat*": "illustration", "video": "video production", "videos": "video production",
    "edit": "editing", "editing": "editing", "photo*": "photography",
    "music": "music production", "writ*": "writing", "3d": "3D modelling",
    "voice": "voice work", "animat*": "animation",
}
AUTOMATABLE = ("automat*", "script", "scripts", "bot", "bots", "generat*", "api",
               "scrape", "scraping", "pipeline", "template", "templates",
               "procedural", "batch")
AUDIENCE_HINTS = ("customer*", "buyer*", "people who", "audience", "users", "readers",
                  "parents", "students", "teachers", "gamers", "kids", "seniors",
                  "beginners", "hobbyists", "developers", "owners", "fans", "for ")

#: Things that need a person to look at them before the office runs ahead.
CAUTION = {
    "crypto": "crypto",
    "day trading": "day trading",
    "forex": "forex",
    "investment advice": "investment advice",
    "financial advice": "financial advice",
    "medical": "medical claims",
    "health advice": "health advice",
    "legal advice": "legal advice",
    "gambling": "gambling",
    "betting": "betting",
    "scrape": "scraping a site that may forbid it",
    "copyright": "someone else's copyright",
    "trademark": "someone else's trademark",
    "fan art": "someone else's characters",
}

MONEY_RE = re.compile(r"\$\s?(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s?(?:dollars|usd|bucks)",
                      re.IGNORECASE)


def mentions(text: str, term: str) -> bool:
    """Whole-word match, or prefix match when the term ends in ``*``."""
    if term.endswith("*"):
        pattern = r"\b" + re.escape(term[:-1])
    elif term.endswith(" "):
        return term in text
    else:
        pattern = r"\b" + re.escape(term) + r"\b"
    return re.search(pattern, text) is not None


def _any(text: str, terms) -> bool:
    return any(mentions(text, t) for t in terms)


@dataclass
class Signals:
    """What the office could work out from the words alone."""

    prices: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    recurring: bool = False
    needs_capital: bool = False
    physical: bool = False
    automatable: bool = False
    names_audience: bool = False
    words: int = 0

    @property
    def thin(self) -> bool:
        """Too little to go on: the staff should ask rather than guess."""
        return self.words < 12 and not (self.prices or self.platforms)

    @property
    def needs_a_person(self) -> bool:
        return bool(self.platforms) or self.physical


def read_idea(text: str) -> Signals:
    """Pull whatever structure we can out of free text. Never raises."""
    low = text.lower()
    prices = []
    for whole, bare in MONEY_RE.findall(text):
        value = whole or bare
        if value:
            prices.append(f"${value}")
    return Signals(
        prices=sorted(set(prices))[:4],
        platforms=sorted({label for key, label in PLATFORMS.items() if mentions(low, key)}),
        skills=sorted({label for key, label in SKILLS.items() if mentions(low, key)}),
        cautions=sorted({label for key, label in CAUTION.items() if mentions(low, key)}),
        recurring=_any(low, RECURRING),
        needs_capital=_any(low, CAPITAL) or bool(prices),
        physical=_any(low, PHYSICAL),
        automatable=_any(low, AUTOMATABLE),
        names_audience=_any(low, AUDIENCE_HINTS),
        words=len(low.split()),
    )


def _listed(items: list[str], limit: int = 2) -> str:
    """Name a couple of things rather than reciting the whole list."""
    shown = items[:limit]
    rest = len(items) - len(shown)
    text = " and ".join(shown) if len(shown) == 2 else (shown[0] if shown else "")
    return f"{text} (and {rest} more)" if rest > 0 else text


@dataclass
class Verdict:
    stance: str
    headline: str
    points: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stance": self.stance,
            "headline": self.headline,
            "points": self.points,
            "questions": self.questions,
        }


# ----------------------------------------------------------------------
# one reviewer per desk
# ----------------------------------------------------------------------
def _largest_price_cents(sig: Signals) -> int:
    """The biggest number the idea names, in cents. 0 if it names none."""
    best = 0
    for price in sig.prices:
        try:
            best = max(best, int(round(float(price.lstrip("$").replace(",", "")) * 100)))
        except ValueError:
            continue
    return best


def banker_view(text: str, sig: Signals, purse_cents: int) -> Verdict:
    points, questions = [], []
    purse = cents_to_str(purse_cents)
    asking = _largest_price_cents(sig)

    if sig.needs_capital:
        points.append(f"This wants money before it earns any. We have {purse} uncommitted.")
        questions.append("What is the smallest amount that would still be a real test?")
    else:
        points.append(f"Nothing here obviously costs money up front, which is the "
                      f"best kind of idea to try. We have {purse} if it turns out to.")

    if sig.prices:
        points.append(f"You have named a price ({', '.join(sig.prices)}), which is more "
                      f"than most ideas arrive with.")
    else:
        questions.append("What does it cost the buyer, and what does one sale actually net us?")

    if sig.recurring:
        points.append("Recurring revenue is worth more per customer than a one-off, "
                      "and it also means a recurring cost if we are hosting anything.")

    if sig.physical:
        points.append("Physical goods mean stock, postage and returns. That is the "
                      "expensive kind of experiment.")

    # A ceiling, always. It is the banker's job to name the number.
    ceiling = min(max(purse_cents // 3, 0), 2000)
    points.append(f"I would cap the whole experiment at {cents_to_str(ceiling)} "
                  f"and stop if it has not shown anything by then.")

    # The number the idea names is the one that matters, not a vague sense
    # that it "costs something".
    if asking > purse_cents:
        return Verdict(
            AGAINST,
            f"It asks for {cents_to_str(asking)} and we have {purse}.",
            points,
            questions + [
                f"Is there a version of this that fits inside {cents_to_str(ceiling)}?",
            ],
        )
    if asking > ceiling:
        return Verdict(
            UNSURE,
            f"We could just about cover {cents_to_str(asking)}, but it would be most "
            f"of what we have.",
            points,
            questions + ["Could we test it at a tenth of that first?"],
        )
    if sig.needs_capital and purse_cents < 500:
        return Verdict(AGAINST, "We cannot fund this yet.", points,
                       questions + ["Can we do a version that costs nothing?"])
    if sig.physical:
        return Verdict(UNSURE, "Affordable only if we keep it tiny.", points, questions)
    return Verdict(FOR, f"Cheap enough to try inside a {cents_to_str(ceiling)} cap.",
                   points, questions)


def salesman_view(text: str, sig: Signals) -> Verdict:
    points, questions = [], []

    if sig.names_audience:
        points.append("You have at least gestured at who this is for, which puts it "
                      "ahead of most ideas that land on my desk.")
    else:
        questions.append("Who exactly buys this? Not 'people' — a person you could describe.")

    if sig.platforms:
        points.append(f"There is somewhere to put it: {', '.join(sig.platforms)}. "
                      f"That solves distribution, which is usually the hard half.")
    else:
        questions.append("Where would someone come across this? If we cannot answer "
                         "that, it does not matter how good it is.")

    if sig.prices:
        points.append(f"At {', '.join(sig.prices)} the question is what they are "
                      f"comparing it to, not whether it is cheap.")
    if sig.recurring:
        points.append("If we are asking for a subscription, it has to be worth opening "
                      "again next month, not just worth buying once.")

    questions.append("What does the buyer do today instead, and why is this better?")

    if sig.thin:
        return Verdict(UNSURE, "Not enough here for me to judge yet.", points, questions)
    if sig.names_audience and (sig.platforms or sig.prices):
        return Verdict(FOR, "There is a buyer and a place to reach them.", points, questions)
    return Verdict(UNSURE, "I can work with this once we name the buyer.", points, questions)


def researcher_view(text: str, sig: Signals) -> Verdict:
    points, questions = [], []

    # Name the riskiest assumption, specifically, from what we can see.
    if not sig.names_audience:
        assumption = "that anyone at all wants this"
        check = "find ten people who already complain about the problem, in public, this week"
    elif sig.prices:
        assumption = f"that they will pay {sig.prices[0]} rather than nod politely"
        check = f"offer it at {sig.prices[0]} to ten of them before building anything"
    elif sig.platforms:
        assumption = f"that we can get any distribution on {sig.platforms[0]}"
        check = f"look at what already ranks on {sig.platforms[0]} and how crowded it is"
    else:
        assumption = "that the thing we would make is the thing they would want"
        check = "describe it to five people and see whether they ask for it or change the subject"

    points.append(f"The riskiest assumption is {assumption}.")
    points.append(f"Cheapest way to check it: {check}.")

    if sig.automatable:
        points.append("Most of the work here sounds repeatable, which means if it does "
                      "work we can do it more than once. That raises the value of testing it.")
    if sig.cautions:
        points.append("Worth flagging: this touches " + _listed(sig.cautions) +
                      ". Not a reason to stop, but a reason to read the rules first.")
        questions.append("Have you checked the rules of whatever platform this sits on?")
    if sig.thin:
        questions.append("Can you say more about it? Right now I would be guessing.")

    questions.append("What would have to be true for this to be obviously worth doing?")

    stance = UNSURE if (sig.thin or sig.cautions) else FOR
    headline = ("Untested, but the test is cheap." if stance == FOR
                else "I need more before I would back it.")
    return Verdict(stance, headline, points, questions)


def maker_view(text: str, sig: Signals) -> Verdict:
    points, questions = [], []

    if sig.automatable:
        points.append("A good share of this could be scripted here rather than done by hand.")
    if sig.skills:
        points.append("It needs " + ", ".join(sig.skills) + ". Worth knowing whether "
                      "that is you, me, or someone we pay.")
    if sig.physical:
        points.append("There is a physical step in here. I cannot ship a box. "
                      "That part is yours or a supplier's.")
    if sig.platforms:
        points.append(f"Publishing to {sig.platforms[0]} needs an account in your name. "
                      f"I can get everything ready up to the upload button.")

    first_step = _first_step(sig)
    points.append(f"First concrete step: {first_step}")

    if not sig.automatable and not sig.skills:
        questions.append("What does the finished thing actually look like? I build better "
                         "from a description of the output than of the goal.")

    if sig.physical and not sig.automatable:
        return Verdict(UNSURE, "Buildable, but mostly by you rather than by me.", points, questions)
    return Verdict(FOR, "I can make a start on this.", points, questions)


def _first_step(sig: Signals) -> str:
    if sig.platforms:
        return f"write down exactly what a finished listing on {sig.platforms[0]} would contain"
    if sig.automatable:
        return "build the smallest script that produces one real example of the output"
    if sig.skills:
        return f"produce one sample by hand so we can see whether the {sig.skills[0]} holds up"
    return "write a one-page description of the finished thing, so we are all picturing the same object"


VIEWS = {
    "banker": banker_view,
    "salesman": salesman_view,
    "researcher": researcher_view,
    "maker": maker_view,
}


def review_idea(persona_key: str, text: str, purse_cents: int = 0) -> Verdict:
    """The scripted opinion of one desk. Always returns something usable."""
    sig = read_idea(text)
    if persona_key == "banker":
        return banker_view(text, sig, purse_cents)
    view = VIEWS.get(persona_key)
    if view is None:
        return Verdict(UNSURE, "No opinion.", [], [])
    return view(text, sig)


# ----------------------------------------------------------------------
# the manager's synthesis
# ----------------------------------------------------------------------
@dataclass
class Decision:
    status: str
    reason: str


def decide(verdicts: dict[str, Verdict], sig: Signals, purse_cents: int) -> Decision:
    """Turn four opinions into one call.

    Open minds, with two brakes: the office does not spend money it does
    not have, and it does not walk the user into something they should look
    at themselves first.
    """
    from .ideas import ACCEPTED, DECLINED, PARKED

    against = [k for k, v in verdicts.items() if v.stance == AGAINST]
    unsure = [k for k, v in verdicts.items() if v.stance == UNSURE]

    if "banker" in against:
        return Decision(
            PARKED,
            f"Ines will not fund it as described — {verdicts['banker'].headline} "
            f"Parked until there is a cheaper version of it, or until the treasury "
            f"is bigger.",
        )

    if sig.cautions:
        return Decision(
            PARKED,
            "This touches " + _listed(sig.cautions) + ", so it waits on you rather "
            "than on us. Say you have looked at the rules and I will start it.",
        )

    if len(against) >= 2:
        return Decision(
            DECLINED,
            "Two of us think this does not work as it stands: "
            + "; ".join(f"{k} — {verdicts[k].headline}" for k in against),
        )

    if sig.thin:
        return Decision(
            PARKED,
            "There is not enough here yet for us to do anything useful with. "
            "Give me a few more sentences and I will put it straight back in.",
        )

    lean = "everyone is behind it" if not unsure and not against else (
        "not everyone is convinced, but nobody can afford to be sure yet either"
    )
    return Decision(
        ACCEPTED,
        f"Taking it as a small test — {lean}. The cost of finding out is lower than "
        f"the cost of arguing about it.",
    )
