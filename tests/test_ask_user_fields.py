"""Ask User shows only the settings that matter for the chosen Kind, each with one purpose; a new Number kind; the
answer is hidden only for the password kind.

Spec points:
1. A block setting can apply only when another setting has certain values (here: Ask User's Kind). Properties shows
   only the settings of the chosen kind and changes them at once when Kind changes; the documentation (Help >
   Documentation, which the docs site is built from) and the card shown on hover say which kinds a setting is for;
   values of hidden settings are not used by the run.
2. Prompt and Kind: every kind. Options: choice. Default: text, number, yes_no and choice, not password. Secret
   (labelled as the NAME of a password saved in Taskloom's secret store): password only; its help says unattended
   runs use it and that a name never saved makes the run fail.
3. Kind number: the question box only accepts a number; the answer is an int when whole, else a float, so an If after
   it can compare it (value > 10). A Default that is not a number is a validation problem naming the block and the
   setting. Unattended runs use the Default as a number.
4. Only a password answer is hidden; any other answer is never hidden, whatever else is filled in.
5. Password kind, unattended: the stored secret named in Secret; Secret empty, or named but never saved: a clean
   failure (no Python internals) saying what is missing; never the Default.
6. README says this in plain words.

Through the real `taskloom run` / `taskloom validate`, the real editor (Properties, the question box, the Log, the
run history) and taskloom.editor.docs.pages."""

import os
import re
import textwrap
from html import unescape
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QLabel, QLineEdit  # noqa: E402

from taskloom.block import setting_label  # noqa: E402
from taskloom.editor import docs  # noqa: E402
from taskloom.editor.dialogs import AskDialog  # noqa: E402
from taskloom.registry import discover  # noqa: E402
from tests.test_ask_no_answer import history, python_internals  # noqa: E402
from tests.test_docs_help import card_lines, dismiss, hover, property_rows, row_named  # noqa: E402
from tests.test_paths_editor_editing import clean, select_only, settle  # noqa: E402
from tests.test_paths_editor_running import click, log_text  # noqa: E402

README = Path(__file__).resolve().parents[1] / "README.md"
ASK_USER = "logic.ask_user"
KINDS = ("text", "password", "yes_no", "choice", "number")
# The settings Properties shows for each kind (spec 2).
SHOWN = {
    "text": {"prompt", "kind", "default"},
    "password": {"prompt", "kind", "secret"},
    "yes_no": {"prompt", "kind", "default"},
    "choice": {"prompt", "kind", "options", "default"},
    "number": {"prompt", "kind", "default"},
}
SECRET = "tok-Zx81-unseen"  # the value of the stored secret api_token, where a test saves it
# What says that a setting is about runs nobody can answer.
UNATTENDED = ("unattended", "scheduled", "nobody can be asked", "nobody can answer")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def save_secret(home: Path, name: str, value: str):
    """Save a secret as `taskloom secret set` does where there is no system credential store (the tests' case)."""
    write(home / "secrets.yaml", f"{name}: {value}\n").chmod(0o600)


def kinds_named(text: str) -> set[str]:
    """The Ask User kinds `text` names (yes_no also as yes/no)."""
    patterns = {kind: rf"\b{kind}\b" for kind in KINDS} | {"yes_no": r"\byes_no\b|\byes/no\b"}
    return {kind for kind, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)}


# --- Properties --------------------------------------------------------------------------------------------------

def shown_settings(win, cls) -> set[str]:
    """The settings of `cls` that Properties shows now: their rows, and any label of a setting left visible."""
    rows = property_rows(win, cls.title, cls)
    labels = {w.text() for w in win.properties.widget().findChildren(QLabel) if w.isVisible()}
    return {r.field[1] for r in rows if r.field} | {k for k, f in cls.config.items() if setting_label(k, f) in labels}


def choose_kind(win, cls, kind: str):
    """Choose `kind` in the Kind box of Properties, as a click on it does."""
    combo = row_named(win, setting_label("kind", cls.config["kind"]), cls=cls).inputs[0]
    assert isinstance(combo, QComboBox), combo
    index = combo.findText(kind)
    assert index >= 0, f"Kind offers no {kind!r}: {[combo.itemText(i) for i in range(combo.count())]}"
    combo.setCurrentIndex(index)
    combo.activated.emit(index)
    settle()


def ask_block_in_editor(win, tmp_path, kind: str):
    tab = win.open_flow(write(tmp_path / "ask.yaml", f"""
        blocks:
          ask:
            type: logic.ask_user
            config: {{prompt: "Password for the warehouse?", kind: {kind}}}
    """))
    select_only(tab, "ask")
    return tab, win.registry.get(ASK_USER)


def test_properties_shows_only_the_settings_of_the_chosen_kind_and_changes_them_at_once(win, tmp_path):
    """Spec 1 and 2, from the user's report: an Ask User of kind password shows Prompt, Kind and the stored secret's
    name, no Options and no Default; choosing another Kind shows and hides the settings at once, for every kind
    (number among them); the secret's label says it is the name of a stored secret."""
    tab, cls = ask_block_in_editor(win, tmp_path, "password")

    try:
        assert shown_settings(win, cls) == SHOWN["password"]
        secret_label = setting_label("secret", cls.config["secret"])
        assert "secret" in secret_label.lower() and "name" in secret_label.lower(), secret_label
        for kind in ("choice", "number", "yes_no", "text", "password"):
            choose_kind(win, cls, kind)
            assert (tab.doc.blocks["ask"].get("config") or {}).get("kind", "text") == kind
            assert shown_settings(win, cls) == SHOWN[kind], f"kind {kind}"
    finally:
        clean(win)  # (closing the window must not ask to save the kinds chosen)


def test_resting_the_mouse_on_ask_users_settings_says_which_kinds_they_are_for(win, qtbot, tmp_path):
    """Spec 1 (the setting help on hover) and spec 2 (Secret's help: what unattended runs use, and a name never saved
    makes the run fail; Default: the kinds it is for)."""
    _, cls = ask_block_in_editor(win, tmp_path, "password")

    try:
        secret = row_named(win, setting_label("secret", cls.config["secret"]), cls=cls)
        said = " ".join(card_lines(hover(qtbot, win, secret.label_widget, secret.label, "Ask User › Secret")))
        dismiss(qtbot, win)
        assert "password" in kinds_named(said), said
        assert any(w in said.lower() for w in UNATTENDED) and "fail" in said.lower(), said

        choose_kind(win, cls, "number")
        default = row_named(win, setting_label("default", cls.config["default"]), cls=cls)
        said = " ".join(card_lines(hover(qtbot, win, default.label_widget, default.label, "Ask User › Default")))
        dismiss(qtbot, win)
        assert {"text", "number", "yes_no", "choice"} <= kinds_named(said) and "kind" in said.lower(), said
    finally:
        clean(win)


# --- Help > Documentation and README -------------------------------------------------------------------------------

def documented_settings() -> dict[str, tuple[str, str]]:
    """Ask User's setting entries in the documentation: {key: (heading, text)}."""
    page = docs.pages(discover(None))[docs.category_page("Logic")]
    entries = re.findall(rf'<h3 id="{re.escape(ASK_USER)}\.(\w+)">(.*?)</h3>(.*?)(?=<h[123][ >]|$)', page, re.S)
    plain = lambda h: " ".join(unescape(re.sub(r"<[^>]+>", " ", h)).split())  # noqa: E731
    return {key: (plain(heading), plain(body)) for key, heading, body in entries}


def test_the_documentation_says_which_kinds_each_ask_user_setting_is_for():
    """Spec 1 (Help > Documentation, from which the docs site is built), 2 and 3: Kind offers number; Options is for
    choice; Default for text, number, yes_no and choice; the stored secret's name for password, its help saying
    unattended runs use it and a name never saved makes the run fail."""
    found = documented_settings()

    assert set(found) == {"prompt", "kind", "options", "default", "secret"}, found
    assert "number" in kinds_named(found["kind"][1]), found["kind"]
    options, default, secret = found["options"][1], found["default"][1], found["secret"]
    assert "choice" in kinds_named(options) and "kind" in options.lower(), options
    assert {"text", "number", "yes_no", "choice"} <= kinds_named(default) and "kind" in default.lower(), default
    heading, text = secret
    assert "secret" in heading.lower() and "name" in heading.lower(), heading
    assert "password" in kinds_named(text) and "kind" in text.lower(), text
    assert any(w in text.lower() for w in UNATTENDED) and "fail" in text.lower(), text


def test_readme_tells_about_ask_users_kinds_and_settings():
    """Spec 6: what README says of Ask User (its paragraphs and list items naming it) names every kind, number
    among them, and its settings Options, Default and the stored secret, as Properties labels it."""
    text = README.read_text(encoding="utf-8")
    passages = [p for p in re.split(r"\n\s*\n|\n(?=\s*[-*|] )", text) if "Ask User" in p]
    said = " ".join(" ".join(passages).split()).lower()
    cls = discover(None).get(ASK_USER)
    secret = re.sub(r"\s*\(.*?\)", "", setting_label("secret", cls.config["secret"])).lower()

    assert kinds_named(said) == set(KINDS), passages
    for setting in ("options", "default", secret):
        assert setting in said, (setting, passages)


# --- Unattended runs (`taskloom run`) ------------------------------------------------------------------------------

def number_flow(path: Path, config: str, manual: bool = False) -> Path:
    """Ask User `count` (its config `config`), then an If `value > 10`: `big` or `small` writes the answer it got,
    with its type, to path.txt."""
    start = "manual: {type: trigger.manual}" if manual else ""
    edge = "- manual.start -> count.after" if manual else ""
    return write(path, f"""
        blocks:
          {start}
          count:
            type: logic.ask_user
            config: {config}
          check:
            type: logic.if
            config: {{condition: "value > 10"}}
          big:
            type: logic.python
            config:
              inputs: [v]
              outputs: []
              code: |
                open('path.txt', 'w', encoding='utf-8').write(f'big {{type(v).__name__}} {{v!r}}')
          small:
            type: logic.python
            config:
              inputs: [v]
              outputs: []
              code: |
                open('path.txt', 'w', encoding='utf-8').write(f'small {{type(v).__name__}} {{v!r}}')
        edges:
          - count.answer -> check.value
          - check.true -> big.v
          - check.false -> small.v
          {edge}
    """)


@pytest.mark.parametrize("default, got", [
    ("'12'", "big int 12"),
    ("'7'", "small int 7"),
    ("'2.5'", "small float 2.5"),
    ("'10.5'", "big float 10.5"),
    ("'4.0'", "small int 4"),
    ("12", "big int 12"),
], ids=["whole-big", "whole-small", "decimal-small", "decimal-big", "whole-written-with-a-point", "yaml-number"])
def test_a_number_ask_user_run_nobody_can_answer_gives_its_default_as_a_number_to_an_if(env, default, got):
    """Spec 3: unattended runs use the Default as a number, an int when whole, else a float; the If after it compares
    it with value > 10, without casting."""
    flow = number_flow(env.root / "flow.yaml", f"{{prompt: 'How many rows?', kind: number, default: {default}}}")

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (env.root / "path.txt").read_text(encoding="utf-8") == got


@pytest.mark.parametrize("default", ["many", "12 apples"])
def test_a_number_ask_users_default_that_is_not_a_number_is_a_problem_naming_the_block_and_the_setting(env, default):
    """Spec 3: Validate and runs refuse it, saying which block and which setting."""
    flow = number_flow(env.root / "flow.yaml", f"{{prompt: 'How many rows?', kind: number, default: '{default}'}}")
    label = setting_label("default", discover(None).get(ASK_USER).config["default"])

    checked = env.taskloom("validate", flow)
    ran = env.taskloom("run", flow)

    assert checked.returncode == 2, checked.stdout + checked.stderr
    assert any("count" in line and label in line for line in checked.stderr.splitlines()), checked.stderr
    assert "Traceback" not in checked.stderr
    assert ran.returncode == 2 and not (env.root / "path.txt").exists(), ran.stdout + ran.stderr


def answer_flow(path: Path, config: str) -> Path:
    """Ask User `ask` (its config `config`): Inspect shows its answer as "got ...", Python Code writes its repr."""
    return write(path, f"""
        blocks:
          ask:
            type: logic.ask_user
            config: {config}
          show:
            type: logic.inspect
            config: {{message: "got {{value}}"}}
          save:
            type: logic.python
            config:
              inputs: [answer]
              outputs: []
              code: |
                open('answer.txt', 'w', encoding='utf-8').write(repr(answer))
        edges:
          - ask.answer -> show.value
          - ask.answer -> save.answer
    """)


@pytest.mark.parametrize("config, answer, shown", [
    ("{prompt: 'Region?', kind: text, default: North, secret: api_token}", "'North'", "got North"),
    ("{prompt: 'How many?', kind: number, default: '7', secret: api_token}", "7", "got 7"),
    ("{prompt: 'Go on?', kind: yes_no, default: 'yes', secret: api_token}", "True", "got True"),
    ("{prompt: 'Region?', kind: choice, options: [North, South], default: South, secret: api_token}", "'South'",
     "got South"),
], ids=["text", "number", "yes_no", "choice"])
def test_a_secret_filled_in_for_another_kind_is_not_used_and_the_answer_is_not_hidden(env, config, answer, shown):
    """Spec 1 (the value of a setting hidden for the kind is not used by the run), spec 4 (only a password answer is
    hidden, whatever else is filled in) and spec 2 (Default is what runs nobody can answer use for these kinds): with
    a stored secret named in Secret, an unattended run gives the Default, shown as it is in the Log."""
    save_secret(env.home, "api_token", SECRET)

    result = env.taskloom("run", answer_flow(env.root / "flow.yaml", config))

    assert result.returncode == 0, result.stdout + result.stderr
    run_id, status = env.last_run()
    assert status == "success"
    assert (env.root / "answer.txt").read_text(encoding="utf-8") == answer
    _, logs = history(env.home, run_id)
    assert shown in logs and "got ***" not in logs, logs


def test_a_password_ask_user_run_nobody_can_answer_uses_its_stored_secret_hidden_never_the_default(env):
    """Spec 5 (the stored secret named in Secret, never the Default) and spec 4 (a password answer stays hidden).
    Passes today: protects what the change keeps."""
    save_secret(env.home, "api_token", SECRET)
    flow = write(env.root / "flow.yaml", f"""
        blocks:
          ask:
            type: logic.ask_user
            config: {{prompt: 'Password?', kind: password, default: other-pass, secret: api_token}}
          show:
            type: logic.inspect
            config: {{message: "got {{value}}"}}
          use:
            type: logic.python
            config:
              inputs: [answer]
              outputs: []
              code: |
                open('used.txt', 'w', encoding='utf-8').write(str(answer == '{SECRET}'))
        edges:
          - ask.answer -> show.value
          - ask.answer -> use.answer
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stdout + result.stderr
    run_id, status = env.last_run()
    assert status == "success"
    assert (env.root / "used.txt").read_text(encoding="utf-8") == "True"  # the stored secret, not the Default
    _, logs = history(env.home, run_id)
    assert "got ***" in logs, logs
    assert SECRET not in logs + result.stdout + result.stderr


@pytest.mark.parametrize("secret, says", [
    ("", [r"nobody can answer", r"secret"]),
    (", secret: never_saved", [r"never_saved", r"not (saved|set|stored)", r"secret set never_saved"]),
], ids=["no-secret", "secret-never-saved"])
def test_a_password_ask_user_run_without_its_stored_secret_fails_cleanly_and_never_uses_the_default(env, secret, says):
    """Spec 5: Secret empty: a clean failure saying nobody can answer and a stored secret is needed; Secret named but
    never saved: a clean failure saying that secret is not saved and how to save it. Never the Default."""
    flow = answer_flow(env.root / "flow.yaml", f"{{prompt: 'Password?', kind: password, default: fallback-pass{secret}}}")

    result = env.taskloom("run", flow)

    assert result.returncode == 1, result.stdout + result.stderr
    run_id, status = env.last_run()
    assert status == "failed"
    blocks, logs = history(env.home, run_id)
    (state, error, _), saved = blocks["ask"], blocks["save"][0]
    assert (state, saved) == ("failed", "skipped"), blocks
    assert not (env.root / "answer.txt").exists()  # the Default was not used
    for pattern in says:
        assert re.search(pattern, error, re.IGNORECASE), (pattern, error)
    printed = result.stdout + result.stderr
    assert python_internals(error) == [] and python_internals(logs) == [] and python_internals(printed) == [], \
        (error, logs, printed)


# --- In the editor -------------------------------------------------------------------------------------------------

def asked(qtbot, win, tab) -> AskDialog:
    """The question on screen once the run asks (a run that ends without asking fails the test at once)."""
    def asking_or_over():  # (a run refused at once says why in a message box)
        return any(d.isVisible() for d in win.findChildren(AskDialog)) or bool(win.message_boxes) or bool(
            tab.runs and not tab.running and tab.runs[-1].status is not None)

    qtbot.waitUntil(asking_or_over, timeout=60_000)
    shown = [d for d in win.findChildren(AskDialog) if d.isVisible()]
    assert shown, f"the run ended without asking:\n{log_text(win)}\nmessages: {win.message_boxes}"
    return shown[0]


def answer_box(dialog):
    """The box the answer is typed in."""
    return dialog.findChild(QAbstractSpinBox) or dialog.findChild(QLineEdit)


def type_answer(dialog, text: str) -> str:
    """Type `text` into the empty question box, key by key; what the box holds then."""
    box = answer_box(dialog)
    box.clear()
    QTest.keyClicks(box, text)
    settle()
    return box.text()


def finished(qtbot, tab) -> int:
    qtbot.waitUntil(lambda: bool(tab.runs and not tab.running and tab.runs[-1].status is not None), timeout=60_000)
    return tab.runs[-1].run_id


@pytest.mark.parametrize("typed, got", [("12", "big int 12"), ("2.5", "small float 2.5")], ids=["whole", "decimal"])
def test_a_number_typed_in_the_question_box_reaches_the_if_after_it_as_a_number(win, qtbot, tmp_path, typed, got):
    """Spec 3, the user's scenario: Manual Start, Ask User of kind number, an If value > 10. The number you type is
    an int when whole, else a float, and the If compares it without casting."""
    tab = win.open_flow(number_flow(tmp_path / "rows.yaml", "{prompt: 'How many rows?', kind: number}", manual=True))
    win.run_flow(tab)

    dialog = asked(qtbot, win, tab)
    type_answer(dialog, typed)
    click(dialog, "OK")
    finished(qtbot, tab)

    assert tab.runs[-1].status == "success", log_text(win)
    assert (tmp_path / "path.txt").read_text(encoding="utf-8") == got


def test_the_question_box_of_a_number_ask_user_only_accepts_a_number(win, qtbot, tmp_path):
    """Spec 3: letters typed in the box are not taken: the box refuses them as they are typed, or OK does not accept
    the answer and the question stays; the number typed then is the answer."""
    tab = win.open_flow(number_flow(tmp_path / "rows.yaml", "{prompt: 'How many rows?', kind: number}", manual=True))
    win.run_flow(tab)

    dialog = asked(qtbot, win, tab)
    held = type_answer(dialog, "4x2")
    click(dialog, "OK")
    settle()
    if "x" in held:  # the box held the letter: OK must not take it
        assert dialog.isVisible(), "OK took the answer '4x2'"
        type_answer(dialog, "42")
        click(dialog, "OK")
    finished(qtbot, tab)

    assert tab.runs[-1].status == "success", log_text(win)
    assert (tmp_path / "path.txt").read_text(encoding="utf-8") == "big int 42"


def test_a_text_answer_typed_in_the_editor_shows_in_the_log_even_with_a_secret_filled_in(win, qtbot, tmp_path):
    """Spec 4 (a text answer is never hidden, whatever else is filled in) and spec 1 (Secret, hidden for kind text,
    is not used): the answer shows as typed in the Log and in the run history."""
    tab = win.open_flow(write(tmp_path / "region.yaml", """
        blocks:
          manual:
            type: trigger.manual
          ask:
            type: logic.ask_user
            config: {prompt: "Which region?", kind: text, secret: api_token}
          use:
            type: logic.python
            config: {inputs: [region], outputs: [], code: "print('using', region)"}
        edges:
          - manual.start -> ask.after
          - ask.answer -> use.region
    """))
    win.run_flow(tab)

    dialog = asked(qtbot, win, tab)
    type_answer(dialog, "North-77")
    click(dialog, "OK")
    run_id = finished(qtbot, tab)

    assert tab.runs[-1].status == "success", log_text(win)
    assert "using North-77" in log_text(win), log_text(win)
    _, logs = history(win.home.root, run_id)
    assert "using North-77" in logs, logs
