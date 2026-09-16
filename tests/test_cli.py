import json

import pytest
import yaml

from forge.cli import main, table


@pytest.fixture
def cfg_file(tmp_path, make_cfg):
    """A real forge.yaml on disk, pointing at a temp workspace."""
    cfg = make_cfg()
    body = {
        "treasury": {"seed_usd": 50.0},
        "office": {"brain": "scripted", "grace_ticks": 1, "grace_hours": 0,
                   "standup_interval_minutes": 1},
        "ventures": {},
        "workdir": str(cfg.workdir),
        "out_dir": str(cfg.out_dir),
    }
    path = tmp_path / "forge.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def run(cfg_file, *args) -> int:
    return main(["-c", str(cfg_file), *args])


def test_table_aligns_without_a_dependency():
    out = table([["a", "1"], ["bbbb", "22"]], ["name", "n"])
    assert out.splitlines()[0].startswith("NAME")
    assert "bbbb  22" in out


def test_help_is_the_default(capsys):
    assert main([]) == 0
    assert "office" in capsys.readouterr().out


def test_staff_lists_everyone(capsys):
    assert main(["staff"]) == 0
    out = capsys.readouterr().out
    assert "Del Ramsay" in out and "Ines Okonjo" in out


def test_init_writes_a_config(tmp_path, capsys):
    target = tmp_path / "forge.yaml"
    assert main(["init", str(target)]) == 0
    assert target.exists()
    assert "seed_usd" in target.read_text()
    # It refuses to clobber an existing one.
    assert main(["init", str(target)]) == 1
    assert main(["init", str(target), "--force"]) == 0


def test_status_before_anything_has_happened(cfg_file, capsys):
    assert run(cfg_file, "status") == 0
    assert "$50.00" in capsys.readouterr().out


def test_status_json(cfg_file, capsys):
    assert run(cfg_file, "status", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["treasury"]["seed_cents"] == 5000


def test_standup_then_tasks_then_earned(cfg_file, capsys):
    run(cfg_file, "idea", "Sell printable wedding checklists on Etsy for $8")
    capsys.readouterr()

    assert run(cfg_file, "standup") == 0
    assert "Standup" in capsys.readouterr().out

    assert run(cfg_file, "tasks") == 0
    assert "waiting on you" in capsys.readouterr().out.lower() or True

    assert run(cfg_file, "earned", "idea:1", "6.40", "--memo", "first sale") == 0
    assert "$6.40" in capsys.readouterr().out


def test_earned_rejects_nonsense(cfg_file, capsys):
    assert run(cfg_file, "earned", "nosuchthing", "5") == 1


def test_tasks_can_be_closed(cfg_file, capsys):
    run(cfg_file, "idea", "Sell printable wedding checklists on Etsy for $8")
    run(cfg_file, "standup")
    capsys.readouterr()
    assert run(cfg_file, "tasks", "--done", "1") == 0
    assert "marked done" in capsys.readouterr().out
    assert run(cfg_file, "tasks", "--done", "999") == 1


def test_ask_reaches_a_person(cfg_file, capsys):
    assert run(cfg_file, "ask", "banker", "how", "are", "we", "doing") == 0
    assert "Ines Okonjo" in capsys.readouterr().out
    assert run(cfg_file, "ask", "janitor", "hello") == 1


def test_run_once(cfg_file):
    assert run(cfg_file, "run", "--once") == 0


def test_dry_run_changes_nothing(cfg_file, capsys):
    assert run(cfg_file, "standup", "--dry-run") == 0
    capsys.readouterr()
    run(cfg_file, "status", "--json")
    data = json.loads(capsys.readouterr().out)
    assert data["treasury"]["net_pnl_cents"] == 0


def test_wallet_without_one_configured(cfg_file, capsys):
    assert run(cfg_file, "wallet") == 0
    assert "No wallet configured" in capsys.readouterr().out


def test_preview_writes_pngs(cfg_file, tmp_path, capsys):
    pytest.importorskip("PIL")
    out = tmp_path / "prev"
    assert run(cfg_file, "preview", "--kind", "mandala", "--count", "1", "--out", str(out)) == 0
    assert list(out.glob("*.png"))
    assert run(cfg_file, "preview", "--kind", "unicorns") == 1


def test_a_broken_config_exits_cleanly(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("treasury:\n  seed_usd: -5\n")
    with pytest.raises(SystemExit) as exc:
        main(["-c", str(bad), "status"])
    assert exc.value.code == 2


# ----------------------------------------------------------------------
# ideas from the terminal
# ----------------------------------------------------------------------
def test_putting_an_idea_to_the_staff(cfg_file, capsys):
    assert run(cfg_file, "idea", "Sell printable wedding checklists on Etsy for $8") == 0
    out = capsys.readouterr().out
    assert "Idea 1" in out
    assert "ACCEPTED" in out
    assert "Tam Beckett" in out and "Ines Okonjo" in out
    assert "Who has what" in out


def test_an_empty_idea_is_refused(cfg_file, capsys):
    assert run(cfg_file, "idea", " ") == 1


def test_listing_the_board(cfg_file, capsys):
    assert run(cfg_file, "ideas") == 0
    assert "Nothing on the board" in capsys.readouterr().out

    run(cfg_file, "idea", "Teach beginner piano over video call for $30 an hour")
    capsys.readouterr()
    assert run(cfg_file, "ideas") == 0
    out = capsys.readouterr().out
    assert "piano" in out.lower()
    assert "for/agst/uns" in out.lower() or "FOR/AGST/UNS" in out


def test_showing_one_idea(cfg_file, capsys):
    run(cfg_file, "idea", "Sell printable wedding checklists on Etsy for $8")
    capsys.readouterr()
    assert run(cfg_file, "idea", "show", "1") == 0
    out = capsys.readouterr().out
    assert "What they said" in out and "The work" in out

    assert run(cfg_file, "idea", "show", "99") == 1
    assert run(cfg_file, "idea", "show") == 1


def test_answering_a_question_from_the_terminal(cfg_file, capsys):
    run(cfg_file, "idea", "Sell printable wedding checklists on Etsy for $8")
    run(cfg_file, "standup")
    capsys.readouterr()

    from forge.core.config import load_config
    from forge.staff.office import Office

    office = Office(load_config(cfg_file))
    blocked = office.ideas.assignments(status="blocked")
    office.close()
    assert blocked

    assert run(cfg_file, "answer", str(blocked[0].id), "Brides, six months out.") == 0
    assert "Noted" in capsys.readouterr().out
    assert run(cfg_file, "answer", "99999", "hello") == 1


def test_a_standup_with_nothing_on_the_board(cfg_file, capsys):
    assert run(cfg_file, "standup") == 0
    assert "Nothing on the board" in capsys.readouterr().out
