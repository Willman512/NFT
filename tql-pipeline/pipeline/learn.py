"""The 'ever improving' part: retrain weights from your logged outcomes and mine new signal terms.

Labels come from the last outcome you logged for each prospect:
  load_booked (3x), meeting / quote_requested (1.5x), replied (1x)   -> positive
  existing_customer / owned_by_other_rep (0.5x)                      -> positive (proof the profile fits TQL)
  bad_fit (1.5x), not_interested (1x), no_response (0.5x)            -> negative
Other outcomes (do_not_contact, manager_declined, out_of_business) are filtered but not learned from.
"""
import json
import math

from .db import active_terms, jload
from .extract import tokenize
from .score import current_model, probability
from .util import now_iso

LABELS = {
    "load_booked": (1, 3.0), "meeting": (1, 1.5), "quote_requested": (1, 1.5), "replied": (1, 1.0),
    "existing_customer": (1, 0.5), "owned_by_other_rep": (1, 0.5), "other_rep_customer": (1, 1.0),
    "bad_fit": (0, 1.5), "not_interested": (0, 1.0), "no_response": (0, 0.5),
}
MIN_LABELS = 6          # below this, keep the starting weights
MIN_PER_CLASS = 2
CALIBRATE_AT = 20       # labels needed before thresholds start moving


def labeled_rows(conn):
    rows = conn.execute("SELECT * FROM prospects WHERE last_outcome IS NOT NULL AND features IS NOT NULL").fetchall()
    return [(r, *LABELS[r["last_outcome"]]) for r in rows if r["last_outcome"] in LABELS]


def train(samples, prior, bias0, l2=0.15, lr=0.2, epochs=400):
    """Weighted logistic regression, L2-pulled toward the prior so small data can't wreck it."""
    feats = sorted({f for x, _, _ in samples for f in x} | set(prior))
    w = {f: prior.get(f, 0.0) for f in feats}
    b = bias0
    total = sum(sw for _, _, sw in samples)
    for _ in range(epochs):
        gw = {f: 0.0 for f in feats}
        gb = 0.0
        for x, y, sw in samples:
            z = b + sum(w[f] * v for f, v in x.items())
            err = (1.0 / (1.0 + math.exp(-max(min(z, 30), -30))) - y) * sw
            gb += err
            for f, v in x.items():
                if v:
                    gw[f] += err * v
        for f in feats:
            w[f] -= lr * (gw[f] / total + l2 * (w[f] - prior.get(f, 0.0)))
        b -= lr * (gb / total)
    return b, w


def calibrate(probs_pos, old_go, old_skip):
    """Raise the bars only as far as the evidence allows: keep ~80% of winners at GO, ~95% above SKIP."""
    s = sorted(probs_pos)
    go = max(0.5, min(0.85, s[int(0.2 * (len(s) - 1))]))
    skip = max(0.15, min(go - 0.1, s[int(0.05 * (len(s) - 1))]))
    return round(go, 3), round(skip, 3)


def mine_terms(samples_text, known_terms, top=12):
    """Words/phrases that show up on winners' sites far more than losers'."""
    pos_docs = [tokenize(t) for t, y in samples_text if y == 1]
    neg_docs = [tokenize(t) for t, y in samples_text if y == 0]
    if len(pos_docs) < MIN_PER_CLASS or len(neg_docs) < MIN_PER_CLASS:
        return []
    pos_df, neg_df = {}, {}
    for d in pos_docs:
        for t in d:
            pos_df[t] = pos_df.get(t, 0) + 1
    for d in neg_docs:
        for t in d:
            neg_df[t] = neg_df.get(t, 0) + 1
    min_df = max(2, int(0.25 * len(pos_docs)))
    out = []
    for t, c in pos_df.items():
        if c < min_df or t in known_terms:
            continue
        lo = math.log((c + 0.5) / (len(pos_docs) + 1)) - math.log((neg_df.get(t, 0) + 0.5) / (len(neg_docs) + 1))
        if lo > 1.0:
            out.append((round(lo, 2), t, c, neg_df.get(t, 0)))
    out.sort(reverse=True)
    return out[:top]


def learn(conn, icp, adopt_terms=0, note=""):
    """Train a new model version. Returns a report dict."""
    data = labeled_rows(conn)
    n_pos = sum(1 for _, y, _ in data if y == 1)
    n_neg = len(data) - n_pos
    report = {"n_pos": n_pos, "n_neg": n_neg, "trained": False, "changes": [], "terms": [], "adopted": []}

    terms = set(active_terms(conn))
    report["terms"] = mine_terms([(r["page_text"] or "", y) for r, y, _ in data], terms)
    if adopt_terms and report["terms"]:
        for lo, t, _, _ in report["terms"][:adopt_terms]:
            conn.execute("INSERT OR REPLACE INTO learned_terms(term, added_at, log_odds, active) VALUES (?,?,?,1)",
                         (t, now_iso(), lo))
            report["adopted"].append(t)
        conn.commit()
        # New terms need features on every stored prospect before training.
        from .pipeline import rescore_all
        rescore_all(conn, icp, quiet=True)
        data = labeled_rows(conn)

    if len(data) < MIN_LABELS or n_pos < MIN_PER_CLASS or n_neg < MIN_PER_CLASS:
        report["reason"] = (f"Need at least {MIN_LABELS} labeled outcomes with {MIN_PER_CLASS}+ wins and "
                            f"{MIN_PER_CLASS}+ losses (have {n_pos} wins / {n_neg} losses). Keep logging outcomes.")
        return report

    prior = {sid: float(s["weight"]) for sid, s in icp["signals"].items()}
    old = current_model(conn, icp)
    samples = [(jload(r["features"], {}), y, sw) for r, y, sw in data]
    bias, weights = train(samples, prior, float(icp["bias"]))

    model = {"bias": bias, "weights": weights, "go": old["go"], "skip": old["skip"]}
    if len(data) >= CALIBRATE_AT:
        pos_probs = [probability(x, model) for x, y, _ in samples if y == 1]
        model["go"], model["skip"] = calibrate(pos_probs, old["go"], old["skip"])

    conn.execute("INSERT INTO models(created_at, bias, weights, go, skip, n_pos, n_neg, note) VALUES (?,?,?,?,?,?,?,?)",
                 (now_iso(), bias, json.dumps(weights), model["go"], model["skip"], n_pos, n_neg, note))
    conn.commit()

    changes = [(weights[f] - old["weights"].get(f, 0.0), f, old["weights"].get(f, 0.0), weights[f]) for f in weights]
    changes.sort(key=lambda c: -abs(c[0]))
    report.update(trained=True, changes=[c for c in changes if abs(c[0]) >= 0.05][:12],
                  go=model["go"], skip=model["skip"], old_go=old["go"], old_skip=old["skip"])
    return report


def hit_rates(conn):
    """How each verdict actually performed — the honest check on whether the filter is working."""
    out = {}
    rows = conn.execute("SELECT o.outcome, o.verdict FROM outcomes o JOIN (SELECT domain, MAX(id) id FROM outcomes "
                        "WHERE outcome != 'contacted' GROUP BY domain) last ON o.id = last.id").fetchall()
    for r in rows:
        if r["outcome"] not in LABELS:
            continue
        y = LABELS[r["outcome"]][0]
        a = out.setdefault(r["verdict"] or "?", [0, 0])
        a[0] += y
        a[1] += 1
    return out
