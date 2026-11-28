"""Credentials on the PC (docs/promises/credentials.yaml): the last of what the window Tools > Credentials… does to the
open flows on Save (CR-16) and to a card deleted while one of its keys is typed over (CR-1), in the real editor
(offscreen), as tests/test_credentials_window.py drives it (its helpers, its watch of the boxes, the contract in its
docstring) and tests/test_credentials_window_actions.py types without leaving a box. Made-up values only.

What the tests take as given, beside those contracts:
- On Save, an open flow is changed only where it names a card or key renamed with "Also rewrite the open flows"; a
  reference to a card that is not in the list, or that was added in the window, names no card the window renamed.
- A reference may be written with spaces inside its braces, {{ secret: UAT.password }}: it names UAT.password, and
  it stays as written unless what it names is renamed with the rewrite.
- A flow file may hold a setting its block does not have (written by hand, or by another version of Taskloom): the
  editor opens it and saves it back in the same order, and the rest of that block's settings are read and rewritten
  as any other block's.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import test_credentials_window as W  # noqa: E402
from test_credentials_store import card_entries  # noqa: E402
from test_credentials_window_actions import typing  # noqa: E402

# tests/test_credentials_window.py's fixtures, used here by name: its watch of the boxes (for every test) and the open
# flows that use the card UAT.
watch = W.watch
open_flows = W.open_flows

UAT_HOST, UAT_PASSWORD, PROD_PASSWORD = W.UAT_HOST, W.UAT_PASSWORD, W.PROD_PASSWORD


def rename_card(window, card: str, box: list):
    """`card` chosen and renamed (the name asked is given by W.asked_names), the question about the open flows
    answered as the test said; then Save."""
    W.choose(window, card)
    W.press(window, "Rename…")
    W.wait_for(lambda: box, 10, f"renaming {card}, which an open flow uses, showed no question")
    W.press(window, "Save")


# --- on Save, the open flows change only where the rewrite renames (CR-16) -----------------------------------------

@pytest.mark.invariant("CR-16")
def test_a_save_leaves_a_flow_naming_a_card_not_listed_or_added_in_the_window_and_rewrites_the_flow_after_it(
        win, monkeypatch, tmp_path, credential_store):
    """Open first: lost_flow, whose note names STAGE.token (STAGE is added in the window) and GHOST.password (no
    card GHOST); then uat_flow, naming UAT.password. STAGE added with a row, UAT renamed TEST with Also rewrite, Save:
    no message; lost_flow as written, with no unsaved change; uat_flow names TEST.password, as an unsaved change."""
    W.cards(win, {"UAT": {"rows": {"password": None}}}, {"UAT.password": UAT_PASSWORD})
    lost_text = "{{secret:STAGE.token}} and {{secret:GHOST.password}}"
    lost = win.open_flow(W.flow_file(tmp_path / "lost_flow.yaml", {"lost_note": W.note(lost_text)}))
    uat = win.open_flow(W.flow_file(tmp_path / "uat_flow.yaml", {"uat_note": W.note("{{secret:UAT.password}}")}))
    assert lost and uat
    W.asked_names(monkeypatch, "STAGE", "TEST")
    box = W.answer_next_box("rewrite")

    def steps(window):
        W.press(window, "Add…")
        W.add_row(window, "token", "made-up-token-41")
        rename_card(window, "UAT", box)

    W.open_credentials(win, steps)

    assert W.messages(win) == []
    assert "uat_flow.yaml: uat_note" in box[0] and "lost_flow" not in box[0], box
    assert (W.unsaved(win, lost), W.unsaved(win, uat)) == (False, True)
    assert W.save_tab(win, lost)["lost_note"]["config"]["message"] == lost_text
    assert W.save_tab(win, uat)["uat_note"]["config"]["message"] == "{{secret:TEST.password}}"
    assert card_entries(credential_store) == {"TEST.password": UAT_PASSWORD, "STAGE.token": "made-up-token-41"}


@pytest.mark.invariant("CR-16")
def test_a_reference_written_with_spaces_stays_as_written_after_a_save_renaming_nothing_or_another_card(
        win, monkeypatch, tmp_path):
    """{{ secret: UAT.password }} (spaces inside the braces) in uat_flow's note and in both_flow's, beside
    {{secret:PROD.password}}. A Save renaming nothing: no flow changes. Then PROD renamed LIVE with Also rewrite, and
    Save: uat_flow has no unsaved change and its note is as written; both_flow names LIVE.password and keeps its UAT
    reference as written."""
    W.cards(win, {"UAT": {"rows": {"password": None}}, "PROD": {"rows": {"password": None}}},
            {"UAT.password": UAT_PASSWORD, "PROD.password": PROD_PASSWORD})
    spaced = "{{ secret: UAT.password }}"
    only_uat = win.open_flow(W.flow_file(tmp_path / "uat_flow.yaml", {"uat_note": W.note(spaced)}))
    both = win.open_flow(W.flow_file(tmp_path / "both_flow.yaml", {"prod_note": W.note("{{secret:PROD.password}}"),
                                                                    "uat_note": W.note(spaced)}))
    assert only_uat and both

    W.open_credentials(win, lambda window: W.press(window, "Save"))

    assert (W.unsaved(win, only_uat), W.unsaved(win, both)) == (False, False)

    W.asked_names(monkeypatch, "LIVE")
    box = W.answer_next_box("rewrite")
    W.open_credentials(win, lambda window: rename_card(window, "PROD", box))

    assert W.messages(win) == []
    assert "both_flow.yaml: prod_note" in box[0] and "uat_flow" not in box[0], box
    assert (W.unsaved(win, only_uat), W.unsaved(win, both)) == (False, True)
    assert W.save_tab(win, only_uat)["uat_note"]["config"]["message"] == spaced
    saved = W.save_tab(win, both)
    assert {name: block["config"]["message"] for name, block in saved.items() if name != "manual"} == {
        "prod_note": "{{secret:LIVE.password}}", "uat_note": spaced}


@pytest.mark.invariant("CR-16")
def test_a_block_holding_a_setting_it_does_not_have_before_a_reference_is_named_by_the_question_and_rewritten(
        win, monkeypatch, tmp_path):
    """odd_flow's API Request holds first a setting colour that API Request does not have, then its URL naming
    {{secret:UAT.password}}: the editor opens it; UAT renamed TEST, the question names odd_flow and the block, and with
    Also rewrite and Save the URL names TEST.password, as an unsaved change; the setting colour is kept."""
    W.cards(win, {"UAT": {"rows": {"password": None}}}, {"UAT.password": UAT_PASSWORD})
    odd = {"type": "servers.api_request",
           "config": {"colour": "blue", "url": "https://orders.example.test/{{secret:UAT.password}}"}}
    tab = win.open_flow(W.flow_file(tmp_path / "odd_flow.yaml", {"odd_call": odd}))
    assert tab
    W.asked_names(monkeypatch, "TEST")
    box = W.answer_next_box("rewrite")

    W.open_credentials(win, lambda window: rename_card(window, "UAT", box))

    assert "\n\nodd_flow.yaml: odd_call\n\n" in box[0], box
    assert W.unsaved(win, tab)
    saved = W.save_tab(win, tab)["odd_call"]["config"]
    assert list(saved.items()) == [("colour", "blue"), ("url", "https://orders.example.test/{{secret:TEST.password}}")]


# --- a card deleted while one of its keys is being typed over (CR-1) -----------------------------------------------

@pytest.mark.invariant("CR-1")
def test_the_last_card_deleted_while_one_of_its_keys_is_typed_over_goes_with_no_question_or_message(
        win, credential_store, open_flows):
    """UAT2, the last card, chosen; its key token, which an open flow uses, typed over as tok_2 without leaving the
    box; then Delete clicked as a click on macOS does it, leaving the focus in that box: UAT2 goes and UAT's rows show,
    with no question about the open flows and no message (the typing went with its card). Save: the list holds UAT
    only, the store UAT's values only, and the open flows are unchanged."""
    _, tabs = open_flows

    def steps(window):
        W.choose(window, "UAT2")
        typing(W.key_box(W.row(window, "token")), "tok_2")
        W.press(window, "Delete")
        assert W.messages(win) == []
        assert (W.card_names(window), W.keys(window)) == (["UAT"], ["host", "password", ""])
        W.press(window, "Save")

    W.open_credentials(win, steps)

    assert W.cards_of(win) == [("UAT", None, [("host", None), ("password", None)])]
    assert card_entries(credential_store) == {"UAT.host": UAT_HOST, "UAT.password": UAT_PASSWORD}
    assert not any(W.unsaved(win, tab) for tab in tabs.values())
