"""Named viewports.

A preset is shorthand for a size and nothing more. These tests hold two properties: a name
resolves to the same pixels everywhere it is accepted, and the name never survives into the
artifacts, because a run has to record the size it was actually measured at.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from journey_evals import cli, contracts
from journey_evals.contracts import (
    VIEWPORT_PRESETS,
    ContractError,
    effective_spec,
    resolve_viewport,
    viewport_class,
    viewport_names,
)

JOURNEY = {
    "schema_version": 1,
    "id": "demo",
    "mode": "verify",
    "task": "Do the declared thing.",
    "url": "http://127.0.0.1:8111/",
    "facts": {},
    "checks": [],
    "acceptance": {"url_path_is": "/done"},
}


def test_every_preset_is_a_usable_size():
    for name, size in VIEWPORT_PRESETS.items():
        assert resolve_viewport(name) == size, name
        assert 320 <= size[0] <= 3840 and 400 <= size[1] <= 2160, name


def test_presets_are_listed_narrowest_first():
    names = viewport_names()
    widths = [VIEWPORT_PRESETS[name] for name in names]
    assert widths == sorted(widths)
    assert set(names) == set(VIEWPORT_PRESETS)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("mobile", (390, 844)),
        ("MOBILE", (390, 844)),
        ("  desktop  ", (1440, 900)),
        ("390x844", (390, 844)),
        ("1120X780", (1120, 780)),
        ({"width": 800, "height": 600}, (800, 600)),
        ((800, 600), (800, 600)),
        ([800, 600], (800, 600)),
    ],
)
def test_accepted_spellings(value, expected):
    assert resolve_viewport(value) == expected


@pytest.mark.parametrize("value", ["phone", "", "1120", "1120x", "x780", "big", "1120*780*2"])
def test_a_name_that_is_not_a_preset_is_refused_with_the_list(value):
    with pytest.raises(ContractError) as error:
        resolve_viewport(value)
    if value and "x" not in value and "*" not in value:
        assert "mobile" in str(error.value), "the error must name the presets that do exist"


@pytest.mark.parametrize("value", [(100, 600), (800, 100), (5000, 600), (800, 5000)])
def test_a_size_outside_the_supported_range_is_refused(value):
    with pytest.raises(ContractError):
        resolve_viewport(value)


def test_a_journey_may_declare_a_preset_by_name(tmp_path):
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**JOURNEY, "viewport": "mobile"}), encoding="utf-8")
    spec, provenance = effective_spec(journey_file=str(path), mode="verify")
    assert spec.viewport == (390, 844)
    # The name is shorthand; only the resolved size is recorded.
    assert provenance["resolved"]["viewport"] == {"width": 390, "height": 844}


def test_a_preset_on_the_command_line_agrees_with_the_same_preset_in_the_file(tmp_path):
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**JOURNEY, "viewport": "mobile"}), encoding="utf-8")
    spec, _ = effective_spec(journey_file=str(path), viewport=resolve_viewport("mobile"))
    assert spec.viewport == (390, 844)


def test_a_preset_that_contradicts_the_file_is_still_refused(tmp_path):
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**JOURNEY, "viewport": "mobile"}), encoding="utf-8")
    with pytest.raises(ContractError, match="not declared by this journey"):
        effective_spec(journey_file=str(path), viewport=resolve_viewport("desktop"))


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((360, 640), "phone"),
        ((390, 844), "phone"),
        ((430, 932), "phone"),
        ((519, 800), "phone"),
        ((834, 1112), "tablet"),
        ((899, 700), "tablet"),
        ((1120, 780), "desktop"),
        ((1920, 1080), "desktop"),
    ],
)
def test_a_viewport_is_classified_by_the_width_it_actually_used(size, expected):
    assert viewport_class(size) == expected


def test_the_class_of_a_custom_size_follows_the_same_rule_as_a_named_one():
    assert viewport_class(resolve_viewport("mobile")) == viewport_class((390, 844))
    assert viewport_class((412, 915)) == "phone"


def test_the_cli_accepts_a_preset_name():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--journey", "j.json", "--viewport", "mobile"])
    assert args.viewport == [[(390, 844)]]
    assert cli._requested_viewports(args) == [(390, 844)]


def test_the_cli_rejects_an_unknown_preset_and_says_what_exists(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--journey", "j.json", "--viewport", "phablet"])
    assert "mobile" in capsys.readouterr().err


def test_the_help_text_lists_every_preset_with_its_size():
    help_text = cli._viewport_help()
    for name, (width, height) in VIEWPORT_PRESETS.items():
        assert f"{name} ({width}x{height})" in help_text


# -- a journey may declare several sizes, and then it is a matrix -------------------------------


def test_a_journey_may_declare_a_list_of_viewports(tmp_path):
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**JOURNEY, "viewport": ["desktop", "mobile"]}), encoding="utf-8")
    assert contracts.declared_viewports(str(path)) == ((1440, 900), (390, 844))


def test_a_two_integer_viewport_is_still_one_size_not_two():
    """A journey written before matrices existed must keep its meaning."""
    assert contracts.resolve_viewports([390, 844]) == ((390, 844),)
    assert contracts.resolve_viewports({"width": 390, "height": 844}) == ((390, 844),)


def test_a_declared_list_keeps_its_order_and_drops_repeats():
    assert contracts.resolve_viewports(["mobile", "desktop", "mobile"]) == ((390, 844), (1440, 900))
    assert contracts.resolve_viewports(["mobile", "390x844"]) == ((390, 844),)


def test_an_empty_viewport_list_is_refused():
    with pytest.raises(ContractError, match="at least one"):
        contracts.resolve_viewports([])


def test_nothing_requested_runs_every_declared_size():
    declared = ((1440, 900), (390, 844))
    assert contracts.select_viewports(declared, []) == declared


def test_a_requested_size_selects_just_that_one():
    declared = ((1440, 900), (390, 844))
    assert contracts.select_viewports(declared, [(390, 844)]) == ((390, 844),)


def test_requesting_a_size_the_journey_does_not_declare_is_refused_and_lists_what_exists():
    declared = ((1440, 900), (390, 844))
    with pytest.raises(ContractError) as raised:
        contracts.select_viewports(declared, [(834, 1112)])
    message = str(raised.value)
    assert "834x1112" in message and "desktop" in message and "mobile" in message


def test_one_cell_of_a_matrix_records_the_whole_matrix_it_came_from(tmp_path):
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**JOURNEY, "viewport": ["desktop", "mobile"]}), encoding="utf-8")
    spec, provenance = effective_spec(journey_file=str(path), viewport=(390, 844))
    assert spec.viewport == (390, 844)
    assert spec.viewports == ((1440, 900), (390, 844))
    assert provenance["resolved"]["viewports"] == [{"width": 1440, "height": 900},
                                                   {"width": 390, "height": 844}]


def test_two_cells_of_one_matrix_are_different_measurements(tmp_path):
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**JOURNEY, "viewport": ["desktop", "mobile"]}), encoding="utf-8")
    wide, _ = effective_spec(journey_file=str(path), viewport=(1440, 900))
    narrow, _ = effective_spec(journey_file=str(path), viewport=(390, 844))
    assert wide.sha256 != narrow.sha256


def test_declaring_one_size_does_not_change_the_specification_hash(tmp_path):
    """A matrix key appears only for a matrix, so existing artifacts stay comparable."""
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**JOURNEY, "viewport": "mobile"}), encoding="utf-8")
    spec, provenance = effective_spec(journey_file=str(path), viewport=(390, 844))
    assert "viewports" not in provenance["resolved"]
    plain, _ = effective_spec(journey_file=str(path))
    assert spec.sha256 == plain.sha256


def test_viewport_label_names_a_preset_and_falls_back_to_pixels():
    assert contracts.viewport_label((390, 844)) == "mobile"
    assert contracts.viewport_label((412, 915)) == "412x915"


# -- the command line -----------------------------------------------------------------------


@pytest.mark.parametrize("text", ["desktop,mobile", "[desktop,mobile]", "[desktop, mobile]"])
def test_the_cli_accepts_a_list_of_viewports(text):
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--journey", "j.json", "--viewport", text])
    assert cli._requested_viewports(args) == [(1440, 900), (390, 844)]


def test_the_viewport_flag_may_be_repeated():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--journey", "j.json",
                              "--viewport", "desktop", "--viewport", "mobile"])
    assert cli._requested_viewports(args) == [(1440, 900), (390, 844)]


def test_a_repeated_viewport_is_only_run_once():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--journey", "j.json",
                              "--viewport", "mobile", "--viewport", "390x844"])
    assert cli._requested_viewports(args) == [(390, 844)]


def test_the_cli_plans_every_declared_size_when_none_is_requested(tmp_path):
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**JOURNEY, "viewport": ["desktop", "mobile"]}), encoding="utf-8")
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--journey", str(path)])
    assert cli._planned_viewports(args) == [(1440, 900), (390, 844)]


def test_the_cli_plans_only_what_was_requested(tmp_path):
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**JOURNEY, "viewport": ["desktop", "mobile"]}), encoding="utf-8")
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--journey", str(path), "--viewport", "mobile"])
    assert cli._planned_viewports(args) == [(390, 844)]


def test_the_help_text_says_the_default_is_every_declared_size():
    assert "every size the journey declares" in cli._viewport_help()


def _watch_payload(monkeypatch, argv):
    """Run `watch` far enough to see what it would put on screen, without serving anything."""
    from journey_evals import console

    captured = {}

    def fake_serve(session, *, port, journey, open_browser=True):
        captured["session"] = session
        captured["journey"] = journey
        return 0

    monkeypatch.setattr(console, "serve", fake_serve)
    args = cli.build_parser().parse_args(argv)
    assert cli.command_watch(args) == 0
    return captured


def test_watch_queues_one_cell_per_declared_viewport(tmp_path, monkeypatch):
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**JOURNEY, "viewport": ["desktop", "mobile"]}), encoding="utf-8")
    captured = _watch_payload(monkeypatch, ["watch", "--journey", str(path), "--no-open",
                                            "--out", str(tmp_path / "out")])
    session = captured["session"]
    assert [cell["label"] for cell in session.cells] == ["desktop", "mobile"]
    # Each cell is an independent run with its own directory, so nothing is overwritten.
    assert session.cells[0]["session"].directory == tmp_path / "out" / "desktop"
    assert session.cells[1]["session"].directory == tmp_path / "out" / "mobile"
    assert "390x844" in session.cells[1]["session"].command
    assert [c["label"] for c in captured["journey"]["viewports"]] == ["desktop", "mobile"]


def test_watch_of_a_single_viewport_still_writes_straight_to_the_run_directory(tmp_path,
                                                                              monkeypatch):
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**JOURNEY, "viewport": ["desktop", "mobile"]}), encoding="utf-8")
    captured = _watch_payload(monkeypatch, ["watch", "--journey", str(path), "--no-open",
                                            "--viewport", "mobile", "--out", str(tmp_path / "o")])
    session = captured["session"]
    assert len(session.cells) == 1
    assert session.cells[0]["session"].directory == tmp_path / "o"


def test_watch_refuses_a_viewport_the_journey_does_not_declare(tmp_path, monkeypatch, capsys):
    path = tmp_path / "journey.json"
    path.write_text(json.dumps({**JOURNEY, "viewport": ["desktop", "mobile"]}), encoding="utf-8")
    args = cli.build_parser().parse_args(["watch", "--journey", str(path), "--no-open",
                                          "--viewport", "tablet"])
    assert cli.command_watch(args) == cli.CONFIGURATION_EXIT
    error = capsys.readouterr().err
    assert "834x1112 is not declared" in error and "mobile (390x844)" in error


# -- the matrix summary ------------------------------------------------------------------------


def test_the_matrix_summary_names_every_cell_and_what_failed():
    cells = [
        {"viewport": {"width": 1120, "height": 780}, "label": "standard", "result": "PASS",
         "goal_status": "verified", "failed_required_checks": [], "exit_code": 0},
        {"viewport": {"width": 390, "height": 844}, "label": "mobile", "result": "FAIL",
         "goal_status": "violated", "failed_required_checks": ["confirm-action-usable"],
         "exit_code": 1},
    ]
    text = cli._matrix_summary(cells)
    assert "standard" in text and "1120x780" in text and "PASS" in text
    assert "mobile" in text and "390x844" in text and "confirm-action-usable" in text


def test_a_matrix_result_is_never_collapsed_into_one_verdict():
    """Passing on a desktop and failing on a phone is a finding, not an average."""
    cells = [
        {"viewport": {"width": 1120, "height": 780}, "label": "standard", "result": "PASS",
         "goal_status": "verified", "failed_required_checks": [], "exit_code": 0},
        {"viewport": {"width": 390, "height": 844}, "label": "mobile", "result": "FAIL",
         "goal_status": "violated", "failed_required_checks": ["x"], "exit_code": 1},
    ]
    worst = max(cell["exit_code"] for cell in cells)
    assert worst == 1
    assert cli._matrix_summary(cells).count("\n") == len(cells) + 1


def test_a_missing_journey_file_names_the_bundled_one_with_the_same_name(tmp_path, monkeypatch):
    monkeypatch.chdir(pathlib.Path(__file__).resolve().parents[1])
    with pytest.raises(ContractError) as raised:
        contracts.declared_viewports("flight-booking-matrix.json")
    message = str(raised.value)
    assert "no journey file at" in message
    assert "examples" in message and "flight-booking-matrix.json" in message


def test_a_missing_journey_file_with_no_bundled_match_just_says_so(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ContractError, match="no journey file at"):
        contracts.declared_viewports("nowhere.json")
