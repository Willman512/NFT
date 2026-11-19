"""Turn crawled pages into research facts + binary features for scoring."""
import re
from collections import Counter
from html.parser import HTMLParser

from .util import today

US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
             "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
CA_PROVINCES = "AB BC MB NB NL NS NT NU ON PE QC SK YT".split()

PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?([2-9]\d{2})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
US_ADDR_RE = re.compile(r"([A-Z][A-Za-z.' -]{2,30}),\s*(" + "|".join(US_STATES) + r")\.?\s+(\d{5})(?:-\d{4})?\b")
CA_ADDR_RE = re.compile(r"([A-Z][A-Za-z.' -]{2,30}),?\s*(" + "|".join(CA_PROVINCES) + r")\.?\s+([A-Z]\d[A-Z]\s?\d[A-Z]\d)\b")
MX_RE = re.compile(r"\b(M[eé]xico|Monterrey|Guadalajara|Nuevo Le[oó]n|Jalisco|Coahuila|C\.P\.\s*\d{5})\b")
COPYRIGHT_RE = re.compile(r"(?:©|&copy;|\(c\)|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", re.I)
TITLE_WORDS = (r"(?:Logistics|Shipping|Traffic|Transportation|Supply Chain|Operations|Warehouse|Distribution|Purchasing|"
               r"Plant|General)\s+(?:Manager|Director|Coordinator|Supervisor|Lead|Specialist)|President|Owner|CEO|COO|VP[^,\n]{0,30}")
CONTACT_RE = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z]\.)?\s[A-Z][a-zA-Z'-]+)\s*(?:,|-|–|—|\||\n)\s*(" + TITLE_WORDS + r")")
GENERIC_INBOX = re.compile(r"^(info|sales|contact|hello|office|admin|support|orders|customerservice|cs|service|inquiries|web|marketing)@", re.I)
FRENCH_WORDS = re.compile(r"\b(nous|notre|nos|produits|entreprise|livraison|accueil|qualité|depuis)\b", re.I)
SLOGAN_HINT = re.compile(r"\b(since|family|promise|quality|built|proud|years|tradition|trusted|mission|motto)\b", re.I)


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title, self.h1, self.meta_desc, self.lang = [], "", [], "", ""
        self._skip = 0
        self._in = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        elif tag == "html":
            self.lang = (a.get("lang") or "").lower()
        elif tag == "meta" and (a.get("name") or a.get("property") or "").lower() in ("description", "og:description"):
            self.meta_desc = self.meta_desc or (a.get("content") or "").strip()
        elif tag in ("title", "h1"):
            self._in = tag
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "footer", "address"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        if tag == self._in:
            self._in = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._in == "title":
            self.title += data
        elif self._in == "h1":
            self.h1.append(data.strip())
        self.parts.append(data)


def _uniq(items):
    return list(dict.fromkeys(items))


def parse_page(html):
    p = _TextParser()
    try:
        p.feed(html or "")
    except Exception:
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return {"text": text, "title": p.title.strip(), "h1": [h for h in p.h1 if h],
            "meta_desc": p.meta_desc, "lang": p.lang}


def _distinctive_detail(parsed_pages, text):
    """A slogan / promise / milestone a human would notice (for the email opener)."""
    candidates = []
    for pg in parsed_pages:
        candidates += pg["h1"] + [pg["title"]]
        if pg["meta_desc"]:
            candidates.append(pg["meta_desc"])
    candidates += [ln.strip() for ln in text.split("\n") if 15 <= len(ln.strip()) <= 120]
    segments = [seg.strip(" .") for c in candidates for seg in re.split(r"\s[|\-–—:]\s|[.!]\s", c or "")]
    segments = [s for s in segments if 2 <= len(s.split()) <= 12
                and not s.lower().startswith(("home", "welcome to", "menu", "skip to", "about", "contact", "©"))]
    for s in segments:  # first choice: sounds like a slogan, promise, or milestone
        if SLOGAN_HINT.search(s):
            return s
    return segments[0] if segments else ""


def extract(pages, signals, learned_terms=()):
    """pages: [{kind,url,html}] -> (research dict, features dict, full text)."""
    parsed = [dict(parse_page(p["html"]), kind=p["kind"], url=p["url"]) for p in pages]
    text = "\n".join(pg["text"] for pg in parsed)
    raw = "\n".join(p["html"] or "" for p in pages)

    # Order of appearance is kept (first address is usually HQ / main plant).
    phones = _uniq(f"({a}) {b}-{c}" for a, b, c in PHONE_RE.findall(text))
    emails = _uniq(e.lower() for e in EMAIL_RE.findall(raw)
                   if not e.lower().endswith((".png", ".jpg", ".gif", ".webp", ".svg")) and "sentry" not in e.lower())
    us_addr = _uniq(f"{c.strip()}, {s} {z}" for c, s, z in US_ADDR_RE.findall(text))
    ca_addr = _uniq(f"{c.strip()}, {s} {z}" for c, s, z in CA_ADDR_RE.findall(text))
    mx = _uniq(MX_RE.findall(text))
    contacts = _uniq(f"{n} — {t.strip()}" for n, t in CONTACT_RE.findall(text))
    years = [int(y) for y in COPYRIGHT_RE.findall(raw)]
    copyright_year = max(years) if years else None
    home = parsed[0] if parsed else {"title": "", "lang": ""}
    is_french = home.get("lang", "").startswith("fr") or len(FRENCH_WORDS.findall(text)) >= 8

    features, hits = pattern_features(text, signals, learned_terms)

    stale = bool(copyright_year and copyright_year < today().year - 2)
    features["stale_site"] = 1 if stale else 0
    features["no_na_presence"] = 1 if not (phones or us_addr or ca_addr or mx) else 0
    features["generic_inbox_only"] = 1 if emails and all(GENERIC_INBOX.match(e) for e in emails) else 0
    features["named_contact"] = 1 if contacts else 0
    features["french_site"] = 1 if is_french else 0
    if len(us_addr) + len(ca_addr) >= 2:
        features["multi_location"] = 1

    research = {
        "name": re.split(r"\s[|\-–—]\s", home.get("title", ""))[0].strip(),
        "pages": [{"kind": pg["kind"], "url": pg["url"]} for pg in parsed],
        "phones": phones[:6], "emails": emails[:8],
        "us_locations": us_addr[:10], "ca_locations": ca_addr[:10], "mx_mentions": mx[:5],
        "contacts": contacts[:8], "copyright_year": copyright_year,
        "detail": _distinctive_detail(parsed, text),
        "signal_hits": hits, "french": is_french,
    }
    return research, features, text


def pattern_features(text, signals, learned_terms=()):
    """Regex signals + learned terms. Works on stored text, so rescoring needs no re-crawl."""
    features, hits = {}, {}
    for sid, sig in signals.items():
        if sig.get("computed"):
            continue
        found = []
        for pat in sig.get("patterns", []):
            m = re.search(pat, text, re.I)
            if m:
                found.append(m.group(0))
        features[sid] = 1 if found else 0
        if found:
            hits[sid] = found[:3]
    low = text.lower()
    for term in learned_terms:
        features[f"term:{term}"] = 1 if re.search(r"\b" + re.escape(term) + r"\b", low) else 0
    return features, hits


def tokenize(text):
    """Lowercase word + bigram counts (presence only) for term mining."""
    words = re.findall(r"[a-z][a-z'-]{2,}", (text or "").lower())
    words = [w for w in words if w not in STOPWORDS]
    grams = set(words) | {f"{a} {b}" for a, b in zip(words, words[1:])}
    return Counter(grams)


STOPWORDS = set("""the and for are with our you your that this from have has was were will can all any not but more
about into they them their its out who what when where how also than then there here been being each other such only
over under upon some most very just may might would could should must per via inc llc ltd com www http https
home contact menu page click read learn privacy policy terms copyright rights reserved cookie cookies site""".split())
