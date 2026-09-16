import json

import pytest

from forge.core.money import usd
from forge.staff.brain import ScriptedBrain, pick_brain
from forge.staff.office import Office
from forge.staff.roles import PERSONAS, STANDUP_ORDER


def test_everyone_speaks_when_an_idea_lands(office):
    office.propose("Sell printable wedding checklists on Etsy for $8.")
    report = office.standup(force=True)
    who = {r["persona"] for r in report.remarks}
    assert {"researcher", "salesman", "banker", "maker", "manager"} <= who


def test_an_empty_office_says_so_rather_than_inventing_work(make_cfg):
    from forge.staff.office import Office

    office = Office(make_cfg(ventures={}))
    try:
        report = office.standup(force=True)
        assert report.ran == [] and report.vetted == []
        assert any("Nothing on the board" in r["text"] for r in report.remarks)
    finally:
        office.close()


def test_ventures_still_run_when_configured(office):
    report = office.standup(force=True)
    assert set(report.ran) == {"coloringbook", "memecoin"}


def test_dry_run_decides_but_does_not_execute(office):
    report = office.standup(force=True, dry_run=True)
    assert report.ran == []
    assert office.ledger.net_pnl == 0
    assert any(r["persona"] == "manager" for r in report.remarks)


def test_the_book_venture_obeys_a_theme_hint(office):
    office.ledger.set_state("venture:coloringbook:theme_hint", "mazes")
    office.ledger.set_state("venture:coloringbook:price_hint", "9.99")
    office.orch.run_cycle(force=True, only={"coloringbook"})
    slug = office.ledger.get_state("venture:coloringbook:last_slug")
    assert slug.startswith("mazes-")
    manifest = json.loads(
        (office.cfg.out_dir / "coloringbook" / slug / "metadata.json").read_text()
    )
    assert manifest["price_usd"] == 9.99


def test_a_losing_venture_is_eliminated_and_stays_blocked(make_cfg):
    office = Office(make_cfg(grace_ticks=1, max_strikes=2))
    try:
        seen = []
        for _ in range(8):
            report = office.standup(force=True)
            seen.append(report.verdicts.get("memecoin"))
        assert "eliminate" in seen
        assert office.survival.status("memecoin").state == "eliminated"
        # Its capital came home and it is never run again.
        assert office.ledger.balance("asset:float:memecoin") == 0
        assert "memecoin" not in office.standup(force=True).ran
        office.ledger.check_invariant()
    finally:
        office.close()


def test_a_halted_office_does_nothing_until_resumed(office):
    office.orch.halt("testing")
    report = office.standup(force=True)
    assert report.halted and report.ran == []
    office.orch.resume()
    assert office.standup(force=True).ran


def test_recording_income_changes_the_verdict(make_cfg):
    office = Office(make_cfg(grace_ticks=1))
    try:
        office.standup(force=True)
        office.ledger.record_cost("coloringbook", usd(2), "a cost, for the sake of argument")
        office.record_income("coloringbook", 9.0, "KDP royalty")
        books = office.ledger.books("coloringbook")
        assert books.paid_for_itself
        report = office.standup(force=True)
        assert report.verdicts["coloringbook"] == "promote"
    finally:
        office.close()


def test_context_is_json_serialisable(office):
    office.standup(force=True)
    blob = json.dumps(office.context(), default=str)
    assert "Infinity" not in blob and "NaN" not in blob


# ----------------------------------------------------------------------
def test_scripted_brain_answers_from_the_books(office):
    office.standup(force=True)
    answer = office.ask("banker", "how much money have we made?")["answer"]
    assert "$" in answer, answer


@pytest.mark.parametrize("key", list(PERSONAS))
def test_every_persona_can_hold_a_conversation(office, key):
    reply = office.ask(key, "what is going on?")
    assert reply["answer"].strip()
    thread = office.memory.thread(key)
    assert [m.speaker for m in thread[-2:]] == ["user", "staff"]


def test_asking_an_unknown_persona_is_an_error(office):
    with pytest.raises(KeyError):
        office.ask("janitor", "hello")


def test_standup_order_covers_every_persona():
    assert set(STANDUP_ORDER) == set(PERSONAS)


def test_brain_falls_back_to_scripted_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(pick_brain({"brain": "auto"}), ScriptedBrain)
    assert isinstance(pick_brain({"brain": "claude"}), ScriptedBrain)


def test_claude_brain_falls_back_when_the_api_fails(office, monkeypatch):
    from forge.staff.brain import ClaudeBrain

    brain = ClaudeBrain(api_key="not-a-real-key")
    monkeypatch.setattr(brain, "_call", _boom)
    text = brain.render(PERSONAS["banker"], {"kind": "finding", "summary": "We are down $3.00."})
    assert "down $3.00" in text  # the scripted line, not an exception


def _boom(*args, **kwargs):
    raise RuntimeError("no network")
