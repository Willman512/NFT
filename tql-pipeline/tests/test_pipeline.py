"""End-to-end test with fake sites (no network). Run: python -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TQL_ADDRESS", "123 Test St, Cincinnati, OH 45202")

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
        # FMCSA checked for every state, no fleets registered (individual tests add some)
        self.conn.executemany("INSERT INTO fmcsa_states(state, fetched_at, n) VALUES (?, '2099-01-01T00:00:00', 0)",
                              [(st,) for st in "AL FL OH TX CA GA PA IN KY TN".split()])
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


class FollowupTest(PipelineTest):
    def test_sequence(self):
        import datetime as dt
        from pipeline.today import followups, plan
        record_outcome(self.conn, "steelco.test", "contacted")
        self.conn.execute("UPDATE prospects SET contacted_at='2026-01-01' WHERE domain='steelco.test'")
        due, _ = followups(self.conn, self.policy, dt.date(2026, 1, 5))
        self.assertEqual([(r["domain"], t) for r, t, _ in due], [("steelco.test", 2)])
        text, warns = draft(self.v("steelco.test"), "(513) 555-0000", 2)
        self.assertIn("re: flatbeds out of pensacola", text)
        self.assertEqual(text.count("?"), 1)
        md = plan(self.conn, self.policy, drafts=True, today=dt.date(2026, 1, 5))
        self.assertIn("frostfarms.test", md)
        self.assertIn("Opener:", md)

    def test_finished_sequence(self):
        import datetime as dt
        from pipeline.today import followups
        for _ in range(3):
            record_outcome(self.conn, "steelco.test", "contacted")
        self.conn.execute("UPDATE prospects SET contacted_at='2026-01-01' WHERE domain='steelco.test'")
        due, finished = followups(self.conn, self.policy, dt.date(2026, 1, 20))
        self.assertEqual(due, [])
        self.assertEqual(finished[0][0]["domain"], "steelco.test")


class ExtractionTest(unittest.TestCase):
    def test_tel_links_city_state_and_thin(self):
        from pipeline.extract import extract
        icp = load_icp()
        html = page("Ohio Mulch", '<p>17 Locations in Columbus, Ohio and South Georgia.</p>'
                    '<a href="tel:614-445-4455">Call</a><p>Ecommerce Software by Shopify</p>')
        research, f, _ = extract([{"kind": "home", "url": "https://m.test", "html": html}], icp["signals"])
        self.assertIn("(614) 445-4455", research["phones"])
        self.assertIn("Columbus, OH", research["us_locations"])
        self.assertEqual(f["service_only"], 0)
        self.assertEqual(f["no_na_presence"], 0)
        _, f2, _ = extract([{"kind": "home", "url": "https://js.test", "html": "<html><body><div id=app></div></body></html>"}],
                           icp["signals"])
        self.assertEqual((f2["thin_site"], f2["no_na_presence"]), (1, 0))

    def test_foreign_flag(self):
        from pipeline.extract import extract
        html = page("Widgets GmbH", "<p>We manufacture widgets in Germany. Call +49 30 1234567.</p>" + "x " * 50)
        _, f, _ = extract([{"kind": "home", "url": "https://w.test", "html": html}], load_icp()["signals"])
        self.assertEqual(f["no_na_presence"], 1)


class QualityTest(unittest.TestCase):
    def test_city_person_subject(self):
        from pipeline.extract import _clean_city, _is_person
        from pipeline.email import _fit_subject
        self.assertEqual(_clean_city("100 Steel View Drive Roanoke"), "Roanoke")
        self.assertFalse(_is_person("High Industries"))
        self.assertTrue(_is_person("Dane Haskins"))
        self.assertEqual(_fit_subject("flatbeds out of {city}", "Seven Mile, OH", "x"), "flatbeds out of seven mile")
        self.assertEqual(_fit_subject("flatbeds out of {city}", "Tiffin, OH", "x"), "flatbeds out of tiffin oh")


class SkipRuleTest(unittest.TestCase):
    def test_freight_signal_never_skipped(self):
        from pipeline.score import score
        icp, policy = load_icp(), load_policy()
        model = {"version": 0, "bias": -5.0, "weights": {"reefer": 1.0}, "go": 0.6, "skip": 0.35}
        v, _, _ = score({"reefer": 1}, {}, model, icp, policy)
        self.assertEqual(v, "MAYBE")
        v, _, _ = score({"reefer": 1, "service_only": 1}, {}, model, icp, policy)
        self.assertEqual(v, "SKIP")
        v, _, _ = score({}, {}, model, icp, policy)
        self.assertEqual(v, "SKIP")


class ContactRankTest(unittest.TestCase):
    def test_rank(self):
        from pipeline.report import ranked_contacts
        got = ranked_contacts(["Delbert Bland — President", "Julie Stewart — VP of HR", "Luke Smith — Logistics Manager"])
        self.assertEqual(got, ["Luke Smith — Logistics Manager", "Delbert Bland — President"])


class DiscoverTest(unittest.TestCase):
    FAC = [
        {"FacName": "COOPER STEEL FABRICATORS INC", "FacCity": "SHELBYVILLE", "FacState": "TN", "FacZip": "37160",
         "FacNAICSCodes": "332312"},
        {"FacName": "Cooper Steel Fabricators, Inc.", "FacCity": "FLORENCE", "FacState": "KY", "FacZip": "41042",
         "FacNAICSCodes": "332312"},
        {"FacName": "COUNTY OF SHELBY ROAD DEPT", "FacCity": "X", "FacState": "TN", "FacZip": "1", "FacNAICSCodes": "332312"},
    ]

    def test_group_and_guess(self):
        from pipeline.discover import domain_guesses, group_companies
        cos = group_companies(self.FAC, ["332312"])
        self.assertEqual(len(cos), 1)
        self.assertEqual(len(cos[0]["plants"]), 2)
        self.assertEqual(cos[0]["profile"], ["flatbed"])
        self.assertIn("coopersteel.com", domain_guesses("Cooper Steel Fabricators Inc"))

    def test_site_matches(self):
        from pipeline.discover import site_matches
        good = "<title>Cooper Steel</title><p>Cooper Steel, Shelbyville, Tennessee</p>"
        self.assertTrue(site_matches(good, "Cooper Steel Fabricators Inc", "TN", ["Shelbyville"]))
        self.assertFalse(site_matches("<p>Mack trucks. oh yes.</p>", "Mack Industries Inc", "OH", ["Valley City"]))
        self.assertFalse(site_matches("<p>Cooper Steel domain is for sale Tennessee</p>", "Cooper Steel", "TN"))
        hidden = '<p>National Products, Elkhart IN</p><script>var x="Omega Industries Vancouver Washington";</script>'
        self.assertFalse(site_matches(hidden, "Omega Industries", "WA", ["Vancouver"]))   # script text doesn't count

    def test_forecast_math(self):
        from pipeline.forecast import beta_rate, prob_at_least_one, prospects_needed
        self.assertEqual(prospects_needed(0.02, 0.95), 149)
        self.assertGreater(prob_at_least_one(0.02, 149), 0.95)
        self.assertAlmostEqual(beta_rate(0, 0, 0.02, 50), 0.02)
        self.assertGreater(beta_rate(3, 50, 0.02, 50), 0.02)  # your wins raise the estimate

    def test_discover_end_to_end(self):
        from pipeline.discover import discover
        tmp = tempfile.TemporaryDirectory()
        conn = db.connect(os.path.join(tmp.name, "d.db"))
        conn.execute("INSERT INTO fmcsa_states VALUES ('TN', '2099-01-01T00:00:00', 0)")  # no network in tests

        def fake_json(url):
            if "get_facilities" in url:
                return {"Results": {"QueryID": "1", "QueryRows": "3"}}
            return {"Results": {"Facilities": self.FAC if "pageno=1" in url else []}}

        def fake_page(url):
            if "coopersteelfabricators.com" in url or "coopersteel.com" in url:
                return url, "<title>Cooper Steel</title> Cooper Steel Fabricators — Shelbyville, Tennessee"
            raise OSError("nope")

        import pipeline.discover as d
        orig = d._resolves
        d._resolves = lambda dom: True
        try:
            res = discover(conn, ["TN"], ["332312"], get_json=fake_json, get_page=fake_page, log=lambda *_: None)
        finally:
            d._resolves = orig
        self.assertEqual((res["companies"], res["matched"]), (1, 1))
        row = conn.execute("SELECT * FROM prospects").fetchone()
        self.assertIn("2 plant(s)", row["notes"])
        self.assertIn("flatbed", row["meta"])
        def no_network(url):
            raise AssertionError("EPA should not be called again: the lookup is cached")
        res2 = discover(conn, ["TN"], ["332312"], get_json=no_network, get_page=fake_page, log=lambda *_: None)
        self.assertEqual(res2["new"], 0)  # never re-adds a company it has seen
        self.assertEqual(res2["facilities"], 3)  # an interrupted run resumes from the cached EPA lookups
        conn.close()
        tmp.cleanup()


class DeskBridgeTest(PipelineTest):
    def test_export_shape_and_dedupe(self):
        from pipeline import desk
        existing = [{"type": "company", "id": "x1", "name": "Frost Farms Frozen Foods", "phone": "", "website": ""}]
        backup, skipped = desk.export(self.conn, existing)
        self.assertEqual(backup["app"], "prospecting-desk")
        names = [c["name"] for c in backup["companies"]]
        self.assertNotIn("Frost Farms Frozen Foods", names)   # already in the Desk
        self.assertEqual(skipped, 1)
        steel = next(c for c in backup["companies"] if "steelco" in c["website"])
        self.assertEqual(steel["stage"], "qualified")
        self.assertEqual(steel["tag"]["status"], "Unchecked")
        self.assertIn("Flatbed", steel["freight"]["modes"])
        self.assertTrue(all(1 <= v <= 5 for v in steel["ratings"].values()))
        self.assertEqual(steel["contacts"][0]["name"], "Jane Doe")
        desk.mark_exported(self.conn, backup)
        again, _ = desk.export(self.conn, existing)
        self.assertEqual(again["companies"], [])           # never sent twice

    def test_import_outcomes(self):
        from pipeline import desk
        from pipeline.forecast import stats
        companies = [
            {"type": "company", "id": "a", "name": "SteelCo Fabrication", "website": "https://steelco.test", "stage": "customer",
             "interactions": [{"kind": "Call", "outcome": "Load booked", "ts": "2026-11-01T10:00:00Z"}]},
            {"type": "company", "id": "b", "name": "Desk Only Bakery", "website": "", "stage": "nurture",
             "interactions": [{"kind": "Call", "outcome": "Not interested", "ts": "2026-11-02T10:00:00Z"}]},
            {"type": "company", "id": "c", "name": "Tagged Co", "website": "", "stage": "qualified",
             "tag": {"status": "Tagged"}, "interactions": []},
        ]
        res = desk.import_desk(self.conn, companies)
        self.assertEqual(res["outcomes"], {"load_booked": 1, "not_interested": 1, "owned_by_other_rep": 1})
        self.assertEqual(stats(self.conn)["wins"], 1)
        self.assertEqual(desk.import_desk(self.conn, companies)["outcomes"], {})  # idempotent

    def test_load_seed_zip_format(self):
        from pipeline.desk import _from_seed
        js = 'window.PC_SEED=[{"type":"company","id":"n1","name":"A"}];window.PC_LEGACY=[{"type":"company","id":"p1","name":"B"}];'
        self.assertEqual([c["id"] for c in _from_seed(js)], ["n1", "p1"])


class NaicsAngleTest(unittest.TestCase):
    def test_industry_code_beats_site_words(self):
        from pipeline.email import context
        from pipeline.desk import _modes
        row = {"research": json.dumps({"us_locations": ["Chicago, IL 60601"], "contacts": [], "signal_hits": {}}),
               "features": json.dumps({"reefer": 1, "manufacturer": 1, "naics_flatbed": 1}),
               "meta": json.dumps({"plants": ["Savannah, GA 31408"], "profile": ["flatbed"]}),
               "name": "Great Dane", "domain": "gd.test"}

        class R(dict):
            def keys(self):
                return super().keys()
        c = context(R(row))
        self.assertEqual(c["freight"], "flatbed loads")
        self.assertEqual(c["city"], "Savannah, GA")
        self.assertEqual(_modes({"reefer": 1, "naics_flatbed": 1}, {}), ["Flatbed"])


class GateTest(PipelineTest):
    def test_two_state_flatbed_shipper_qualifies(self):
        row = self.v("steelco.test")   # plants in FL + AL, "we ship nationwide by flatbed truckload"
        g = json.loads(row["gates"])
        self.assertTrue(g["qualified"], g)
        self.assertEqual(row["status"], "queued")

    def test_fmcsa_fleet_blocks(self):
        from pipeline.pipeline import gate_all
        self.conn.execute("INSERT INTO fmcsa VALUES ('FL','steelco fabrication','','PENSACOLA','123','PRIVATE PROPERTY',12,1)")
        gate_all(self.conn, self.policy, fetch_fmcsa=False, log=lambda *_: None)
        apply_filters(self.conn, self.policy)
        row = self.v("steelco.test")
        self.assertEqual(row["status"], "held")
        self.assertIn("USDOT 123", row["filter_reason"])

    def test_single_truck_is_not_a_fleet(self):
        from pipeline.pipeline import gate_all
        self.conn.execute("INSERT INTO fmcsa VALUES ('FL','steelco fabrication','','PENSACOLA','9','PRIVATE PROPERTY',1,1)")
        gate_all(self.conn, self.policy, fetch_fmcsa=False, log=lambda *_: None)
        self.assertTrue(json.loads(self.v("steelco.test")["gates"])["qualified"])

    def test_single_state_unknown_is_held(self):
        row = self.v("oldmill.test")   # one AL address, no multi-state evidence
        g = json.loads(row["gates"])
        self.assertEqual(g["interstate"][0], "unknown")
        self.assertNotEqual(row["status"], "queued")

    def test_site_fleet_words_block(self):
        from pipeline.qualify import OWN_FLEET
        for t in ["Now hiring CDL drivers", "our fleet of 40 trucks", "Our drivers deliver on time", "Class A driver positions"]:
            self.assertTrue(OWN_FLEET.search(t), t)
        self.assertFalse(OWN_FLEET.search("We ship by LTL and truckload carriers nationwide"))
        self.assertFalse(OWN_FLEET.search("Our Trailers are built to last"))   # product, not a fleet
        for t in ["500+ company-owned routes", "Direct Store Delivery", "route sales representatives"]:
            self.assertTrue(OWN_FLEET.search(t), t)

    def test_possessive_name_matches_fmcsa(self):
        from pipeline import fmcsa
        self.conn.execute("INSERT INTO fmcsa VALUES ('PA','herr foods','','NOTTINGHAM','1','PRIVATE PROPERTY',90,1)")
        self.assertEqual(len(fmcsa.match(self.conn, "Herr's", ["PA"], ["Nottingham"])), 1)
        self.assertEqual(len(fmcsa.match(self.conn, "Herr's", ["PA"], ["Lancaster"])), 0)  # needs the same city

    def test_trucking_subsidiary_and_hq_state(self):
        from pipeline import fmcsa
        self.conn.execute("INSERT INTO fmcsa VALUES ('PA','utztran','','HANOVER','2','PRIVATE PROPERTY',47,1)")
        self.conn.execute("INSERT INTO fmcsa VALUES ('OH','unverferth mfg','','KALIDA','3','PRIVATE PROPERTY',19,1)")
        self.assertEqual(len(fmcsa.match(self.conn, "Utz Quality Foods", ["PA"], [])), 1)
        self.assertEqual(len(fmcsa.match(self.conn, "Unverferth", ["IA", "GA"], [])), 1)
        self.assertEqual(len(fmcsa.match(self.conn, "Utz Quality Foods", ["OH"], [])), 0)

    def test_no_false_matches(self):
        from pipeline import fmcsa
        self.conn.execute("INSERT INTO fmcsa VALUES ('OH','ohio cat','','COLUMBUS','4','PRIVATE PROPERTY',1875,1)")
        self.conn.execute("INSERT INTO fmcsa VALUES ('MT','mikesell sales service','','HELENA','5','PRIVATE PROPERTY',4,1)")
        self.assertEqual(fmcsa.match(self.conn, "Ohio Steel Industries", ["OH"], ["Columbus"]), [])
        self.assertEqual(fmcsa.match(self.conn, "Mikesell's", ["OH"], ["Dayton"]), [])


class ForwarderTest(unittest.TestCase):
    def test_forwarder_is_prospect_not_competitor(self):
        from pipeline.extract import pattern_features
        icp = load_icp()
        f, _ = pattern_features("We are a licensed NVOCC and freight forwarder offering ocean freight, air freight and customs brokerage.",
                                icp["signals"])
        self.assertEqual((f["forwarder"], f["broker_3pl"]), (1, 0))
        f2, _ = pattern_features("Freight brokerage: carrier setup packet here.", icp["signals"])
        self.assertEqual((f2["forwarder"], f2["broker_3pl"]), (0, 1))

    def test_forwarder_flag_and_gates(self):
        from pipeline.score import score
        from pipeline.qualify import evaluate
        icp, policy = load_icp(), load_policy()
        model = {"version": 0, "bias": 0.0, "weights": {"forwarder": 3.0}, "go": 0.6, "skip": 0.35}
        v, _, reasons = score({"forwarder": 1}, {}, model, icp, policy)
        self.assertEqual(v, "GO")
        self.assertTrue(any("FORWARDER" in r and "manager" in r for r in reasons))

    def test_customs_broker_is_international(self):
        from pipeline.qualify import evaluate
        conn = db.connect(":memory:")
        conn.execute("INSERT INTO fmcsa_states VALUES ('TX','2099-01-01T00:00:00',0)")
        g = evaluate(conn, {"name": "Border Customs Brokers"}, "Licensed customs broker in Laredo. Import / export clearance.",
                     {"us_locations": ["Laredo, TX 78041"]}, {"forwarder": 1}, {}, load_policy())
        self.assertTrue(g["qualified"], g)

    def test_forwarder_own_usdot_trucks_fail(self):
        from pipeline.qualify import evaluate
        conn = db.connect(":memory:")
        conn.execute("INSERT INTO fmcsa_states VALUES ('FL','2099-01-01T00:00:00',0)")
        conn.execute("INSERT INTO fmcsa_dot VALUES ('1397252', 1324, 'AUTHORIZED FOR HIRE', 'A', 'PODS')")
        g = evaluate(conn, {"name": "Pods"}, "Ocean freight forwarder nationwide.", {"us_locations": ["Clearwater, FL 33760"]},
                     {"forwarder": 1}, {"dot": "1397252"}, load_policy())
        self.assertEqual(g["fleet"][0], "fail")
        self.assertIn("1324 trucks", g["fleet"][1])

    def test_fmcsa_forwarder_rows(self):
        from pipeline.discover import forwarder_companies
        rows = [{"docket_number": "FF1", "legal_name": "ACME FORWARDING INC", "bus_city": "ATLANTA", "bus_state_code": "GA",
                 "bus_zip_code": "30339", "bus_telno": "4045551212"},
                {"docket_number": "FF2", "legal_name": "EXEL INC", "dba_name": "DHL SUPPLY CHAIN", "bus_city": "X", "bus_state_code": "OH"}]
        cos = forwarder_companies(rows)
        self.assertEqual([c["name"] for c in cos], ["Acme Forwarding Inc"])
        self.assertEqual(cos[0]["phone"], "(404) 555-1212")
        self.assertEqual(cos[0]["profile"], ["forwarder"])

    def test_csv_list(self):
        from pipeline.discover import companies_from_csv
        tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        tmp.write("Company,City,State,Phone\nBLUE WAVE LOGISTICS LLC,Savannah,GA,912-555-0101\n")
        tmp.close()
        cos = companies_from_csv(tmp.name)
        self.assertEqual(cos[0]["name"], "Blue Wave Logistics Llc")
        self.assertEqual(cos[0]["plants"], ["Savannah, GA"])
        os.unlink(tmp.name)


class CustomerProfileTest(PipelineTest):
    def test_desk_status_maps_to_model_customer(self):
        from pipeline.desk import desk_outcome
        self.assertEqual(desk_outcome({"tag": {"status": "Active customer (other rep)"}, "interactions": []})[0],
                         "other_rep_customer")

    def test_profile_and_lookalike(self):
        from pipeline import profile
        from pipeline.pipeline import rescore_all
        for d in ("steelco.test", "oldmill.test", "overseas.test"):   # three manufacturers as model customers
            record_outcome(self.conn, d, "other_rep_customer")
        self.assertEqual(self.v("steelco.test")["status"], "suppressed")   # never on a call list
        prof = profile.build(self.conn)
        self.assertTrue(prof["active"])
        self.assertEqual(prof["n_models"], 3)
        distinctive = [t for _, t, _, _ in prof["distinctive"]]
        self.assertIn("manufacturer", distinctive)
        text = profile.render(prof, self.icp)
        self.assertIn("3 active customers", text)
        rescore_all(self.conn, self.icp, self.policy, quiet=True)
        saas = json.loads(self.v("saasy.test")["features"])
        self.assertEqual(saas.get("lookalike"), 0)                    # a software firm doesn't look like them

    def test_profile_needs_three(self):
        from pipeline import profile
        record_outcome(self.conn, "steelco.test", "other_rep_customer")
        self.assertFalse(profile.build(self.conn)["active"])


class DeskModelTest(PipelineTest):
    def test_model_without_website_uses_desk_research(self):
        from pipeline import desk
        c = {"type": "company", "id": "m1", "name": "Sunny Citrus Packers", "website": "", "city": "Orange Cove",
             "state": "CA", "industry": "Produce: citrus", "description": "Grower and packer of fresh citrus.",
             "freight": {"modes": ["Reefer", "Van"], "commodities": {"v": "Oranges"}},
             "tag": {"status": "Active customer (other rep)"}, "interactions": []}
        orig = desk._find_site
        desk._find_site = lambda c: None
        try:
            desk.import_desk(self.conn, [c])
        finally:
            desk._find_site = orig
        self.assertEqual(desk.profile_from_desk(self.conn, self.icp), 1)
        row = self.conn.execute("SELECT * FROM prospects WHERE name='Sunny Citrus Packers'").fetchone()
        f = json.loads(row["features"])
        self.assertEqual((f["reefer"], f["food_producer"]), (1, 1))
        self.assertEqual(row["status"], "suppressed")
