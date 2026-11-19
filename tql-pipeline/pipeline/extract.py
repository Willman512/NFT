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
               r"Plant|General)\s+(?:Manager|Director|Coordinator|Supervisor|Lead|Specialist)|President|Owner|CEO|COO|VP[^,\n]{0,30}|"
               r"(?:Vice President|Director) of [A-Z][a-z]+(?: [A-Z][a-z]+)?")
CONTACT_RE = re.compile(r"\b([A-Z][a-z]+(?: [A-Z]\.)? [A-Z][a-zA-Z'-]+)[ \t]*(?:,|-|–|—|\||\n)\s*(" + TITLE_WORDS + r")")
GENERIC_INBOX = re.compile(r"^(info|sales|contact|hello|office|admin|support|orders|customerservice|cs|service|inquiries|web|marketing)@", re.I)
STATE_NAMES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "Ontario": "ON", "Quebec": "QC", "Québec": "QC", "British Columbia": "BC", "Alberta": "AB", "Manitoba": "MB"}
CITY_STATE_RE = re.compile(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+){0,2}),\s*(" + "|".join(STATE_NAMES) + r")\b(?:\s+(\d{5}))?")
TEL_RE = re.compile(r'href=["\']tel:([^"\']+)["\']', re.I)
JSONLD_PHONE = re.compile(r'"telephone"\s*:\s*"([^"]+)"')
JSONLD_ADDR = re.compile(r'"addressLocality"\s*:\s*"([^"]+)"\s*,\s*"addressRegion"\s*:\s*"([A-Za-z ]+)"')
FOREIGN_RE = re.compile(r"(\+(?:[2-9]\d)[\s\d().-]{6,})|\b(GmbH|S\.A\. de C\.V|Pvt\.? Ltd|Co\., Ltd|S\.p\.A|B\.V\.|"
                        r"Sdn Bhd|PLC|Pty Ltd)\b|\b(United Kingdom|Germany|China|India|Australia|Italy|Spain|Netherlands)\b")
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


STREET_WORDS = {"drive", "dr", "street", "st", "road", "rd", "avenue", "ave", "highway", "hwy", "blvd", "boulevard", "lane",
                "ln", "way", "parkway", "pkwy", "court", "ct", "suite", "ste", "pike", "route", "circle", "plaza", "box"}
NOT_A_PERSON = {"industries", "inc", "group", "company", "corp", "corporation", "llc", "steel", "concrete", "lumber",
                "products", "materials", "supply", "foods", "farms", "iv", "iii", "ii", "jr", "sr", "team", "office"}


def _clean_city(c):
    """'100 Steel View Drive Roanoke' -> 'Roanoke'."""
    words = c.strip().split()
    cut = max((i for i, w in enumerate(words) if w.lower().strip(".") in STREET_WORDS), default=-1)
    words = words[cut + 1:][-3:]
    return " ".join(words)


def _is_person(name):
    return not any(w.lower().strip(".") in NOT_A_PERSON for w in name.split())


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


MILESTONE_RE = re.compile(
    r"\b(?:(?:family|employee)[- ](?:owned|operated|run)(?: and (?:operated|owned))?(?:,? (?:business |company )?since (?:18|19|20)\d\d)?"
    r"|(?:second|third|fourth|fifth|2nd|3rd|4th|5th)[- ]generation(?: family[- ]owned)?"
    r"|(?:over|more than|nearly) \d{2,3} years(?: of (?:experience|service|excellence|quality|history|tradition|craftsmanship))?\b"
    r"|since (?:18|19|20)\d\d|founded in (?:18|19|20)\d\d)", re.I)


def _distinctive_detail(parsed_pages, text):
    """A short, quotable milestone or slogan a human would notice (for the email/call opener).
    Never cut mid-sentence: either a whole short phrase or nothing."""
    milestones = [m.group(0).strip() for m in MILESTONE_RE.finditer(text)]
    if milestones:
        def rank(m):
            low = m.lower()
            return (bool(re.search(r"\d", m)) + ("family" in low or "generation" in low), len(m))
        best = max(milestones, key=rank).lower()
        years = sorted(int(y) for y in re.findall(r"(?:since|founded in) ((?:18|19|20)\d\d)", text, re.I))
        if best.startswith(("family", "employee")) and not re.search(r"\d", best) and years:
            best += f" since {years[0]}"  # "family owned" + "since 1907" elsewhere on the site
        return best
    candidates = []
    for pg in parsed_pages:
        candidates += pg["h1"] + [pg["meta_desc"]]
    for c in candidates:
        c = (c or "").strip(" .")
        if 2 <= len(c.split()) <= 8 and not re.search(r"[/|]", c) and SLOGAN_HINT.search(c) \
                and not c.lower().startswith(("home", "welcome", "about", "contact", "history")):
            return c
    return ""


def extract(pages, signals, learned_terms=()):
    """pages: [{kind,url,html}] -> (research dict, features dict, full text)."""
    parsed = [dict(parse_page(p["html"]), kind=p["kind"], url=p["url"]) for p in pages]
    text = "\n".join(pg["text"] for pg in parsed)
    raw = "\n".join(p["html"] or "" for p in pages)

    # Order of appearance is kept (first address is usually HQ / main plant).
    phone_src = text + "\n" + "\n".join(TEL_RE.findall(raw) + JSONLD_PHONE.findall(raw))
    phones = _uniq(f"({m.group(1)}) {m.group(2)}-{m.group(3)}" for m in PHONE_RE.finditer(phone_src)
                   if "fax" not in phone_src[max(0, m.start() - 12):m.start()].lower())
    site = pages[0]["url"].split("//")[-1].split("/")[0].lower().removeprefix("www.") if pages else ""
    emails = _uniq(e.lower() for e in EMAIL_RE.findall(raw)
                   if not e.lower().endswith((".png", ".jpg", ".gif", ".webp", ".svg")) and "sentry" not in e.lower()
                   and (e.lower().split("@")[1].endswith(site) or e.lower().split("@")[1] in FREEMAIL))
    us_addr = _uniq(f"{_clean_city(c)}, {s} {z}" for c, s, z in US_ADDR_RE.findall(text) if _clean_city(c))
    for c, s, z in CITY_STATE_RE.findall(text):
        code = STATE_NAMES[s]
        if c not in STATE_NAMES and not any(a.startswith(f"{c}, ") for a in us_addr):
            us_addr.append(f"{c}, {code}" + (f" {z}" if z else ""))
    for c, s in JSONLD_ADDR.findall(raw):
        code = STATE_NAMES.get(s.strip(), s.strip().upper())
        if code in US_STATES and not any(a.startswith(f"{c}, ") for a in us_addr):
            us_addr.append(f"{c}, {code}")
    ca_addr = _uniq(f"{_clean_city(c)}, {s} {z}" for c, s, z in CA_ADDR_RE.findall(text) if _clean_city(c))
    mx = _uniq(MX_RE.findall(text))
    contacts = _uniq(f"{n} — {t.strip()}" for n, t in CONTACT_RE.findall(text) if _is_person(n))
    years = [int(y) for y in COPYRIGHT_RE.findall(raw)]
    copyright_year = max(years) if years else None
    home = parsed[0] if parsed else {"title": "", "lang": ""}
    is_french = home.get("lang", "").startswith("fr") or len(FRENCH_WORDS.findall(text)) >= 8

    features, hits = pattern_features(text, signals, learned_terms)

    stale = bool(copyright_year and copyright_year < today().year - 2)
    features["stale_site"] = 1 if stale else 0
    thin = len(text) < 80 or (len(text) < 400 and re.search(r"enable javascript|javascript is (required|disabled)", text, re.I))
    no_na = not (phones or us_addr or ca_addr or mx)
    features["thin_site"] = 1 if thin else 0
    # Overseas only if we actually see foreign markers; missing info alone just means "look it up".
    features["no_na_presence"] = 1 if no_na and not thin and FOREIGN_RE.search(text) else 0
    features["contact_info_missing"] = 1 if no_na and not features["no_na_presence"] else 0
    features["generic_inbox_only"] = 1 if emails and all(GENERIC_INBOX.match(e) for e in emails) else 0
    features["named_contact"] = 1 if contacts else 0
    features["french_site"] = 1 if is_french else 0
    if len(us_addr) + len(ca_addr) >= 2:
        features["multi_location"] = 1
    features["many_locations"] = 1 if len(us_addr) + len(ca_addr) >= 5 else 0

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


FREEMAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com", "icloud.com", "comcast.net", "att.net"}

STOPWORDS = set("""the and for are with our you your that this from have has was were will can all any not but more
about into they them their its out who what when where how also than then there here been being each other such only
over under upon some most very just may might would could should must per via inc llc ltd com www http https
home contact menu page click read learn privacy policy terms copyright rights reserved cookie cookies site""".split())
