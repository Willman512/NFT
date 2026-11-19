"""The customer plan: how much outreach gives ~95% odds of at least one new customer, and whether you're on pace.

No list can promise a shipper will say yes. What *can* be made near-certain is the math: if each fully worked
prospect (call + 3-touch email sequence) converts at rate p, then working n of them gives
P(at least one customer) = 1 - (1 - p)^n. The pipeline estimates p from YOUR logged results (Bayesian:
starts from a cautious benchmark, then trusts your data as it grows), sizes n, splits it into a daily quota,
and checks the queue holds enough GO/MAYBE leads to hit it.
"""
import datetime as dt
import math

WIN = {"load_booked"}                                                # a customer
DONE = {"load_booked", "quote_requested", "meeting", "replied", "no_response", "not_interested", "bad_fit"}


def beta_rate(wins, trials, prior_rate, prior_strength):
    """Posterior mean of a Beta prior centered on prior_rate, worth `prior_strength` prospects of evidence."""
    a = prior_rate * prior_strength + wins
    b = (1 - prior_rate) * prior_strength + (trials - wins)
    return a / (a + b)


def prospects_needed(p, target):
    if p <= 0:
        return math.inf
    return math.ceil(math.log(1 - target) / math.log(1 - p))


def prob_at_least_one(p, n):
    return 1 - (1 - p) ** n


def stats(conn):
    worked = conn.execute("SELECT COUNT(*) FROM prospects WHERE touches > 0 OR last_outcome IN (%s)"
                          % ",".join("?" * len(DONE)), tuple(DONE)).fetchone()[0]
    finished = conn.execute("SELECT COUNT(*) FROM prospects WHERE last_outcome IN (%s)" % ",".join("?" * len(DONE)),
                            tuple(DONE)).fetchone()[0]
    wins = conn.execute("SELECT COUNT(*) FROM prospects WHERE last_outcome='load_booked'").fetchone()[0]
    warm = conn.execute("SELECT COUNT(*) FROM prospects WHERE last_outcome IN "
                        "('replied','meeting','quote_requested')").fetchone()[0]
    supply = dict(conn.execute("SELECT verdict, COUNT(*) FROM prospects WHERE status='queued' AND touches=0 "
                               "GROUP BY verdict").fetchall())
    return {"worked": worked, "finished": finished, "wins": wins, "warm": warm,
            "supply_go": supply.get("GO", 0), "supply_maybe": supply.get("MAYBE", 0)}


def plan(conn, policy, today=None):
    cfg = policy.get("customer_plan", {})
    target = float(cfg.get("target_probability", 0.95))
    prior = float(cfg.get("benchmark_customer_rate", 0.02))
    strength = float(cfg.get("benchmark_weight_prospects", 50))
    days = int(cfg.get("working_days", 20))
    start = cfg.get("start_date") or (today or dt.date.today()).isoformat()
    today = today or dt.date.today()

    s = stats(conn)
    p = beta_rate(s["wins"], s["finished"], prior, strength)
    n = prospects_needed(p, target)
    elapsed = max(0, _workdays(dt.date.fromisoformat(start), today))
    remaining_days = max(1, days - elapsed)
    remaining = max(0, n - s["worked"])
    per_day = math.ceil(remaining / remaining_days) if remaining else 0
    supply = s["supply_go"] + s["supply_maybe"]
    return {
        **s, "p": p, "p_source": "your results" if s["finished"] >= strength else
        ("benchmark + your results" if s["finished"] else "a cautious benchmark until you log results"),
        "target": target, "needed": n, "remaining": remaining, "per_day": per_day, "days_left": remaining_days,
        "odds_now": prob_at_least_one(p, s["worked"]), "odds_if_plan": prob_at_least_one(p, max(n, s["worked"])),
        "supply": supply, "supply_short": max(0, remaining - supply), "start": start,
    }


def _workdays(a, b):
    d, n = a, 0
    while d < b:
        if d.weekday() < 5:
            n += 1
        d += dt.timedelta(days=1)
    return n


def render(pl):
    lines = [
        "## Customer plan",
        f"- Conversion estimate: **{pl['p']:.1%}** of fully worked prospects become customers ({pl['p_source']}).",
        f"- Work **{pl['needed']}** prospects (call + 3 emails each) for a **{pl['target']:.0%}** chance of at least one "
        f"new customer.",
        f"- Worked so far: {pl['worked']} · warm (replied/meeting/quote): {pl['warm']} · customers: {pl['wins']} · "
        f"odds so far: {pl['odds_now']:.0%}",
        f"- **Today's quota: {pl['per_day']} new prospects** ({pl['remaining']} to go over {pl['days_left']} working days).",
    ]
    if pl["supply_short"]:
        lines.append(f"- ⚠ Queue is {pl['supply_short']} leads short of the plan. Run `python prospect.py discover` "
                     "to pull more from EPA data.")
    if pl["wins"]:
        lines.append("- 🎉 You've booked a customer. The plan keeps running so the next one is on the way.")
    return "\n".join(lines)
