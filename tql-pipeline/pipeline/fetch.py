"""Polite site crawler: home page + the 'about / products / contact / shipping' pages from its nav.

Respects robots.txt and a per-request delay from config/policy.json. Never touches blocked domains.
"""
import re
import time
import urllib.error
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .util import domain_matches, normalize_domain

USER_AGENT = "Mozilla/5.0 (compatible; ProspectResearch/1.0; manual sales research)"

# Which nav links are worth a visit, in priority order.
PAGE_TYPES = [
    ("about", r"about|our-story|story|history|who-we-are|company"),
    ("products", r"product|services|solutions|capabilit|what-we-do|catalog"),
    ("contact", r"contact|location|facilit|plants|find-us|directions"),
    ("shipping", r"shipping|delivery|freight|logistics|distribution|dealer|retailer|where-to-buy|store-locator"),
    ("careers", r"career|jobs|employment|join"),
    ("team", r"team|leadership|staff|management|people"),
]


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []  # (href, text)
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None


def find_nav_links(html, base_url, limit):
    """Pick same-site links that look like about/products/contact/shipping pages."""
    parser = _LinkParser()
    try:
        parser.feed(html)
    except Exception:  # malformed HTML: use what we got
        pass
    base_dom = normalize_domain(base_url)
    chosen, seen = [], {base_url.rstrip("/")}
    english_toggle = None
    for kind, pattern in PAGE_TYPES:
        for href, text in parser.links:
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            full = urljoin(base_url, href).split("#")[0]
            if normalize_domain(full) != base_dom or full.rstrip("/") in seen:
                continue
            if re.search(r"\.(pdf|jpg|jpeg|png|gif|zip|docx?)$", full, re.I):
                continue
            if english_toggle is None and text.strip().upper() in ("EN", "ENGLISH"):
                english_toggle = full
            haystack = (urlparse(full).path + " " + text).lower()
            if re.search(pattern, haystack):
                chosen.append((kind, full))
                seen.add(full.rstrip("/"))
                break  # one page per type, keeps the crawl small
    if english_toggle and english_toggle.rstrip("/") not in seen:
        chosen.insert(0, ("english", english_toggle))
    return chosen[:limit]


def links_from_sitemap(xml, base_url, have, limit):
    """Pick about/products/contact/shipping URLs out of a sitemap.xml."""
    base_dom = normalize_domain(base_url)
    locs = [u.strip() for u in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml or "", re.I)]
    have = {k for k, _ in have}
    chosen = []
    for kind, pattern in PAGE_TYPES:
        if kind in have:
            continue
        for u in sorted(locs, key=len):  # shortest URL = top-level page, not a blog post
            if normalize_domain(u) == base_dom and re.search(pattern, urlparse(u).path.lower()) \
                    and not u.lower().endswith(".xml"):
                chosen.append((kind, u))
                break
    return chosen[:limit]


class Fetcher:
    """HTTP fetcher. Swap `get` in tests with a dict-backed function."""

    def __init__(self, policy, get=None):
        self.policy = policy
        self.delay = float(policy.get("min_seconds_between_requests", 1.5))
        self.timeout = float(policy.get("request_timeout_seconds", 15))
        self._last = 0.0
        self._robots = {}
        self._live = get is None
        self._get = get or self._http_get

    def _http_get(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if not any(t in ctype for t in ("html", "text", "xml")):
                return resp.geturl(), ""
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.geturl(), resp.read(2_000_000).decode(charset, errors="replace")

    def allowed(self, url):
        dom = normalize_domain(url)
        if domain_matches(dom, self.policy.get("blocked_domains", [])):
            return False
        if not self.policy.get("respect_robots_txt", True) or not self._live:
            return True
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self._robots:
            self._robots[root] = self._load_robots(root)
        rp = self._robots[root]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def _load_robots(self, root):
        """RFC 9309: 4xx = no rules (allowed); 5xx/unreachable = disallow for now (retry later).
        Python's stock reader asks with a 'Python-urllib' agent that firewalls 403, then wrongly blocks everything."""
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(root + "/robots.txt", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                rp.parse(resp.read(500_000).decode("utf-8", errors="replace").splitlines())
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                return None
            rp.disallow_all = True
            return rp
        except (urllib.error.URLError, OSError, ValueError):
            return None  # main request will surface the real network error
        delay = rp.crawl_delay(USER_AGENT)
        if delay:
            self.delay = max(self.delay, min(float(delay), 30.0))
        return rp

    def get(self, url):
        if not self.allowed(url):
            raise PermissionError(f"blocked by policy/robots.txt: {url}")
        wait = self.delay - (time.time() - self._last)
        if wait > 0 and self._live:
            time.sleep(wait)
        self._last = time.time()
        return self._get(url)

    def crawl(self, url):
        """Return list of {kind, url, html}. Home page first."""
        final_url, html = self.get(url)
        pages = [{"kind": "home", "url": final_url, "html": html}]
        limit = int(self.policy.get("max_pages_per_site", 6)) - 1
        links = find_nav_links(html, final_url, limit)
        if len(links) < 3:  # menu probably built by JavaScript: fall back to the sitemap
            try:
                p = urlparse(final_url)
                _, xml = self.get(f"{p.scheme}://{p.netloc}/sitemap.xml")
                if "<sitemapindex" in xml:  # one level down: first page-sitemap
                    subs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml)
                    sub = next((u for u in subs if "page" in u.lower()), subs[0] if subs else None)
                    xml = self.get(sub)[1] if sub else ""
                links += links_from_sitemap(xml, final_url, links, limit - len(links))
            except (urllib.error.URLError, PermissionError, TimeoutError, OSError, ValueError):
                pass
        for kind, link in links:
            try:
                u, h = self.get(link)
                pages.append({"kind": kind, "url": u, "html": h})
            except (urllib.error.URLError, PermissionError, TimeoutError, OSError, ValueError):
                continue
        return pages
