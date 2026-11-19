"""The daily plan: follow-ups due, new GO prospects with a phone opener, MAYBEs to check."""
import datetime as dt

from .config import OUTPUT_DIR
from .db import jload
from .email import call_opener, draft
from .report import ranked_contacts


def _days_since(iso, today):
    try:
        return (today - dt.date.fromisoformat(iso[:10])).days
    except (TypeError, ValueError):
        return 0


def followups(conn, policy, today=None):
    """[(row, next_touch, days_since)] due now, plus finished sequences that need an outcome logged."""
    today = today or dt.date.today()
    gaps = policy.get("followup_days", [3, 7])
    max_touches = int(policy.get("max_touches", len(gaps) + 1))
    due, finished = [], []
    rows = conn.execute("SELECT * FROM prospects WHERE touches > 0 AND (last_outcome IS NULL) "
                        "AND status NOT IN ('suppressed','won') ORDER BY contacted_at").fetchall()
    for r in rows:
        days = _days_since(r["contacted_at"], today)
        n = r["touches"]
        if n >= max_touches:
            if days >= (gaps[-1] if gaps else 7):
                finished.append((r, days))
        elif days >= gaps[min(n - 1, len(gaps) - 1)]:
            due.append((r, n + 1, days))
    return due, finished


def next_touch(row, policy):
    if not row["touches"] or row["last_outcome"]:
        return 1
    max_touches = int(policy.get("max_touches", 3))
    return min(row["touches"] + 1, max_touches)


def plan(conn, policy, top=15, drafts=False, phone="[Phone]", today=None):
    today = today or dt.date.today()
    due, finished = followups(conn, policy, today)
    cap = policy.get("max_new_contacts_per_day")
    n_new = min(top, cap) if cap else top
    go = conn.execute("SELECT * FROM prospects WHERE status='queued' AND verdict='GO' AND touches=0 "
                      "ORDER BY score DESC LIMIT ?", (n_new,)).fetchall()
    maybe = conn.execute("SELECT * FROM prospects WHERE status='queued' AND verdict='MAYBE' AND touches=0 "
                         "ORDER BY score DESC LIMIT ?", (max(5, top // 3),)).fetchall()

    out = [f"# Today — {today.isoformat()}", "",
           f"{len(due)} follow-ups due · {len(go)} new GO · {len(maybe)} MAYBE to check · "
           f"{len(finished)} finished sequences", ""]

    out.append(f"## 1. Follow-ups due ({len(due)})")
    if not due:
        out.append("None due.")
    for r, touch, days in due:
        res = jload(r["research"], {})
        out.append(f"- **{r['name'] or r['domain']}** ({r['domain']}): touch {touch}, last touch {days} days ago"
                   + (f" · {res['phones'][0]}" if res.get("phones") else ""))
        if drafts:
            text, _ = draft(r, phone, touch)
            out += ["", "```", text, "```", ""]
    out.append("")

    out.append(f"## 2. New GO prospects: call first, then email ({len(go)})")
    if not go:
        out.append("Queue has no new GOs. Add leads (`add`) or run `suggest` for search ideas.")
    for i, r in enumerate(go, 1):
        res = jload(r["research"], {})
        reasons = jload(r["reasons"], [])
        why = "; ".join(x[2:] for x in reasons if x.startswith("+"))[:160]
        out.append(f"### {i}. {r['name'] or r['domain']} — {r['domain']} (fit {r['score']:.2f})")
        out.append(f"- Why: {why}")
        best = ranked_contacts(res.get("contacts", []))
        out.append(f"- Call: {', '.join(res.get('phones', [])[:2]) or 'no phone on site (check Google Maps)'}"
                   + (f" · ask for {best[0]}" if best else " · ask who handles transportation"))
        out.append(f"- Opener: {call_opener(r)}")
        flags = [x for x in reasons if not x.startswith(("+", "-"))]
        if any("RED FLAG" in x or "stale" in x for x in flags):
            out.append("- ⚑ " + " ".join(x for x in flags if "RED FLAG" in x or "stale" in x))
        if drafts:
            text, warns = draft(r, phone, 1)
            out += ["", "```", text, "```"] + [f"> check: {w}" for w in warns] + [""]
    out.append("")

    out.append(f"## 3. MAYBE: check before contacting ({len(maybe)})")
    for r in maybe:
        flags = [x for x in jload(r["reasons"], []) if "RED FLAG" in x or "stale" in x or "Confirm" in x]
        out.append(f"- {r['name'] or r['domain']} ({r['domain']}, fit {r['score']:.2f})"
                   + (f": {flags[0]}" if flags else ""))
    out.append("")

    if finished:
        out.append(f"## 4. Sequences finished with no reply ({len(finished)}): log them")
        out += [f"- `python prospect.py outcome {r['domain']} no_response`" for r, _ in finished]
        out.append("")
    out.append("After each call or email: `python prospect.py outcome <domain> contacted` "
               "(or replied / quote_requested / load_booked / not_interested …)")
    return "\n".join(out)


def write_plan(text, today=None):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"today-{(today or dt.date.today()).isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    return path
