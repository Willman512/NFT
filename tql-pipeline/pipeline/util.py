"""Small shared helpers."""
import datetime as dt
from urllib.parse import parse_qs, urlparse


def now_iso():
    return dt.datetime.now().replace(microsecond=0).isoformat()


def today():
    return dt.date.today()


def unwrap_redirect(url):
    """Pull the real site out of Google redirect links (…/url?q=… or url=…)."""
    url = (url or "").strip()
    p = urlparse(url)
    if "google." in p.netloc and p.path.startswith("/url"):
        qs = parse_qs(p.query)
        for key in ("url", "q"):
            if qs.get(key):
                return qs[key][0]
    return url


def normalize_url(url):
    url = unwrap_redirect(url)
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    return url


def normalize_domain(url_or_domain):
    s = (url_or_domain or "").strip().lower()
    if not s:
        return ""
    if "://" not in s:
        s = "http://" + s
    host = urlparse(unwrap_redirect(s)).netloc.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def domain_matches(domain, blocked):
    return any(domain == b or domain.endswith("." + b) for b in blocked)
