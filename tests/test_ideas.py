"""The pipeline the office actually runs on: propose, vet, delegate, work."""

import pytest

from forge.staff.ideas import (
    ACCEPTED,
    BLOCKED,
    DELIVERED,
    DONE,
    IN_PROGRESS,
    KIND_ASK,
    PARKED,
    PROPOSED,
    TODO,
    IdeaBook,
    slugify,
    title_of,
)
from forge.staff.vetting import Signals, decide, mentions, read_idea, review_idea
from forge.staff.work import plan_assignments, write_brief

ETSY = "Sell printable wedding planning checklists on Etsy for $8. Automate generating them."
PALLETS = ("Buy pallets of returned electronics, test them, and resell the working ones "
           "on eBay. Need about $400 upfront for the first pallet.")
CRYPTO = "Automate a daily crypto trading bot that scrapes signals off Reddit."
VAGUE = "make an app"


@pytest.fixture
def book(ledger) -> IdeaBook:
    return IdeaBook(ledger)


# ----------------------------------------------------------------------
# storage
# ----------------------------------------------------------------------
def test_an_idea_needs_words(book):
    with pytest.raises(ValueError):
        book.propose("   ")


def test_title_is_taken_from_the_first_sentence():
    assert title_of("Sell mugs online. Then scale it up.") == "Sell mugs online."
    assert title_of("") == "Untitled idea"
    assert len(title_of("x " * 200)) <= 99


def test_slugs_are_safe_for_a_filename():
    assert slugify("Sell $8 checklists on Etsy!") == "sell-8-checklists-on-etsy"
    assert slugify("!!!") == "idea"


def test_a_reviewer_can_change_their_mind_without_duplicating(book):
    idea = book.propose(ETSY)
    book.add_review(idea.id, "banker", "against", "Too dear")
    book.add_review(idea.id, "banker", "for", "Actually, fine")
    reviews = book.reviews(idea.id)
    assert len(reviews) == 1 and reviews[0].stance == "for"


def test_stance_is_validated(book):
    idea = book.propose(ETSY)
    with pytest.raises(ValueError):
        book.add_review(idea.id, "banker", "maybe", "hmm")


def test_the_desk_shows_blocked_work_rather_than_looking_idle(book):
    idea = book.propose(ETSY)
    blocked = book.assign(idea.id, "manager", "A question for you", kind=KIND_ASK, seq=1)
    book.update_assignment(blocked.id, status=BLOCKED, blocked_on="you")
    assert book.next_for("manager").id == blocked.id

    doing = book.assign(idea.id, "manager", "Something actionable", seq=2)
    assert book.next_for("manager").id == doing.id, "real work outranks a blocked item"


def test_progress_counts_every_state(book):
    idea = book.propose(ETSY)
    a = book.assign(idea.id, "maker", "one")
    book.assign(idea.id, "maker", "two")
    book.update_assignment(a.id, status=DONE)
    assert book.progress(idea.id) == {TODO: 1, "doing": 0, BLOCKED: 0, DONE: 1}


def test_notes_accumulate_in_order(book):
    idea = book.propose(ETSY)
    a = book.assign(idea.id, "maker", "one")
    book.update_assignment(a.id, note="first")
    updated = book.update_assignment(a.id, note="second")
    assert updated.notes == ["first", "second"]


# ----------------------------------------------------------------------
# reading an idea
# ----------------------------------------------------------------------
def test_word_matching_does_not_fire_on_substrings():
    assert not mentions("a relationship column", "ship")
    assert mentions("we ship mugs", "ship")
    assert mentions("illustrations", "illustrat*")


def test_signals_from_a_concrete_idea():
    sig = read_idea(ETSY)
    assert sig.platforms == ["Etsy"]
    assert sig.prices == ["$8"]
    assert sig.automatable
    assert not sig.physical, "a printable is not a physical good"
    assert not sig.thin


def test_a_vague_idea_is_recognised_as_vague():
    assert read_idea(VAGUE).thin


def test_cautions_only_fire_on_things_worth_pausing_for():
    assert read_idea(CRYPTO).cautions
    assert not read_idea(ETSY).cautions
    assert not read_idea(PALLETS).cautions, "ordinary resale is not a legal flag"


# ----------------------------------------------------------------------
# vetting
# ----------------------------------------------------------------------
def test_every_desk_has_an_opinion_on_anything():
    for text in (ETSY, PALLETS, CRYPTO, VAGUE, "Teach piano over video call"):
        for key in ("banker", "salesman", "researcher", "maker"):
            verdict = review_idea(key, text, 3200)
            assert verdict.stance in {"for", "against", "unsure"}
            assert verdict.headline.strip()


def test_the_banker_counts_what_the_idea_asks_for():
    poor = review_idea("banker", PALLETS, purse_cents=3200)
    assert poor.stance == "against" and "$400" in poor.headline
    rich = review_idea("banker", PALLETS, purse_cents=200_000)
    assert rich.stance != "against"


def test_an_unaffordable_idea_is_parked_not_refused():
    sig = read_idea(PALLETS)
    verdicts = {k: review_idea(k, PALLETS, 3200) for k in
                ("banker", "salesman", "researcher", "maker")}
    call = decide(verdicts, sig, 3200)
    assert call.status == PARKED
    assert "cheaper version" in call.reason


def test_a_flagged_idea_waits_for_the_owner():
    sig = read_idea(CRYPTO)
    verdicts = {k: review_idea(k, CRYPTO, 3200) for k in
                ("banker", "salesman", "researcher", "maker")}
    call = decide(verdicts, sig, 3200)
    assert call.status == PARKED and "crypto" in call.reason


def test_a_thin_idea_is_parked_with_a_request_for_more():
    sig = read_idea(VAGUE)
    verdicts = {k: review_idea(k, VAGUE, 3200) for k in
                ("banker", "salesman", "researcher", "maker")}
    call = decide(verdicts, sig, 3200)
    assert call.status == PARKED and "few more sentences" in call.reason


def test_an_ordinary_idea_is_accepted_as_a_small_test():
    sig = read_idea(ETSY)
    verdicts = {k: review_idea(k, ETSY, 3200) for k in
                ("banker", "salesman", "researcher", "maker")}
    assert decide(verdicts, sig, 3200).status == ACCEPTED


def test_two_objections_decline_it():
    from forge.staff.vetting import Verdict

    verdicts = {
        "banker": Verdict("for", "fine"),
        "salesman": Verdict("against", "nobody wants it"),
        "researcher": Verdict("against", "the premise is wrong"),
        "maker": Verdict("for", "buildable"),
    }
    assert decide(verdicts, read_idea(ETSY), 3200).status == "declined"


# ----------------------------------------------------------------------
# delegation
# ----------------------------------------------------------------------
def test_work_is_split_across_the_desks():
    sig = read_idea(ETSY)
    verdicts = {k: review_idea(k, ETSY, 3200) for k in
                ("banker", "salesman", "researcher", "maker")}
    plans = plan_assignments(
        type("I", (), {"id": 1, "title": "Etsy checklists", "text": ETSY})(), verdicts, sig
    )
    owners = {p.owner for p in plans}
    assert {"researcher", "salesman", "banker", "maker"} <= owners
    # Research comes before building, because that is the point of research.
    order = {p.owner: p.seq for p in plans if p.kind != KIND_ASK}
    assert order["researcher"] < order["banker"] < order["maker"]


def test_questions_are_spread_across_the_desks_not_taken_from_one():
    from forge.staff.vetting import Verdict

    verdicts = {
        "banker": Verdict("for", "x", questions=["b1", "b2", "b3"]),
        "salesman": Verdict("for", "x", questions=["s1"]),
        "researcher": Verdict("for", "x", questions=["r1"]),
        "maker": Verdict("for", "x", questions=[]),
    }
    asks = [p.title for p in plan_assignments(
        type("I", (), {"id": 1, "title": "t", "text": ETSY})(), verdicts, read_idea(ETSY))
        if p.kind == KIND_ASK]
    assert asks == ["b1", "s1", "r1"]


# ----------------------------------------------------------------------
# end to end, through the office
# ----------------------------------------------------------------------
def test_an_idea_goes_all_the_way_to_a_brief(empty_office):
    office = empty_office
    idea = office.propose(ETSY)
    assert idea.status == PROPOSED

    office.standup(force=True)
    assert office.ideas.idea(idea.id).status == IN_PROGRESS
    assert len(office.ideas.reviews(idea.id)) == 4

    office.standup(force=True)  # everyone finishes their piece

    blocked = office.ideas.assignments(idea_id=idea.id, status=BLOCKED)
    assert blocked, "the manager should have questions only you can answer"
    assert all(a.blocked_on == "you" for a in blocked)

    for a in blocked:
        office.answer(a.id, "Brides in the six months before the wedding.")

    assert office.ideas.idea(idea.id).status == DELIVERED
    brief = office.cfg.out_dir / "ideas" / f"{idea.id:03d}-{idea.slug}" / "brief.md"
    assert brief.exists()
    text = brief.read_text()
    assert "Etsy" in text and "What each of them said" in text


def test_questions_reach_your_task_list_and_clear_when_answered(empty_office):
    office = empty_office
    office.propose(ETSY)
    office.standup(force=True)
    before = office.queue.count_open()
    assert before >= 1

    blocked = office.ideas.assignments(status=BLOCKED)
    office.answer(blocked[0].id, "Brides, six months out.")
    assert office.queue.count_open() == before - 1


def test_answering_is_validated(empty_office):
    office = empty_office
    office.propose(ETSY)
    office.standup(force=True)
    blocked = office.ideas.assignments(status=BLOCKED)
    with pytest.raises(ValueError):
        office.answer(blocked[0].id, "   ")
    with pytest.raises(KeyError):
        office.answer(99999, "hello")


def test_a_parked_idea_gets_no_work(empty_office):
    office = empty_office
    idea = office.propose(CRYPTO)
    office.standup(force=True)
    assert office.ideas.idea(idea.id).status == PARKED
    assert office.ideas.assignments(idea_id=idea.id) == []


def test_two_ideas_are_worked_in_parallel(empty_office):
    office = empty_office
    office.propose(ETSY)
    office.propose("Teach beginner piano over video call to adults for $30 an hour.")
    office.standup(force=True)
    statuses = {i.status for i in office.ideas.ideas()}
    assert statuses == {IN_PROGRESS}
    # Each desk holds one thing at a time, so the board does not stampede.
    for key in ("researcher", "salesman", "banker", "maker"):
        assert office.ideas.next_for(key) is not None


def test_the_brief_survives_an_idea_with_nothing_in_it(tmp_path, book):
    idea = book.propose("Something short")
    path = write_brief(tmp_path, idea, [], [])
    assert path.exists() and "Something short" in path.read_text()
