"""Filters run every cycle, so the queue keeps getting cleaner as you log outcomes."""
import datetime as dt

from .util import domain_matches

# Outcomes that permanently remove a prospect from the queue.
PERMANENT = {
    "do_not_contact": "asked not to be contacted",
    "existing_customer": "already a TQL customer",
    "owned_by_other_rep": "owned/worked by another TQL rep",
    "manager_declined": "manager said no",
    "out_of_business": "out of business",
    "bad_fit": "confirmed bad fit",
}
# Outcomes that mean the prospect is now in your active book (out of the cold queue).
ACTIVE = {"replied", "meeting", "quote_requested", "load_booked"}


def filter_reason(row, policy, suppression, today=None):
    """Return why this prospect should be held out of the queue, or None."""
    today = today or dt.date.today()
    dom = row["domain"]
    if dom in suppression:
        return f"suppression list: {suppression[dom]}"
    if domain_matches(dom, policy.get("blocked_domains", [])):
        return "blocked domain (not a company site)"
    last = row["last_outcome"]
    if last in PERMANENT:
        return PERMANENT[last]
    if last in ACTIVE:
        return f"active: {last}"
    if last == "not_interested" and row["last_outcome_at"]:
        days = int(policy.get("not_interested_cooldown_days") or 0)
        since = dt.date.fromisoformat(row["last_outcome_at"][:10])
        if (today - since).days < days:
            return f"said not interested — revisit after {since + dt.timedelta(days=days)}"
    if row["contacted_at"]:
        cooldown = int(policy.get("recontact_cooldown_days") or 0)
        try:
            contacted = dt.date.fromisoformat(row["contacted_at"][:10])
        except ValueError:
            contacted = today
        if (today - contacted).days < cooldown:
            return f"cooldown until {contacted + dt.timedelta(days=cooldown)}"
    return None
