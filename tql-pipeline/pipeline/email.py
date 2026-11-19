"""Intro email drafts that follow the tql-prospect-research rules. Draft only — you edit and send."""
import re

from .db import jload
from .report import FREIGHT_ROLES

# signal -> (subject template, pain guess, one TQL capability line). First match wins.
ANGLES = [
    ("flatbed_oversize", "flatbeds out of {city}", "finding flatbeds that show up on time is a weekly fight",
     "We cover flatbed, step deck, and heavy haul with 110,000+ vetted carriers.", "flatbed loads"),
    ("reefer", "reefer loads out of {city}", "keeping reefer trucks on time matters a lot when loads can't sit",
     "We run reefer every day, with a team up 24/7 if a truck runs late.", "reefer loads"),
    ("retail_dealers", "getting trucks to {customers}", "hitting tight delivery windows for your {customers} keeps you busy",
     "We move truckload and LTL into retail DCs, with live tracking in TQL TRAX.", "retail deliveries"),
    ("cross_border", "loads into mexico and canada", "moving freight across the border takes more calls than it should",
     "Our Monterrey team handles Mexico loads, and we cross into Canada daily.", "cross-border loads"),
    ("hazmat", "hazmat loads out of {city}", "finding good hazmat carriers is not easy",
     "We have vetted hazmat carriers and a claims team if anything goes wrong.", "hazmat loads"),
    ("distributor", "extra trucks out of {city}", "your usual carriers get tight when orders spike",
     "We have 110,000+ vetted carriers, so we can add trucks fast.", "outbound loads"),
    ("manufacturer", "outbound trucks from {city}", "your usual carriers get tight when orders pile up",
     "We have 110,000+ vetted carriers, so we can add trucks fast.", "outbound loads"),
]
DEFAULT = (None, "pallets out of {city}", "getting pallets out the door on time takes more chasing than it should",
           "We cover truckload, partials, and LTL with 45+ LTL carriers.", "pallet loads")
BANNED = ["dedicated contact", "right in our wheelhouse", "hope this finds you well", "touching base",
          "circle back", "synergy", "you get me", "roi"]


def _snippet(detail, max_words=8):
    """Quote a whole short phrase or nothing (a cut-off quote reads like a bot)."""
    words = re.sub(r"[\"“”]", "", detail).split()
    return " ".join(words).rstrip(".,;:!-–—") if len(words) <= max_words else ""


def _first_name(contacts):
    for c in contacts:
        name, _, title = c.partition(" — ")
        if any(k in title.lower() for k in FREIGHT_ROLES):
            return name.split()[0]
    return None


def _fit_subject(template, city, customers):
    """3-5 words, lowercase. Drop the state, then the city, if it runs long."""
    for place in (city.replace(",", ""), city.split(",")[0], "your plant"):
        s = template.format(city=place.lower() or "your plant", customers=customers).lower()
        if len(s.split()) <= 5:
            return s
    return s


def context(row):
    r = jload(row["research"], {})
    f = jload(row["features"], {})
    hits = r.get("signal_hits", {})
    # Saying "Canada" or "Mexico" once isn't a cross-border shipper; a Mexican city/state or "cross-border" is.
    real_xborder = any("cross" in h.lower() for h in hits.get("cross_border", [])) \
        or any(m.lower() not in ("mexico", "méxico") for m in r.get("mx_mentions", []))
    # Food/reefer companies mention "stainless steel" etc.; don't pitch them flatbeds.
    foodish = f.get("food_producer")

    def usable(a):
        if not f.get(a[0]):
            return False
        if a[0] == "cross_border":
            return real_xborder
        if a[0] == "flatbed_oversize" and foodish:
            return False
        if a[0] == "reefer" and f.get("flatbed_oversize") and not f.get("food_producer"):
            return False  # e.g. precast plants with temperature-controlled curing
        return True
    _, subj, pain, capability, freight = next((a for a in ANGLES if usable(a)), DEFAULT)
    locs = r.get("us_locations", []) + r.get("ca_locations", [])
    city = ""
    if locs:
        c, _, rest = locs[0].partition(",")
        city = f"{c.strip()}, {rest.split()[0]}" if rest.split() else c.strip()
    customers = "dealers" if "dealer" in " ".join(r.get("signal_hits", {}).get("retail_dealers", [])).lower() else "retailers"
    first = _first_name(r.get("contacts", []))
    return {
        "subject": _fit_subject(subj, city, customers),
        "pain": pain.format(customers=customers), "capability": capability, "freight": freight,
        "city": city, "first": first, "french": bool(f.get("french_site")),
        "snippet": _snippet(r["detail"]) if r.get("detail") else "",
        "name": row["name"] or row["domain"], "phones": r.get("phones", []),
    }


def draft(row, phone="[Phone]", touch=1):
    """touch 1 = intro, 2 = short bump (reply in the same thread), 3 = last note."""
    c = context(row)
    first = c["first"] or "[First Name]"
    greet = f"Bonjour {first}," if c["french"] else f"Hi {first},"
    where = f" out of {c['city']}" if c["city"] else ""
    if touch <= 1:
        subject = c["subject"]
        opener = (f"Saw \"{c['snippet']}\" on your site and wanted to reach out." if c["snippet"]
                  else f"I was reading about {c['name']} and wanted to reach out.")
        body = [opener, f"I'm guessing {c['pain']}.", f"I'm Will, a broker with TQL. {c['capability']}",
                "Would it help to have another option when your usual trucks fall through?"]
        words = (50, 90)
    elif touch == 2:
        subject = "re: " + c["subject"]
        body = [f"Quick follow-up on my note about {c['freight']}{where}.",
                "If a lane gets tight or a truck falls off, I'm glad to quote it the same day.",
                "Is that worth a quick look next time it happens?"]
        words = (25, 60)
    else:
        subject = "re: " + c["subject"]
        body = ["I'll stop filling up your inbox after this one.",
                f"If you ever have {c['freight']} your carriers can't cover, I'm a call away at {phone}.",
                "If freight isn't your area, who would be the right person to ask?"]
        words = (25, 60)
    text = "\n\n".join(body)
    warnings = check(subject.replace("re: ", ""), text, words)
    if first == "[First Name]":
        warnings.append("No freight contact found on the site: fill in [First Name].")
    if touch > 1:
        warnings.append("Send as a reply in the same thread as your first email.")
    return f"Subject: {subject}\n\n{greet}\n\n{text}\n\nWill\nTQL | {phone}", warnings


def call_opener(row):
    """Phone first: a 10-second opener for the main line or the decision-maker."""
    c = context(row)
    hook = f"I saw \"{c['snippet']}\" on your site. " if c["snippet"] else ""
    ask = f"who handles your {c['freight']}" + (f" out of {c['city']}" if c["city"] else "") + "?"
    return f"\"Hi, this is Will with TQL. {hook}Quick one: {ask}\""


def check(subject, body, words=(50, 90)):
    w = []
    n = len(body.split())
    if not words[0] <= n <= words[1]:
        w.append(f"Body is {n} words (target {words[0]}-{words[1]}).")
    if body.count("?") != 1:
        w.append(f"Body has {body.count('?')} questions (want exactly 1).")
    if not 3 <= len(subject.split()) <= 5:
        w.append(f"Subject is {len(subject.split())} words (target 3-5).")
    low = body.lower()
    w += [f"Banned phrase: '{b}'" for b in BANNED if re.search(r"\b" + re.escape(b) + r"\b", low)]
    return w
