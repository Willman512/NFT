"""End-to-end test with fake sites (no network). Run: python -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import db  # noqa: E402
from pipeline.config import load_icp, load_policy  # noqa: E402
from pipeline.email import draft  # noqa: E402
from pipeline.fetch import Fetcher  # noqa: E402
from pipeline.learn import learn  # noqa: E402
from pipeline.pipeline import add_lead, apply_filters, queue, record_outcome, rescore_all, research_pending  # noqa: E402


def page(title, body, lang="en", year=2026, nav=""):
    return (f'<html lang="{lang}"><head><title>{title}</title><meta name="description" content="{title}"></head>'
            f'<body><nav>{nav}</nav><h1>{title}</h1>{body}<footer>© {year} {title}</footer></body></html>')


NAV = '<a href="/about">About Us</a><a href="/products">Products</a><a href="/contact">Contact</a>'
SITES = {
    # Strong fit: flatbed manufacturer, multiple plants, ships nationally
    "https://steelco.test": page("SteelCo Fabrication - Built Strong Since 1962",
        "<p>We manufacture structural steel beams and trusses at two plants.</p>", nav=NAV),
    "https://steelco.test/about": page("About", "<p>Family-owned since 1962. We ship nationwide by flatbed truckload.</p>"),
    "https://steelco.test/products": page("Products", "<p>Steel beams, coils, pipe.</p>"),
    "https://steelco.test/contact": page("Contact",
        "<p>Plant 1: 100 Mill Rd, Pensacola, FL 32501</p><p>Plant 2: 5 Iron Way, Mobile, AL 36602</p>"
        "<p>Call (850) 555-0100</p><p>Jane Doe, Logistics Manager</p><p>info@steelco.test</p>"),
    # Broker: should be capped at MAYBE
    "https://haulers.test": page("Haulers Logistics",
        "<p>We are a freight brokerage and 3PL. Carrier setup packet here. Truckload, flatbed, reefer nationwide.</p>"
        "<p>Columbus, OH 43215 (614) 555-0199</p>"),
    # Service business: SKIP
    "https://saasy.test": page("Saasy", "<p>Our SaaS software helps marketing teams. Consulting services.</p>"
                               "<p>Austin, TX 78701</p>"),
    # Reefer producer
    "https://frostfarms.test": page("Frost Farms Frozen Foods",
        "<p>Growers and packers of frozen vegetables. We ship refrigerated truckloads to grocery retailers "
        "across the US and Canada.</p><p>Salinas, CA 93901 (831) 555-0142</p>"),
    # Stale site
    "https://oldmill.test": page("Old Mill Lumber", "<p>Lumber manufacturer. Dothan, AL 36301</p>", year=2015),
    # Overseas only
    "https://overseas.test": page("Overseas Widgets GmbH", "<p>We manufacture widgets in Germany. Pallets ship worldwide.</p>"),
}


def fake_get(url):
    url = url.rstrip("/")
    if url not in SITES:
        raise OSError(f"404 {url}")
    return url, SITES[url]


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(os.path.join(self.tmp.name, "t.db"))
        self.icp, self.policy = load_icp(), load_policy()
        for url in SITES:
            if url.count("/") == 2:
                add_lead(self.conn, url)
        research_pending(self.conn, self.icp, self.policy, Fetcher(self.policy, get=fake_get), log=lambda *_: None)
        apply_filters(self.conn, self.policy)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def v(self, dom):
        return db.get_prospect(self.conn, dom)

    def test_verdicts(self):
        self.assertEqual(self.v("steelco.test")["verdict"], "GO")
        self.assertEqual(self.v("frostfarms.test")["verdict"], "GO")
        self.assertIn(self.v("haulers.test")["verdict"], ("MAYBE", "SKIP"))
        self.assertEqual(self.v("saasy.test")["verdict"], "SKIP")
        self.assertNotEqual(self.v("oldmill.test")["verdict"], "GO")
        self.assertNotEqual(self.v("overseas.test")["verdict"], "GO")

    def test_research_card_facts(self):
        r = json.loads(self.v("steelco.test")["research"])
        self.assertIn("(850) 555-0100", r["phones"])
        self.assertIn("Pensacola, FL 32501", r["us_locations"])
        self.assertTrue(any("Jane Doe" in c for c in r["contacts"]))
        self.assertEqual(len(r["pages"]), 4)

    def test_dedupe_and_redirect(self):
        _, status = add_lead(self.conn, "https://www.google.com/url?q=https://www.steelco.test/&sa=D")
        self.assertEqual(status, "duplicate")

    def test_outcomes_filter_queue(self):
        record_outcome(self.conn, "steelco.test", "owned_by_other_rep")
        apply_filters(self.conn, self.policy)
        doms = [r["domain"] for r in queue(self.conn)]
        self.assertNotIn("steelco.test", doms)
        self.assertEqual(self.v("steelco.test")["status"], "suppressed")

    def test_cooldown(self):
        record_outcome(self.conn, "frostfarms.test", "contacted")
        apply_filters(self.conn, self.policy)
        self.assertEqual(self.v("frostfarms.test")["status"], "contacted")
        self.assertIn("cooldown", self.v("frostfarms.test")["filter_reason"])

    def test_not_interested_held_out(self):
        record_outcome(self.conn, "steelco.test", "not_interested")
        apply_filters(self.conn, self.policy)
        self.assertNotIn("steelco.test", [r["domain"] for r in queue(self.conn)])

    def test_hit_rates_use_snapshot(self):
        from pipeline.learn import hit_rates
        record_outcome(self.conn, "steelco.test", "load_booked")
        record_outcome(self.conn, "saasy.test", "bad_fit")
        self.assertEqual(hit_rates(self.conn), {"GO": [1, 1], "SKIP": [0, 1]})

    def test_learning_moves_weights(self):
        for d, o in [("steelco.test", "load_booked"), ("frostfarms.test", "meeting"), ("haulers.test", "replied"),
                     ("saasy.test", "bad_fit"), ("oldmill.test", "no_response"), ("overseas.test", "not_interested")]:
            record_outcome(self.conn, d, o)
        rep = learn(self.conn, self.icp, adopt_terms=2)
        self.assertTrue(rep["trained"], rep)
        self.assertEqual(db.latest_model(self.conn)["version"], 1)
        rescore_all(self.conn, self.icp, self.policy, quiet=True)
        self.assertEqual(self.v("steelco.test")["model_version"], 1)

    def test_email_draft_rules(self):
        text, warnings = draft(self.v("steelco.test"), phone="(513) 555-0000")
        self.assertIn("Subject: flatbeds out of pensacola", text)
        self.assertIn("Hi Jane,", text)
        self.assertIn("I'm Will, a broker with TQL.", text)
        self.assertEqual(warnings, [], text)


if __name__ == "__main__":
    unittest.main()
