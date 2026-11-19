"""Intro email drafts that follow the tql-prospect-research rules. Draft only — you edit and send."""
import re

from .db import jload
from .report import FREIGHT_ROLES

# signal -> (subject template, pain guess, one TQL capability line). First match wins.
ANGLES = [
    ("flatbed_oversize", "flatbeds out of {city}", "finding flatbeds that show up on time is a weekly fight",
     "We cover flatbed, step deck, and heavy haul with 110,000+ vetted carriers."),
    ("reefer", "reefer loads out of {city}", "keeping reefer trucks on time matters a lot when loads can't sit",
     "We run reefer every day, with a team up 24/7 if a truck runs late."),
    ("cross_border", "loads into mexico and canada", "moving freight across the border takes more calls than it should",
     "Our Monterrey team handles Mexico loads, and we cross into Canada daily."),
    ("retail_dealers", "getting trucks to {customers}", "hitting tight delivery windows for your {customers} keeps you busy",
     "We move truckload and LTL into retail DCs, with live tracking in TQL TRAX."),
    ("hazmat", "hazmat loads out of {city}", "finding good hazmat carriers is not easy",
     "We have vetted hazmat carriers and a claims team if anything goes wrong."),
    ("distributor", "extra trucks out of {city}", "your usual carriers get tight when orders spike",
     "We have 110,000+ vetted carriers, so we can add trucks fast."),
    ("manufacturer", "outbound trucks from {city}", "your usual carriers get tight when orders pile up",
     "We have 110,000+ vetted carriers, so we can add trucks fast."),
]
DEFAULT = (None, "pallets out of {city}", "getting pallets out the door on time takes more chasing than it should",
           "We cover truckload, partials, and LTL with 45+ LTL carriers.")
BANNED = ["dedicated contact", "right in our wheelhouse", "hope this finds you well", "touching base",
          "circle back", "synergy", "you get me", "roi"]


def _snippet(detail, max_words=6):
    words = re.sub(r"[\"“”]", "", detail).split()
    return " ".join(words[:max_words]).rstrip(".,;:!-–—")


def _first_name(contacts):
    for c in contacts:
        name, _, title = c.partition(" — ")
        if any(k in title.lower() for k in FREIGHT_ROLES):
            return name.split()[0]
    return None


def draft(row, phone="[Phone]"):
    r = jload(row["research"], {})
    f = jload(row["features"], {})
    angle = next((a for a in ANGLES if f.get(a[0])), DEFAULT)
    _, subj, pain, capability = angle
    locs = r.get("us_locations", []) + r.get("ca_locations", [])
    city = locs[0].split(",")[0].strip().lower() if locs else "your plant"
    customers = "dealers" if "dealer" in " ".join(r.get("signal_hits", {}).get("retail_dealers", [])).lower() else "retailers"
    subject = subj.format(city=city, customers=customers).lower()
    pain = pain.format(customers=customers)

    first = _first_name(r.get("contacts", [])) or "[First Name]"
    greet = f"Bonjour {first}," if f.get("french_site") else f"Hi {first},"
    opener = (f"Saw \"{_snippet(r['detail'])}\" on your site and wanted to reach out."
              if r.get("detail") else f"I was reading about {row['name'] or 'your company'} and wanted to reach out.")
    body = [
        opener,
        f"I'm guessing {pain}.",
        f"I'm Will, a broker with TQL. {capability}",
        "Would it help to have another option when your usual trucks fall through?",
    ]
    text = "\n\n".join(body)
    warnings = check(subject, text)
    if first == "[First Name]":
        warnings.append("No freight contact found on the site: fill in [First Name].")
    email = f"Subject: {subject}\n\n{greet}\n\n{text}\n\nWill\nTQL | {phone}"
    return email, warnings


def check(subject, body):
    w = []
    n = len(body.split())
    if not 50 <= n <= 90:
        w.append(f"Body is {n} words (target 50-90).")
    if body.count("?") != 1:
        w.append(f"Body has {body.count('?')} questions (want exactly 1).")
    if not 3 <= len(subject.split()) <= 5:
        w.append(f"Subject is {len(subject.split())} words (target 3-5).")
    low = body.lower()
    w += [f"Banned phrase: '{b}'" for b in BANNED if re.search(r"\b" + re.escape(b) + r"\b", low)]
    return w
