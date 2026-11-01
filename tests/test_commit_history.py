"""File → Commit History…: a window of its own with the flow's Git history. On its left a search field (".*" regular
expressions, on when it opens; "Aa" match case, off) over the commits' messages, then the outputs of the latest run
of each version, then the code of its blocks, then anything else in its flow file; under it the commit cards, newest
first (message, author date and time, short id), Up and Down moving through them. On its right a read-only preview of
the flow as it was at the selected commit, with the outputs of the latest run made with exactly that version (a run
records the Git blob id of the bytes it loaded). Restore this version puts that version into the flow's tab as one
undoable change; File → Save and File → Save & Commit… then keep it. Each test names the point of the specification
(1-10) it protects.

Driven through the real editor (the `win` fixture), the real `taskloom run` processes (the `env` fixture, the same
Taskloom folder) and their run history. Git histories are made, and what Taskloom committed is checked, with the real
git program; every repository and the Git config are the test's own.

The window is read as you would read it: a window (not the editor's) titled with the flow's file name; its search
field (the one text field outside the preview) with the ".*" and "Aa" buttons; the list of cards (its one item view:
each row a card, its text the row's text or that of the labels of the row's widget, the selected card the selected
row); the preview (its one QGraphicsView, drawing the canvas's own block, connection and text items); the line above
the preview; the "Restore this version" button."""

import copy
import datetime as dt
import hashlib
import html
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QModelIndex, QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QGuiApplication, QHelpEvent, QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QApplication, QGraphicsView, QLabel,  # noqa: E402
                               QLineEdit, QListView, QMenu, QTableView, QToolTip, QTreeView)

from taskloom.editor.canvas import EdgeItem, NodeItem, NoteItem  # noqa: E402
from taskloom.flow import real_path  # noqa: E402
from tests.test_find_bar import red_pixels  # noqa: E402
from tests.test_paths_editor_editing import clean, menu, save_as, when_dialog_shows  # noqa: E402
from tests.test_save_and_commit import (ANA, GIT, OK, SAVE_AND_COMMIT, click, count, fill, git,  # noqa: E402
                                        head, inside_a_repository, lines, needs_git, new_repo, shown, snapshot,
                                        subjects)
from tests.test_theme import choose, is_dark, most_common, not_in_theme  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
COMMIT_HISTORY = "Commit History…"  # the File menu item, as shown
RESTORE = "Restore this version"
FORMAT = "%Y-%m-%d %H:%M:%S"
WHEN = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d"
NO_RUN = "No run was made with exactly this version"
MANUAL = {"type": "trigger.manual", "ui": {"x": -280, "y": 0}}


# --- isolation ---------------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def git_config(tmp_path, monkeypatch) -> Path:
    """The test's own Git config: this empty global config file, no system config, no GIT_* variables."""
    config = tmp_path / "gitconfig"
    config.write_bytes(b"")
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    if inside_a_repository(tmp_path):
        pytest.skip(f"{tmp_path} is inside a Git repository: these tests need folders outside any")
    return config


@pytest.fixture(autouse=True)
def windows_closed():
    """The windows a test opened are closed after it (one left open would be taken for the next test's)."""
    app = QApplication.instance()
    before = set(app.topLevelWidgets()) if app else set()
    yield
    app = QApplication.instance()
    for window in (app.topLevelWidgets() if app else []):
        try:
            if window not in before and window.isVisible():
                window.close()
        except RuntimeError:  # (already deleted)
            pass
    settle()


@pytest.fixture
def look_kept(qapp, monkeypatch, tmp_path):
    """The application's look as the test found it, after it (the theme chosen in Settings is the whole session's);
    Save in Settings writes Windows' Startup script into this test's folder, never the real one."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    style, sheet, palette, tips = qapp.style().name(), qapp.styleSheet(), QApplication.palette(), QToolTip.palette()
    yield
    QGuiApplication.styleHints().unsetColorScheme()
    if qapp.style().name() != style:
        QApplication.setStyle(style)
    if qapp.styleSheet() != sheet:
        qapp.setStyleSheet(sheet)
    QApplication.setPalette(palette)
    QToolTip.setPalette(tips)
    settle()


def known_identity(config: Path):
    """The user's Git config gives the name and email to commit with."""
    config.write_bytes(b"[user]\n\tname = Ana Lima\n\temail = ana@example.com\n")


# --- flows, versions and runs ------------------------------------------------------------------------------------

def flow_bytes(blocks: dict, edges=(), notes: dict | None = None, name: str = "report") -> bytes:
    """A flow file's bytes, as Taskloom writes them (so the editor saves them unchanged)."""
    data = {"taskloom": 1, "name": name, "params": {}, "blocks": blocks, "edges": list(edges)}
    if notes:
        data["notes"] = notes
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")


def python(code: str, x: int, inputs=(), y: int = 0) -> dict:
    """A Python Code block at (x, y) with one output, `output`."""
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": ["output"], "code": code},
            "ui": {"x": x, "y": y}}


def blob_id(data: bytes) -> str:
    """The Git blob id of a file's bytes, as Git computes it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def local(date: str) -> str:
    """An ISO date with its time zone, in the computer's local time, as YYYY-MM-DD HH:MM:SS."""
    return dt.datetime.fromisoformat(date).astimezone().strftime(FORMAT)


def make_history(repo: Path, commits) -> list[str]:
    """A new repository in `repo` with one commit per entry, oldest first, on its branch: (message, {path in the
    repository: bytes}, author date as ISO with its time zone); each committed a day and three hours after its
    author date. Made with the real git program; the files then hold their newest bytes and the index is HEAD's.
    The commits' ids, oldest first."""
    new_repo(repo)
    ref = git(repo, "symbolic-ref", "HEAD").strip()
    stream, newest = b"", {}
    for i, (message, files, date) in enumerate(commits, 1):
        when = dt.datetime.fromisoformat(date)
        seconds, zone = int(when.timestamp()), when.strftime("%z")
        text = message.encode("utf-8")
        stream += (f"commit {ref}\nmark :{i}\nauthor Setup <setup@example.com> {seconds} {zone}\n"
                   f"committer Setup <setup@example.com> {seconds + 97200} {zone}\n"
                   f"data {len(text)}\n").encode() + text + b"\n"
        if i > 1:
            stream += f"from :{i - 1}\n".encode()
        for path, data in files.items():
            stream += f"M 100644 inline {path}\ndata {len(data)}\n".encode() + data + b"\n"
            newest[path] = data
    done = subprocess.run([GIT, "-C", str(repo), "fast-import", "--quiet"], input=stream, capture_output=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    for path, data in newest.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(data)
    git(repo, "reset", "-q")
    return lines(repo, "rev-list", "--reverse", "HEAD")


def dates(n: int, first=dt.datetime(2026, 1, 5, 9, 30, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))):
    """n author dates, a day and a few minutes apart, oldest first."""
    return [(first + dt.timedelta(days=i, minutes=7 * i)).isoformat() for i in range(n)]


def run(env, flow: Path, *args) -> int:
    """`taskloom run <flow>`, as a terminal or the scheduler service runs it; its run's number."""
    done = env.taskloom("run", flow, *args)
    assert done.returncode == 0, done.stdout + done.stderr
    return env.last_run()[0]


def run_with(env, flow: Path, data: bytes, *args) -> int:
    """A run of `flow` while its file holds `data` (put back as it was after)."""
    before = flow.read_bytes()
    flow.write_bytes(data)
    try:
        return run(env, flow, *args)
    finally:
        flow.write_bytes(before)


def recorded(env, run_id: int) -> list[str]:
    """Everything the run history keeps with run `run_id`, as text."""
    (row,) = env.query("SELECT * FROM runs WHERE id=?", run_id)
    return [str(value) for value in row]


def run_info(win, run_id: int) -> dict:
    """When run `run_id` started and finished, and when each of its blocks finished (to the second, local time)."""
    with sqlite3.connect(win.home.history_db) as db:
        started, finished = db.execute("SELECT started, finished FROM runs WHERE id=?", (run_id,)).fetchone()
        blocks = dict(db.execute("SELECT block, finished FROM blocks WHERE run_id=?", (run_id,)).fetchall())
    second = lambda ts: ts.replace("T", " ")[:19]  # noqa: E731
    return {"started": second(started), "finished": second(finished),
            "blocks": {b: second(ts) for b, ts in blocks.items() if ts}}


# --- the editor --------------------------------------------------------------------------------------------------

def settle(rounds: int = 5):
    for _ in range(rounds):
        QApplication.processEvents()


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def file_menu(win) -> QMenu:
    (found,) = [a.menu() for a in win.menuBar().actions() if shown(a.text()) == "File"]
    return found


def history_item(win):
    found = [a for a in file_menu(win).actions() if shown(a.text()) == COMMIT_HISTORY]
    assert len(found) == 1, (f"the File menu has no {COMMIT_HISTORY} item: "
                             f"{[shown(a.text()) for a in file_menu(win).actions()]}")
    return found[0]


def history_windows(win, name: str) -> list:
    """The windows showing, other than the editor's, titled with `name`."""
    return [w for w in QApplication.topLevelWidgets() if w is not win and w.isVisible() and name in w.windowTitle()
            and not isinstance(w, QMenu) and not w.inherits("QTipLabel")]


def open_history(win, qtbot, name: str) -> "Shown":
    """File → Commit History… for the flow on screen (named `name`): the window that shows. A window that waits for
    an answer (a modal one) fails the test."""
    item = history_item(win)
    assert item.isEnabled(), f"File → {COMMIT_HISTORY} is disabled: {item.toolTip()}"
    modal = []

    def unblock():
        waiting = QApplication.activeModalWidget()
        if waiting is not None:
            modal.append(waiting.windowTitle())
            waiting.close()

    guard = QTimer(win, singleShot=True)
    guard.timeout.connect(unblock)
    guard.start(5_000)
    try:
        item.trigger()
    finally:
        guard.stop()
        guard.deleteLater()
    assert not modal, f"Commit History waits for an answer in a modal window {modal}: the editor cannot be used"

    def one():
        found = history_windows(win, name)
        assert len(found) == 1, f"{len(found)} windows titled with {name} show"

    qtbot.waitUntil(one, timeout=10_000)
    settle()
    return Shown(history_windows(win, name)[0])


def is_closed(window) -> bool:
    try:
        return not window.isVisible()
    except RuntimeError:  # deleted
        return True


def is_hidden(view: QAbstractItemView, row: int) -> bool:
    if isinstance(view, QTreeView):
        return view.isRowHidden(row, QModelIndex())
    if isinstance(view, (QListView, QTableView)):
        return view.isRowHidden(row)
    return False


class Shown:
    """What the Commit History window shows."""

    def __init__(self, window):
        self.window = window

    def _visible(self, kind) -> list:
        return [w for w in self.window.findChildren(kind) if w.isVisible()]

    def activate(self, qtbot):
        """As when you click into the window: it is the active one (the keys you press go to it)."""
        self.window.activateWindow()
        qtbot.waitUntil(lambda: QApplication.activeWindow() is self.window, timeout=5_000)

    # left pane

    @property
    def field(self) -> QLineEdit:
        previews = self._visible(QGraphicsView)
        found = [f for f in self._visible(QLineEdit) if not any(v.isAncestorOf(f) for v in previews)]
        assert len(found) == 1, f"the window shows {len(found)} search fields"
        return found[0]

    def toggle(self, text: str) -> QAbstractButton:
        found = [b for b in self._visible(QAbstractButton) if b.text().replace("&", "") == text]
        assert len(found) == 1, f"the window shows {len(found)} {text!r} buttons"
        return found[0]

    @property
    def listing(self) -> QAbstractItemView:
        found = self._visible(QAbstractItemView)
        assert len(found) == 1, f"the window shows {len(found)} lists"
        return found[0]

    def rows(self) -> list[int]:
        view = self.listing
        model = view.model()
        return [r for r in range(model.rowCount() if model else 0) if not is_hidden(view, r)]

    def card(self, row: int) -> str:
        view = self.listing
        index = view.model().index(row, 0)
        parts = [index.data(Qt.DisplayRole), index.data(Qt.AccessibleTextRole)]
        widget = view.indexWidget(index)
        if widget is not None:
            parts += [label.text() for label in ([widget] if isinstance(widget, QLabel) else [])
                      + widget.findChildren(QLabel)]
        return plain(" ".join(p for p in parts if isinstance(p, str)))

    def cards(self) -> list[str]:
        return [self.card(r) for r in self.rows()]

    def ids(self, ids: list[str]) -> list[str]:
        """Which of the commits `ids` each card shown is (by its short id), top to bottom."""
        found = []
        for text in self.cards():
            which = [i for i in ids if i[:7] in text]
            assert len(which) == 1, f"the card {text!r} shows the short id of {len(which)} of the commits"
            found += which
        return found

    def selected(self) -> int | None:
        """The place (from 0) of the selected card among the cards shown; None: none is."""
        view = self.listing
        rows = sorted({i.row() for i in view.selectionModel().selectedIndexes()}) if view.selectionModel() else []
        assert len(rows) <= 1, f"{len(rows)} cards are selected"
        return self.rows().index(rows[0]) if rows and rows[0] in self.rows() else None

    def selected_id(self, ids: list[str]) -> str | None:
        place = self.selected()
        return None if place is None else self.ids(ids)[place]

    def in_view(self, place: int) -> bool:
        view = self.listing
        rect = view.visualRect(view.model().index(self.rows()[place], 0))
        return view.viewport().rect().contains(rect)

    def click_card(self, place: int):
        """Click the card at `place` among those shown, as you would."""
        view = self.listing
        index = view.model().index(self.rows()[place], 0)
        view.scrollTo(index)
        settle()
        viewport = view.viewport()
        at = view.visualRect(index).center()
        target = viewport.childAt(at) or viewport
        QTest.mouseClick(target, Qt.LeftButton, Qt.NoModifier, target.mapFrom(viewport, at) if target is not viewport
                         else at)
        settle()

    def type(self, text: str):
        """Type `text` over what the search field holds (nothing: empty it)."""
        field = self.field
        field.setFocus()
        field.selectAll()
        if text:
            QTest.keyClicks(field, text)
        else:
            QTest.keyClick(field, Qt.Key_Backspace)
        settle()

    def texts(self) -> str:
        """Everything the window says in words: its labels and its cards."""
        return " | ".join([plain(label.text()) for label in self._visible(QLabel) if label.text().strip()]
                          + self.cards())

    # right pane

    @property
    def preview(self) -> QGraphicsView:
        found = self._visible(QGraphicsView)
        assert len(found) == 1, f"the window shows {len(found)} previews"
        return found[0]

    def nodes(self) -> dict:
        scene = self.preview.scene()
        return {i.block_id: i for i in scene.items() if isinstance(i, NodeItem)} if scene else {}

    def notes(self) -> list[str]:
        scene = self.preview.scene()
        return sorted(i.toPlainText() for i in scene.items() if isinstance(i, NoteItem)) if scene else []

    def edges(self) -> int:
        scene = self.preview.scene()
        return sum(isinstance(i, EdgeItem) for i in scene.items()) if scene else 0

    def drawn(self) -> dict:
        """The flow the preview draws: each block's place and the texts and connections."""
        return {"blocks": {b: (round(n.pos().x()), round(n.pos().y())) for b, n in self.nodes().items()},
                "notes": self.notes(), "edges": self.edges()}

    def run_line(self) -> str:
        found = [plain(label.text()) for label in self._visible(QLabel)
                 if re.match(r"(Outputs of run #|No run was made)", plain(label.text()))]
        assert len(found) == 1, f"the window shows {len(found)} lines saying which run the outputs are of"
        return found[0]

    @property
    def restore(self) -> QAbstractButton:
        found = [b for b in self._visible(QAbstractButton) if shown(b.text()) == RESTORE]
        assert len(found) == 1, f"the window shows {len(found)} {RESTORE!r} buttons"
        return found[0]


def shows_tip(qtbot, timeout=3_000) -> str:
    """The text of the tooltip showing within `timeout` ms ("" if none shows)."""
    def tip():
        return next((w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()), None)
    try:
        qtbot.waitUntil(lambda: tip() is not None, timeout=timeout)
    except qtbot.TimeoutError:
        return ""
    return tip().text()


def hovered(qtbot, view: QGraphicsView, node: NodeItem, scroll: bool = False) -> str:
    """Rest the mouse on the line under `node` in `view`, as you would: the text of the tooltip that shows. `scroll`:
    first scrolled into view (the editor's canvas can be narrower than the flow), else it must be in view."""
    settle()
    QToolTip.hideText()
    qtbot.waitUntil(lambda: not any(w.inherits("QTipLabel") and w.isVisible() for w in QApplication.topLevelWidgets()),
                    timeout=5_000)
    at = node.footer.sceneBoundingRect().center()
    if scroll:
        view.centerOn(at)
        settle()
    viewport = view.viewport()
    pos = view.mapFromScene(at)
    assert viewport.rect().contains(pos), f"the line under {node.block_id} is not in view"
    QApplication.sendEvent(viewport, QHelpEvent(QEvent.ToolTip, pos, viewport.mapToGlobal(pos)))
    text = shows_tip(qtbot)
    QToolTip.hideText()
    return text


def save_and_commit(win, message: str):
    """File → Save & Commit… for the flow on screen; its window answered with `message`, the name and the email."""
    seen = []

    def do(dialog):
        seen.append(dialog.windowTitle())
        fill(dialog, **ANA, message=message)
        click(dialog, OK)

    when_dialog_shows(win, do)
    menu(win, SAVE_AND_COMMIT).trigger()
    settle()
    assert seen == ["Save & Commit"], seen
    assert win.message_boxes == []


def status_bar(win) -> str:
    return win.statusBar().currentMessage()


# --- 1: each run records the version of the flow file it ran ---------------------------------------------------

def test_each_run_records_the_git_blob_id_of_the_bytes_of_the_flow_file_it_ran(env):
    """Spec 1: a run keeps with it, in the run history, the Git blob id of the exact bytes of its flow file (CRLF
    line ends as written on Windows, no conversion); a run after the file changed keeps the new bytes' id, the first
    run its own; a re-run of one block (--only) keeps the id of the bytes it read too."""
    flow = env.root / "flows" / "report.yaml"
    flow.parent.mkdir()
    first = flow_bytes({"manual": MANUAL, "load": python("output = 6 * 7", 0)},
                       ["manual.start -> load.after"]).replace(b"\n", b"\r\n")
    flow.write_bytes(first)
    if GIT:  # (the id as the real git program computes it)
        assert git(env.root, "hash-object", "--no-filters", flow).strip() == blob_id(first)

    one = run(env, flow)

    assert blob_id(first) in recorded(env, one), recorded(env, one)

    second = flow_bytes({"manual": MANUAL, "load": python("output = 6 * 8", 0)}, ["manual.start -> load.after"])
    flow.write_bytes(second)
    two = run(env, flow)
    three = run(env, flow, "--only", "load", "--inputs-from", two)

    assert blob_id(second) in recorded(env, two) and blob_id(first) not in recorded(env, two)
    assert blob_id(second) in recorded(env, three), recorded(env, three)
    assert blob_id(first) in recorded(env, one) and blob_id(second) not in recorded(env, one)


CHANGER = '''\
from pathlib import Path

# A run imports the blocks of your own once it has read its flow file, just before it begins: this one saves the
# flow file then, as a Save in the editor would while a scheduled run (started early) waits for its time.
Path({path!r}).write_bytes({data!r})
'''


def test_a_run_records_the_bytes_it_loaded_even_when_the_file_changes_before_it_begins(env):
    """Spec 1 ("the bytes the run read when it loaded the flow"): the flow file is saved again after the run read it
    and before the run began: the run runs what it read and records the id of those bytes, not of the file's new
    ones."""
    flow = env.root / "flows" / "report.yaml"
    flow.parent.mkdir()
    loaded = flow_bytes({"manual": MANUAL, "load": python("output = 1", 0)}, ["manual.start -> load.after"])
    saved = flow_bytes({"manual": MANUAL, "other": python("output = 2", 0)}, ["manual.start -> other.after"])
    flow.write_bytes(loaded)
    blocks = env.home / "blocks"
    blocks.mkdir()
    (blocks / "changer.py").write_text(CHANGER.format(path=str(flow), data=saved), encoding="utf-8")

    run_id = run(env, flow)

    assert flow.read_bytes() == saved  # (it was saved again meanwhile)
    assert set(env.block_status(run_id)) == {"manual", "load"}  # it ran what it had read
    assert blob_id(loaded) in recorded(env, run_id), recorded(env, run_id)
    assert blob_id(saved) not in recorded(env, run_id)


# --- 2: the menu item and the window -----------------------------------------------------------------------------

def test_commit_history_sits_just_under_save_and_commit_and_is_disabled_for_a_flow_never_saved(win, tmp_path,
                                                                                                monkeypatch):
    """Spec 2: File → Commit History… is the item just under Save & Commit…; enabled for a saved flow, disabled for
    the tab of a flow never saved, with a tooltip (the File menu shows tooltips) saying it needs saving; it follows
    the tab on screen, and once the new flow is saved it is enabled."""
    folder = tmp_path / "flows"
    folder.mkdir()
    (folder / "report.yaml").write_bytes(flow_bytes({"manual": MANUAL}))
    saved_tab = win.open_flow(folder / "report.yaml")
    items = [shown(a.text()) for a in file_menu(win).actions()]
    at = items.index("Save & Commit…")

    assert items[at + 1] == COMMIT_HISTORY, items
    item = history_item(win)
    assert item.isEnabled()

    untitled = win.new_flow()
    settle()

    assert not item.isEnabled()
    assert re.search(r"sav", item.toolTip(), re.IGNORECASE) and shown(item.toolTip()) != COMMIT_HISTORY, item.toolTip()
    assert file_menu(win).toolTipsVisible(), "the File menu does not show its items' tooltips"

    win.tabs.setCurrentWidget(saved_tab)
    settle()
    assert item.isEnabled()
    win.tabs.setCurrentWidget(untitled)
    settle()
    assert not item.isEnabled()

    save_as(monkeypatch, folder / "new.yaml")
    menu(win, "Save").trigger()
    settle()
    assert untitled.doc.path is not None and item.isEnabled()
    clean(win)


def test_commit_history_opens_a_window_of_its_own_per_flow_brought_to_the_front_again_and_closed_with_its_tab(
        win, qtbot, tmp_path):
    """Spec 2, 8: the window is a window of its own titled with the flow's file name, not modal (the editor stays
    usable while it shows); another flow gets a window of its own; Commit History again for the first flow brings
    its window to the front instead of opening a second one; closing a flow's tab closes its window, only its."""
    for name in ("report", "nightly"):
        (tmp_path / name).mkdir()
        (tmp_path / name / f"{name}.yaml").write_bytes(flow_bytes({"manual": MANUAL}, name=name))
    report = win.open_flow(tmp_path / "report" / "report.yaml")

    first = open_history(win, qtbot, "report.yaml")

    window = first.window
    assert window is not win and window.isWindow()
    assert not window.isModal() and QApplication.activeModalWidget() is None
    report.doc.move_blocks({"manual": (-200, 40)})  # the editor still takes your edits
    assert report.doc.dirty
    clean(win)

    nightly = win.open_flow(tmp_path / "nightly" / "nightly.yaml")
    other = open_history(win, qtbot, "nightly.yaml").window
    assert other is not window and window.isVisible() and other.isVisible()

    win.tabs.setCurrentWidget(report)
    win.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=5_000)
    again = open_history(win, qtbot, "report.yaml")

    assert again.window is window, "a second window opened for the same flow"
    qtbot.waitUntil(lambda: QApplication.activeWindow() is window, timeout=5_000)

    assert win.close_tab(report)
    qtbot.waitUntil(lambda: is_closed(window), timeout=5_000)
    assert other.isVisible()
    assert win.close_tab(nightly)
    qtbot.waitUntil(lambda: is_closed(other), timeout=5_000)


@pytest.mark.parametrize("repository", ["none", "the file never committed", "no commit yet"])
def test_a_flow_with_no_commits_opens_a_window_saying_there_are_none_yet_and_how_to_make_one(win, qtbot, tmp_path,
                                                                                             repository):
    """Spec 2: no repository, a repository in which the flow file was never committed, or one with no commit at
    all: the window opens, its list says plainly there are no commits yet and that File → Save & Commit… makes one;
    nothing fails (no message box) and there is nothing to restore."""
    folder = tmp_path / "flows"
    if repository == "none":
        folder.mkdir()
    else:
        if GIT is None:
            pytest.skip("the repository is made with the real git program, which is not installed")
        if repository == "the file never committed":
            make_history(folder, [("Add the notes", {"notes.txt": b"notes\n"}, dates(1)[0])])
        else:
            new_repo(folder)
    flow = folder / "report.yaml"
    flow.write_bytes(flow_bytes({"manual": MANUAL}))
    win.open_flow(flow)

    window = open_history(win, qtbot, "report.yaml")

    said = window.texts()
    assert re.search(r"no commits", said, re.IGNORECASE), said
    assert "Save & Commit" in said, said
    assert not window.restore.isEnabled()
    assert win.message_boxes == []


# --- 3: the list of commits --------------------------------------------------------------------------------------

@needs_git
def test_the_list_shows_the_commits_of_the_flow_file_newest_first_with_message_author_date_and_short_id(
        win, qtbot, tmp_path):
    """Spec 3, 5: in a bigger repository (the flow in a folder of it), the cards are the commits that change the
    flow file, newest first (not one that changes only another file); each shows its message's first line and the
    rest, the author date and time in local time as YYYY-MM-DD HH:MM:SS (not the commit date) and the short id. The
    search field has .* on and Aa off, each with a tooltip; the first card is selected and previewed."""
    v1 = flow_bytes({"manual": MANUAL, "load": python("output = 1", 0)}, ["manual.start -> load.after"])
    v2 = flow_bytes({"manual": MANUAL, "load": python("output = 1", 0), "total": python("output = input", 280,
                                                                                         ["input"])},
                    ["manual.start -> load.after", "load.output -> total.input"])
    v3 = flow_bytes({"manual": MANUAL, "load": python("output = 1", 0), "total": python("output = input", 280,
                                                                                         ["input"], y=120)},
                    ["manual.start -> load.after", "load.output -> total.input"])
    when = ["2026-01-05T09:30:00+05:30", "2026-02-06T10:40:00-03:00", "2026-03-07T11:50:00+00:00",
            "2026-04-08T08:15:00+02:00"]
    body = "Why: the nightly numbers need a total.\nChecked by hand."
    ids = make_history(tmp_path / "project", [
        ("Load the numbers", {"flows/report.yaml": v1, "notes.txt": b"a\n"}, when[0]),
        ("Update the notes", {"notes.txt": b"b\n"}, when[1]),
        (f"Add the totals\n\n{body}", {"flows/report.yaml": v2}, when[2]),
        ("Move the totals", {"flows/report.yaml": v3}, when[3]),
    ])
    win.open_flow(tmp_path / "project" / "flows" / "report.yaml")

    window = open_history(win, qtbot, "report.yaml")

    assert window.ids(ids) == [ids[3], ids[2], ids[0]]
    cards = window.cards()
    for card, message, date in zip(cards, ["Move the totals", "Add the totals", "Load the numbers"],
                                   [when[3], when[2], when[0]]):
        assert message in card, card
        assert local(date) in card, (local(date), card)
        committed = (dt.datetime.fromisoformat(date) + dt.timedelta(hours=27)).astimezone().strftime(FORMAT)
        assert committed not in card, f"the card shows the commit date, not the author date: {card}"
    assert plain(body.split("\n")[0]) in cards[1], cards[1]
    regex, case = window.toggle(".*"), window.toggle("Aa")
    assert regex.isCheckable() and regex.isChecked() and regex.toolTip().strip()
    assert case.isCheckable() and not case.isChecked() and case.toolTip().strip()
    assert window.selected() == 0
    assert set(window.nodes()) == {"manual", "load", "total"}
    assert window.restore.isEnabled()


@needs_git
def test_a_history_carried_by_save_as_keeps_the_dates_of_the_original_commits(win, qtbot, tmp_path, monkeypatch,
                                                                              git_config):
    """Spec 3 ("so a history carried by Save as keeps its dates"): Save as… into another folder replays the
    original's versions there (committed now, by you); the copy's cards show the original messages and author dates
    and times, newest first, with the copy's own commit ids."""
    known_identity(git_config)
    when = ["2026-01-05T09:30:00+05:30", "2026-02-06T10:40:00-03:00", "2026-03-07T11:50:00+00:00"]
    messages = ["Load the numbers", "Add the totals", "Move the totals"]
    versions = [flow_bytes({"manual": MANUAL, "load": python(f"output = {i}", 0)}, ["manual.start -> load.after"])
                for i in range(3)]
    make_history(tmp_path / "flows", [(m, {"report.yaml": v}, d) for m, v, d in zip(messages, versions, when)])
    win.open_flow(tmp_path / "flows" / "report.yaml")
    (tmp_path / "copies").mkdir()
    save_as(monkeypatch, tmp_path / "copies" / "copy.yaml")
    menu(win, "Save as…").trigger()
    settle()
    assert win.message_boxes == []
    copied = lines(tmp_path / "copies", "rev-list", "HEAD")  # newest first

    window = open_history(win, qtbot, "copy.yaml")

    assert window.ids(copied) == copied
    for card, message, date in zip(window.cards(), reversed(messages), reversed(when)):
        assert message in card and local(date) in card, (message, local(date), card)


# --- 4: searching ------------------------------------------------------------------------------------------------

ZEBRA_DATES = dates(6)


def zebra_history(env, folder: Path) -> tuple[list[str], dict]:
    """Six versions of report.yaml, oldest first, and where "zebra" is in each:
    v1 "Zebra report: first version" and v2 "Count the zebra crossings": in the message;
    v3: in the outputs of load_sales in its run (its code is "ze" + "bra" + " crossings");
    v4: in the code of total_sales (a comment), and in no output (it was never run);
    v5: in a text on the canvas (the flow file);
    v6 "Tidy up": nowhere (it was run: the latest run of the flow file).
    The commits' ids, oldest first, and the versions' bytes by name."""
    def version(load: str, total: str, notes=None) -> bytes:
        return flow_bytes({"manual": MANUAL, "load_sales": python(load, 0),
                           "total_sales": python(total, 280, ["input"])},
                          ["manual.start -> load_sales.after", "load_sales.output -> total_sales.input"], notes)

    label = 'output = "ze" + "bra" + " crossings"'
    v = {"v1": version("output = 6 * 7", "output = input + 1"),
         "v2": version("output = 6 * 8", "output = input + 1"),
         "v3": version(label, "output = len(input)"),
         "v4": version(label, "# zebra crossings are counted once\noutput = len(input)"),
         "v5": version(label, "output = len(input)",
                       {"note_1": {"text": "Zebra crossings, counted nightly", "x": 0, "y": 220}}),
         "v6": version("output = 6 * 9", "output = input + 1")}
    messages = ["Zebra report: first version", "Count the zebra crossings", "Label the totals", "Explain the check",
                "Describe the flow", "Tidy up"]
    ids = make_history(folder, [(m, {"report.yaml": v[f"v{i}"]}, d)
                                for i, (m, d) in enumerate(zip(messages, ZEBRA_DATES), 1)])
    flow = folder / "report.yaml"
    run_with(env, flow, v["v3"])
    run(env, flow)  # v6, as the file holds it
    return ids, v


@needs_git
def test_typing_filters_the_commits_grouped_by_where_they_match_message_outputs_code_then_flow_file(
        env, win, qtbot, tmp_path):
    """Spec 4, 5: typing "zebra" filters the list at once: the message matches first (newest first), then the
    version whose latest run's outputs hold it, then the one whose code holds it, then the one whose flow file holds
    it elsewhere; each card says where it matched. A version is matched by the outputs of a run of exactly it (v4
    has v3's code but was never run). The first card is selected after the search; the empty field shows every commit
    again, newest first, the first selected."""
    ids, _ = zebra_history(env, tmp_path / "flows")
    v1, v2, v3, v4, v5, v6 = ids
    win.open_flow(tmp_path / "flows" / "report.yaml")
    window = open_history(win, qtbot, "report.yaml")
    assert window.ids(ids) == ids[::-1]

    window.type("zebra")

    def grouped():
        assert window.ids(ids) == [v2, v1, v3, v4, v5]
    qtbot.waitUntil(grouped, timeout=3_000)
    where = dict(zip(window.ids(ids), window.cards()))
    assert "in the message" in where[v2] and "in the message" in where[v1], where
    assert "in the outputs of load_sales" in where[v3], where[v3]
    assert "in the code of total_sales" in where[v4], where[v4]
    assert "in the flow file" in where[v5], where[v5]
    assert window.selected() == 0

    window.type("")

    def every():
        assert window.ids(ids) == [v6, v5, v4, v3, v2, v1]
    qtbot.waitUntil(every, timeout=3_000)
    assert window.selected() == 0


@needs_git
def test_regular_expressions_and_match_case_toggles_and_an_invalid_pattern_is_marked_red_and_said(win, qtbot,
                                                                                                tmp_path):
    """Spec 3, 4: .* on: "zeb.a" is a pattern; off, it is found as typed (here nowhere), and '"ze" + "bra"' (no
    match as a pattern) is found in the code. Aa off: "zebra" matches "Zebra" too; on, only the case typed. An invalid
    pattern ("zebra(") marks the field red and says it is not valid, as the find bar does; a valid one takes the
    mark away."""
    code = 'output = "ze" + "bra"'
    v1 = flow_bytes({"manual": MANUAL, "load_sales": python("output = 1", 0)}, ["manual.start -> load_sales.after"])
    v3 = flow_bytes({"manual": MANUAL, "load_sales": python(code, 0)}, ["manual.start -> load_sales.after"])
    ids = make_history(tmp_path / "flows", [("Zebra report", {"report.yaml": v1}, dates(3)[0]),
                                            ("count the zebra crossings", {"report.yaml": v1 + b"# 2\n"},
                                             dates(3)[1]),
                                            ("Label the totals", {"report.yaml": v3}, dates(3)[2])])
    v1_id, v2_id, v3_id = ids
    win.open_flow(tmp_path / "flows" / "report.yaml")
    window = open_history(win, qtbot, "report.yaml")
    regex, case = window.toggle(".*"), window.toggle("Aa")

    def found(*expected):
        def check():
            assert window.ids(ids) == list(expected)
        qtbot.waitUntil(check, timeout=3_000)

    window.type("zeb.a")
    found(v2_id, v1_id)
    window.type("zebra")
    found(v2_id, v1_id)
    valid_red = red_pixels(window.field)

    QTest.mouseClick(case, Qt.LeftButton)
    settle()
    assert case.isChecked()
    found(v2_id)
    window.type("Zebra")
    found(v1_id)
    QTest.mouseClick(case, Qt.LeftButton)
    settle()
    assert not case.isChecked()

    window.type("zebra(")
    assert red_pixels(window.field) - valid_red >= 10, "the field is not marked red"
    said = [window.field.toolTip()] + [label.text() for label in window._visible(QLabel)]
    assert any(re.search(r"invalid|not (a )?valid", t, re.IGNORECASE) for t in said), said
    window.type("zebra")
    found(v2_id, v1_id)
    assert red_pixels(window.field) - valid_red < 10, "the red mark stays with a valid pattern"

    window.type('"ze" + "bra"')
    found()
    QTest.mouseClick(regex, Qt.LeftButton)
    settle()
    assert not regex.isChecked()
    found(v3_id)
    assert "in the code of load_sales" in window.cards()[0], window.cards()
    window.type("zeb.a")
    found()
    window.type("zebra(")
    found()
    assert red_pixels(window.field) - valid_red < 10, "with .* off, ( is no pattern: nothing is invalid"


TALLY = '''\
from taskloom import Block, fields, ports


class Tally(Block):
    type_id = "user.tally"
    title = "Tally"
    description = "Counts what it gets with a formula of yours."
    inputs = {"value": ports.Any(required=False)}
    outputs = {"total": ports.Any()}
    config = {"formula": fields.Code("python", default="len(value)", help="How to count"),
              "label": fields.Text(default="", help="A name for the count")}

    def run(self, ctx, value=None):
        return {"total": eval(self.config.formula, {"value": value})}
'''


@pytest.fixture
def tally_block(tmp_path):
    """A block of your own with a code setting (formula) and a text setting (label), in the Taskloom folder the
    editor starts with (list it before `win`)."""
    folder = tmp_path / "home" / "blocks"
    folder.mkdir(parents=True)
    (folder / "tally.py").write_text(TALLY, encoding="utf-8")


CODE_SETTINGS = [  # each block type with a code setting: the block, its type and its config with "zebra" in its code
    ("python_step", "logic.python", {"inputs": [], "outputs": ["output"], "code": "output = 'zebra'"}),
    ("duckdb_step", "data.duckdb_sql", {"sql": "select 'zebra' as animal"}),
    ("polars_step", "data.polars", {"code": "df.filter(pl.col('animal') == 'zebra')"}),
    ("html_step", "reports.html_template", {"template": "<b>Zebra</b> crossings"}),
    ("if_step", "logic.if", {"condition": "value == 'zebra'"}),
    ("inspect_step", "logic.inspect", {"message": "Saw {value} zebra"}),
    ("query_step", "db.run_query", {"connection": "warehouse", "sql": "select * from zebra"}),
    ("tally_step", "user.tally", {"formula": "len(value) + len('zebra')"}),
]


@needs_git
def test_the_code_searched_is_every_code_setting_of_every_block_type_and_other_settings_are_the_flow_file(
        tally_block, win, qtbot, tmp_path):
    """Spec 4c, 4d: "zebra" in the Python code, the SQL (DuckDB SQL, Run Query), the Polars code, the HTML template,
    the If condition, the Inspect message and a block of your own's code setting each matches "in the code of" that
    block; in a block's other setting (the Tally's label), "in the flow file"."""
    commits = [(f"Version {i}", {"report.yaml": flow_bytes({"manual": MANUAL, block: {"type": kind, "config": config,
                                                                                       "ui": {"x": 0, "y": 0}}})},
                date)
               for i, ((block, kind, config), date) in enumerate(zip(CODE_SETTINGS, dates(len(CODE_SETTINGS))), 1)]
    commits.append(("Name the count", {"report.yaml": flow_bytes({"manual": MANUAL, "tally_step": {
        "type": "user.tally", "config": {"formula": "len(value)", "label": "zebra count"}, "ui": {"x": 0, "y": 0}}})},
        dates(len(CODE_SETTINGS) + 1)[-1]))
    ids = make_history(tmp_path / "flows", commits)
    win.open_flow(tmp_path / "flows" / "report.yaml")
    window = open_history(win, qtbot, "report.yaml")

    window.type("zebra")

    def matched():
        assert window.ids(ids) == ids[:-1][::-1] + ids[-1:]
    qtbot.waitUntil(matched, timeout=3_000)
    where = dict(zip(window.ids(ids), window.cards()))
    for commit, (block, kind, _) in zip(ids, CODE_SETTINGS):
        assert f"in the code of {block}" in where[commit], (kind, where[commit])
    assert "in the flow file" in where[ids[-1]], where[ids[-1]]


# --- 5: moving through the cards ---------------------------------------------------------------------------------

@needs_git
def test_up_and_down_move_through_the_cards_shown_also_from_the_search_field_and_keep_the_card_in_view(
        win, qtbot, tmp_path):
    """Spec 5, 6: 40 commits ("Step 1" … "Step 40", each with a text "version N"): Down and Up in the search field
    move the selection, and the preview follows; Down 39 times reaches the last card, scrolled into view, Up 39 times
    the first; in the list itself the arrows move it too; clicking a card selects it. With a search ("Step 1": 11
    cards), Down goes to the next card shown."""
    n = 40
    ids = make_history(tmp_path / "flows", [
        (f"Step {i}", {"report.yaml": flow_bytes({"manual": MANUAL},
                                                 notes={"note_1": {"text": f"version {i}", "x": 0, "y": 200}})}, d)
        for i, d in enumerate(dates(n), 1)])
    newest_first = ids[::-1]
    win.open_flow(tmp_path / "flows" / "report.yaml")
    window = open_history(win, qtbot, "report.yaml")
    assert window.selected() == 0 and window.notes() == [f"version {n}"]
    window.activate(qtbot)
    field = window.field
    field.setFocus()

    def at(place: int):
        def check():
            assert window.selected() == place and window.selected_id(ids) == newest_first[place]
            assert window.notes() == [f"version {n - place}"], window.notes()
        qtbot.waitUntil(check, timeout=3_000)

    QTest.keyClick(field, Qt.Key_Down)
    at(1)
    QTest.keyClick(field, Qt.Key_Up)
    at(0)
    for _ in range(n - 1):
        QTest.keyClick(field, Qt.Key_Down)
    at(n - 1)
    assert window.in_view(n - 1), "the selected card is not scrolled into view"
    for _ in range(n - 1):
        QTest.keyClick(field, Qt.Key_Up)
    at(0)
    assert window.in_view(0), "the selected card is not scrolled into view"

    window.listing.setFocus()
    QTest.keyClick(window.listing, Qt.Key_Down)
    at(1)
    window.click_card(4)
    at(4)

    window.type("Step 1")

    def eleven():
        assert len(window.cards()) == 11 and window.selected() == 0
    qtbot.waitUntil(eleven, timeout=3_000)
    QTest.keyClick(window.field, Qt.Key_Down)

    def next_shown():
        assert window.selected() == 1 and "Step 18" in window.cards()[1]
        assert window.notes() == ["version 18"], window.notes()
    qtbot.waitUntil(next_shown, timeout=3_000)


# --- 6: the preview, with the outputs of the latest run of exactly that version ----------------------------------

@needs_git
def test_the_preview_shows_the_selected_version_with_the_outputs_of_its_latest_run_as_the_canvas_shows_them(
        env, win, qtbot, tmp_path):
    """Spec 6 (the user's case): each card's preview draws that version's blocks (where they were), connections and
    texts, fitted to the pane. The newest version, run twice (from a terminal, then from the editor): the line above
    says "Outputs of run #3, <date time>: the latest run made with exactly this version" and each block shows its
    result under it with, on hover, the same tooltip as on the canvas (ending "results from <time>"); a later run of
    an uncommitted change counts for no version. The version before shows its own run's outputs; the oldest, never
    run, says "No run was made with exactly this version" and shows no result."""
    read = 'output = open("label.txt", encoding="utf-8").read().strip()'
    v0 = flow_bytes({"manual": MANUAL, "load": python("output = 0", 0)}, ["manual.start -> load.after"])
    v1 = flow_bytes({"manual": MANUAL, "load": python("output = 'v1 ' + " + read.split(" = ", 1)[1], 0)},
                    ["manual.start -> load.after"])
    v2 = flow_bytes({"manual": MANUAL, "load": python(read, 0), "total": python('output = input + " total"', 280,
                                                                                ["input"], y=60)},
                    ["manual.start -> load.after", "load.output -> total.input"],
                    {"note_1": {"text": "Nightly totals", "x": 0, "y": 260}})
    folder = tmp_path / "flows"
    when = dates(3)
    ids = make_history(folder, [("First try", {"report.yaml": v0}, when[0]), ("Read the label", {"report.yaml": v1},
                                                                             when[1]),
                                ("Add the total", {"report.yaml": v2}, when[2])])
    flow = folder / "report.yaml"
    label = folder / "label.txt"
    label.write_text("old", encoding="utf-8")
    run_v1 = run_with(env, flow, v1)
    label.write_text("early", encoding="utf-8")
    run(env, flow)
    label.write_text("late", encoding="utf-8")
    tab = win.open_flow(flow)
    menu(win, "▶ Run now").trigger()
    qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.last is not None, timeout=60_000)
    run_v2 = env.last_run()[0]
    assert run_v2 == 3 and env.last_run()[1] == "success" and flow.read_bytes() == v2
    canvas = {b: (hovered(qtbot, tab.view, tab.scene.nodes[b], scroll=True), tab.scene.nodes[b].status_text)
              for b in ("manual", "load", "total")}
    assert all("results from" in tip for tip, _ in canvas.values()), canvas
    label.write_text("stray", encoding="utf-8")
    run_with(env, flow, v2.replace(b"Nightly totals", b"Nightly totals, changed"))

    window = open_history(win, qtbot, "report.yaml")

    assert window.selected_id(ids) == ids[2]
    info = run_info(win, run_v2)
    line = window.run_line()
    said = re.fullmatch(rf"Outputs of run #{run_v2}, ({WHEN}): the latest run made with exactly this version", line)
    assert said and said[1] in (info["started"], info["finished"]), line
    assert window.drawn() == {"blocks": {"manual": (-280, 0), "load": (0, 0), "total": (280, 60)},
                              "notes": ["Nightly totals"], "edges": 2}
    view = window.preview
    for node in window.nodes().values():
        block = node.mapRectToScene(node.boundingRect().adjusted(4, 4, -4, -4))  # (without the selection's margin)
        box = view.mapFromScene(block).boundingRect()
        assert view.viewport().rect().contains(box), f"{node.block_id} is not in the pane: the flow is not fitted"
    nodes = window.nodes()
    for block, (tip, text) in canvas.items():
        assert nodes[block].status == "success" and nodes[block].status_text == text, (block, nodes[block].status_text)
        assert hovered(qtbot, view, nodes[block]) == tip, block
    assert "late total" in plain(canvas["total"][0])
    assert plain(canvas["total"][0]).endswith(f"results from {info['blocks']['total']}")

    window.click_card(1)

    def older():
        assert window.selected_id(ids) == ids[1] and set(window.nodes()) == {"manual", "load"}
        assert window.run_line().startswith(f"Outputs of run #{run_v1}, ")
    qtbot.waitUntil(older, timeout=3_000)
    tip = plain(hovered(qtbot, window.preview, window.nodes()["load"]))
    assert "v1 old" in tip and tip.endswith(f"results from {run_info(win, run_v1)['blocks']['load']}"), tip

    window.click_card(2)

    def never_run():
        assert window.selected_id(ids) == ids[0] and window.run_line() == NO_RUN
    qtbot.waitUntil(never_run, timeout=3_000)
    for node in window.nodes().values():
        assert node.status is None, (node.block_id, node.status, node.status_text)
        assert "results from" not in hovered(qtbot, window.preview, node)


OLD_HISTORY = """
CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, flow TEXT NOT NULL, flow_path TEXT NOT NULL,
    params TEXT NOT NULL, status TEXT NOT NULL, started TEXT NOT NULL, finished TEXT, pid INTEGER,
    started_by TEXT NOT NULL DEFAULT '', counts_for TEXT NOT NULL DEFAULT '');
CREATE TABLE blocks (run_id INTEGER NOT NULL, block TEXT NOT NULL, status TEXT NOT NULL, started TEXT, finished TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, error TEXT, summary TEXT, tables TEXT, previews TEXT, query TEXT,
    PRIMARY KEY (run_id, block));
CREATE TABLE logs (run_id INTEGER NOT NULL, block TEXT, ts TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL,
    show TEXT);
CREATE INDEX logs_run ON logs (run_id, block);
CREATE TABLE keeps (flow_path TEXT NOT NULL, block TEXT NOT NULL, keep TEXT, keep_failed INTEGER NOT NULL,
    PRIMARY KEY (flow_path, block));
"""


@needs_git
def test_runs_recorded_before_runs_kept_their_version_are_no_versions_run(env, win, qtbot, tmp_path):
    """Spec 1, 4b, 6: a run history from before this change (no version kept with its runs) holding a run of the
    flow: that run is no version's run (the older version says "No run was made with exactly this version", and its
    outputs match no search); a run made now in that same history file keeps its version (the newest's line names
    it)."""
    v1 = flow_bytes({"manual": MANUAL, "load": python("output = 'from before'", 0)}, ["manual.start -> load.after"])
    v2 = flow_bytes({"manual": MANUAL, "load": python("output = 'from now on'", 0)}, ["manual.start -> load.after"])
    ids = make_history(tmp_path / "flows", [("Load it", {"report.yaml": v1}, dates(2)[0]),
                                            ("Load it again", {"report.yaml": v2}, dates(2)[1])])
    flow = real_path(tmp_path / "flows" / "report.yaml")
    yesterday = (dt.datetime.now() - dt.timedelta(days=1)).isoformat(timespec="milliseconds")
    with sqlite3.connect(env.home / "history.db") as db:
        db.executescript(OLD_HISTORY)
        db.execute("INSERT INTO runs (flow, flow_path, params, status, started, finished, started_by, counts_for) "
                   "VALUES ('report', ?, '{}', 'success', ?, ?, 'manual', 'manual')", (str(flow), yesterday, yesterday))
        for block, summary, previews in (("manual", "start: yesterday", '{"start": "yesterday"}'),
                                         ("load", "output: 'from before'", '{"output": "from before"}')):
            db.execute("INSERT INTO blocks (run_id, block, status, finished, attempts, summary, tables, previews) "
                       "VALUES (1, ?, 'success', ?, 1, ?, '{}', ?)", (block, yesterday, summary, previews))
    db.close()

    now = run(env, flow)

    assert now == 2 and blob_id(v2) in recorded(env, now)
    win.open_flow(flow)
    window = open_history(win, qtbot, "report.yaml")
    assert window.run_line().startswith(f"Outputs of run #{now}, ")
    window.click_card(1)

    def older():
        assert window.selected_id(ids) == ids[0] and window.run_line() == NO_RUN
    qtbot.waitUntil(older, timeout=3_000)
    assert all(node.status is None for node in window.nodes().values())

    window.type("from before")

    def in_code_only():
        assert window.ids(ids) == [ids[0]] and "in the code of load" in window.cards()[0], window.cards()
    qtbot.waitUntil(in_code_only, timeout=3_000)


@needs_git
def test_a_block_type_that_is_not_installed_is_drawn_as_an_unknown_block_as_on_the_canvas(win, qtbot, tmp_path):
    """Spec 6: a version with a block whose type is not installed: the window opens, and the preview draws it as the
    canvas does, as an unknown block."""
    data = flow_bytes({"manual": MANUAL, "mystery": {"type": "custom.missing", "config": {"x": 1},
                                                     "ui": {"x": 0, "y": 0}}}, ["manual.start -> mystery.after"])
    make_history(tmp_path / "flows", [("Add a block of another computer", {"report.yaml": data}, dates(1)[0])])
    tab = win.open_flow(tmp_path / "flows" / "report.yaml")

    window = open_history(win, qtbot, "report.yaml")

    drawn, canvas = window.nodes()["mystery"], tab.scene.nodes["mystery"]
    assert not drawn.known and (drawn.known, drawn.type_title) == (canvas.known, canvas.type_title)
    assert win.message_boxes == []


@needs_git
def test_nothing_in_the_preview_can_be_changed_moved_deleted_connected_pasted_into_or_run(env, win, qtbot,
                                                                                         tmp_path):
    """Spec 6: in the preview, dragging a block, dragging from an output to an input, Delete and Backspace on a
    clicked block, Ctrl+V (blocks copied in the editor), double-clicking a text and typing, F5 and a right-click
    (no editing menu) change nothing: the preview draws the same flow, the editor's tab is unchanged, nothing is
    saved, committed or run."""
    data = flow_bytes({"manual": MANUAL, "load": python("output = 1", 0), "total": python("output = input", 280,
                                                                                          ["input"])},
                      ["manual.start -> load.after", "load.output -> total.input"],
                      {"note_1": {"text": "Nightly totals", "x": 0, "y": 260}})
    folder = tmp_path / "flows"
    make_history(folder, [("Add the total", {"report.yaml": data}, dates(1)[0])])
    tab = win.open_flow(folder / "report.yaml")
    tab.scene.clearSelection()
    tab.scene.nodes["load"].setSelected(True)
    win.copy_blocks()  # (for Ctrl+V)
    tab_before, files_before = copy.deepcopy(tab.doc.data), snapshot(folder)
    window = open_history(win, qtbot, "report.yaml")
    drawn = window.drawn()
    window.activate(qtbot)
    view = window.preview
    viewport = view.viewport()
    menus = []

    def close_menus():
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMenu) and widget.isVisible():
                menus.append([shown(a.text()) for a in widget.actions() if a.text()])
                widget.close()

    watch = QTimer(win, interval=20)
    watch.timeout.connect(close_menus)
    watch.start()
    try:
        def point(node, x=None, y=10.0):
            return view.mapFromScene(node.mapToScene(QPointF(node.boundingRect().center().x() if x is None else x, y)))

        load = window.nodes()["load"]
        a, b = point(load), point(load) + QPointF(90, 70).toPoint()
        QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, a)
        QTest.mouseMove(viewport, (a + b) / 2)
        QTest.mouseMove(viewport, b)
        QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, b)
        settle()
        nodes = window.nodes()
        start = view.mapFromScene(nodes["manual"].ports[("start", True)].scenePos())
        after = view.mapFromScene(nodes["total"].ports[("after", False)].scenePos())
        QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(viewport, (start + after) / 2)
        QTest.mouseMove(viewport, after)
        QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, after)
        settle()
        QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, point(window.nodes()["total"]))
        for key in (Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_F5):
            QTest.keyClick(viewport, key)
        QTest.keySequence(viewport, QKeySequence(QKeySequence.Paste))
        settle()
        note = next(i for i in view.scene().items() if isinstance(i, NoteItem))
        at = view.mapFromScene(note.sceneBoundingRect().center())
        QTest.mouseDClick(viewport, Qt.LeftButton, Qt.NoModifier, at)
        QTest.keyClicks(viewport, "xyz")
        empty = viewport.rect().bottomRight() - QPointF(5, 5).toPoint()  # (the text left, as you would)
        QTest.mouseClick(viewport, Qt.LeftButton, Qt.NoModifier, empty)
        settle()
        on = point(window.nodes()["load"])
        QApplication.sendEvent(viewport, QContextMenuEvent(QContextMenuEvent.Mouse, on, viewport.mapToGlobal(on)))
        settle()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:  # (a menu opened late is still seen)
            settle()
    finally:
        watch.stop()
        close_menus()
    editing = ("Run", "Delete", "Paste", "Add text", "Create Reusable block", "Results of this block")
    assert not [items for items in menus if any(i.startswith(editing) for i in items)], menus
    assert window.drawn() == drawn
    assert tab.doc.data == tab_before and not tab.doc.dirty
    assert tab.runs == []
    assert not (env.home / "history.db").exists() or env.query("SELECT count(*) FROM runs") == [(0,)]
    assert snapshot(folder) == files_before


# --- 7: Restore --------------------------------------------------------------------------------------------------

@needs_git
def test_restore_puts_the_version_in_the_flows_tab_as_one_undoable_change_then_save_and_commit_commits_it_on_top(
        win, qtbot, tmp_path):
    """Spec 7, 8 (the user's case): Restore this version (enabled only with a card selected) puts the selected
    version into the flow's tab, also while another flow's tab is on screen: one change, shown as modified, the
    status bar says "Restored the version of <date time> (<message>): Save or Save & Commit to keep it"; nothing is
    written, Git is unchanged. Undo brings back what the tab held, its unsaved change too; Redo the version again.
    File → Save saves it; File → Save & Commit… commits it on top of the history, and the open window lists that
    commit at the top, the older ones still there."""
    v1 = flow_bytes({"manual": MANUAL, "load": python("output = 1", 0)}, ["manual.start -> load.after"])
    v2 = flow_bytes({"manual": MANUAL, "load": python("output = 1", 0), "total": python("output = input", 280,
                                                                                         ["input"])},
                    ["manual.start -> load.after", "load.output -> total.input"])
    v3 = flow_bytes({"manual": MANUAL, "load": python("output = 1", 0), "total": python("output = input", 280,
                                                                                         ["input"], y=120)},
                    ["manual.start -> load.after", "load.output -> total.input"])
    folder = tmp_path / "flows"
    when = dates(3)
    ids = make_history(folder, [("Load the numbers\n\nThe first version.", {"report.yaml": v1}, when[0]),
                                ("Add the totals", {"report.yaml": v2}, when[1]),
                                ("Move the totals", {"report.yaml": v3}, when[2])])
    flow = folder / "report.yaml"
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "nightly.yaml").write_bytes(flow_bytes({"manual": MANUAL}, name="nightly"))
    tab = win.open_flow(flow)
    window = open_history(win, qtbot, "report.yaml")
    tab.doc.move_blocks({"load": (40, 40)})  # an unsaved change
    held = copy.deepcopy(tab.doc.data)
    assert window.restore.isEnabled()
    window.type("nothing is called this")
    qtbot.waitUntil(lambda: window.cards() == [] or window.selected() is None, timeout=3_000)
    assert window.selected() is None and not window.restore.isEnabled()
    window.type("")
    qtbot.waitUntil(lambda: window.selected() == 0, timeout=3_000)
    window.click_card(2)
    qtbot.waitUntil(lambda: window.selected_id(ids) == ids[0], timeout=3_000)
    other = win.open_flow(tmp_path / "other" / "nightly.yaml")
    other_before = copy.deepcopy(other.doc.data)
    files_before = snapshot(folder)

    QTest.mouseClick(window.restore, Qt.LeftButton)
    settle()

    first = yaml.safe_load(v1)
    assert tab.doc.data == first and tab.doc.dirty
    assert win.tabs.tabText(win.tabs.indexOf(tab)).endswith("*")
    assert set(tab.scene.nodes) == {"manual", "load"}
    assert other.doc.data == other_before and not other.doc.dirty
    assert status_bar(win) == (f"Restored the version of {local(when[0])} (Load the numbers): Save or Save & Commit "
                               "to keep it")
    assert snapshot(folder) == files_before
    assert win.message_boxes == []

    win.tabs.setCurrentWidget(tab)
    menu(win, "Undo").trigger()
    assert tab.doc.data == held and tab.doc.dirty
    menu(win, "Redo").trigger()
    assert tab.doc.data == first

    menu(win, "Save").trigger()
    settle()
    assert flow.read_bytes() == v1 and not tab.doc.dirty  # the very bytes: the same version, keys in its order
    assert count(folder) == 3

    save_and_commit(win, "Back to the first version")

    assert count(folder) == 4 and git(folder, "rev-parse", "HEAD^").strip() == ids[2]
    assert git(folder, "rev-parse", "HEAD:report.yaml") == git(folder, "rev-parse", f"{ids[0]}:report.yaml")
    assert subjects(folder, "report.yaml") == ["Back to the first version", "Move the totals", "Add the totals",
                                               "Load the numbers"]
    newest = head(folder)
    every = [newest, *ids[::-1]]

    def on_top():
        assert window.ids(every) == every
        assert "Back to the first version" in window.cards()[0]
    qtbot.waitUntil(on_top, timeout=5_000)


# --- 8: the window while you work --------------------------------------------------------------------------------

@needs_git
def test_a_save_and_commit_while_the_window_is_open_adds_the_commit_at_the_top_and_keeps_the_search(win, qtbot,
                                                                                                    tmp_path):
    """Spec 8: with "totals" typed in the search field, a Save & Commit of the flow ("Fix the totals") adds its
    commit at the top of the list, still filtered by "totals"."""
    v1 = flow_bytes({"manual": MANUAL, "load": python("output = 1", 0)}, ["manual.start -> load.after"])
    v2 = flow_bytes({"manual": MANUAL, "load": python("output = 2", 0)}, ["manual.start -> load.after"])
    folder = tmp_path / "flows"
    ids = make_history(folder, [("Load the totals", {"report.yaml": v1}, dates(2)[0]),
                                ("Rename the report", {"report.yaml": v2}, dates(2)[1])])
    tab = win.open_flow(folder / "report.yaml")
    window = open_history(win, qtbot, "report.yaml")
    window.type("totals")
    qtbot.waitUntil(lambda: window.ids(ids) == [ids[0]], timeout=3_000)

    tab.doc.move_blocks({"load": (60, 30)})
    win.tabs.setCurrentWidget(tab)
    save_and_commit(win, "Fix the totals")

    newest = head(folder)
    assert count(folder) == 3

    def on_top():
        assert window.ids([newest, *ids]) == [newest, ids[0]]
        assert "Fix the totals" in window.cards()[0]
    qtbot.waitUntil(on_top, timeout=5_000)
    assert window.field.text() == "totals"


# --- 9: light and dark -------------------------------------------------------------------------------------------

@needs_git
def test_the_window_shows_the_theme_chosen_dark_and_then_light(look_kept, win, qtbot, tmp_path):
    """Spec 9: with Dark chosen in Settings, the window, its cards and its preview are dark with text that stands
    out; Light chosen while it is open makes them light."""
    data = flow_bytes({"manual": MANUAL, "load": python("output = 1", 0)}, ["manual.start -> load.after"],
                      {"note_1": {"text": "Nightly totals", "x": 0, "y": 200}})
    make_history(tmp_path / "flows", [("Load the numbers", {"report.yaml": data}, dates(1)[0])])
    win.open_flow(tmp_path / "flows" / "report.yaml")
    choose(win, "Dark")

    window = open_history(win, qtbot, "report.yaml")

    assert not (off := not_in_theme(window.window, dark=True)), "\n".join(off)
    assert is_dark(most_common(window.preview.viewport().grab().toImage(), step=4))

    choose(win, "Light")
    settle()

    assert not (off := not_in_theme(window.window, dark=False)), "\n".join(off)
    assert not is_dark(most_common(window.preview.viewport().grab().toImage(), step=4))


# --- 10: README --------------------------------------------------------------------------------------------------

def test_the_readme_tells_of_commit_history_where_it_tells_of_save_and_commit():
    """Spec 10: in the README's section on Save & Commit, a paragraph on Commit History: the search (message, then
    outputs, then code, then the flow file), the preview with the outputs, and Restore."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    sections = re.split(r"\n(?=## )", text)
    (section,) = [s for s in sections if s.startswith("## ") and "Save & Commit" in s.splitlines()[0]]
    paragraphs = [p[p.index("Commit History"):] for p in re.split(r"\n\s*\n", section) if "Commit History" in p]
    assert paragraphs, "the Save & Commit section does not tell of Commit History"

    def tells(paragraph: str) -> bool:
        lower, at = paragraph.lower(), 0
        for word in ("message", "output", "code", "file"):  # in this order
            at = lower.find(word, at)
            if at < 0:
                return False
        return "preview" in lower and "restore" in lower

    assert any(tells(p) for p in paragraphs), paragraphs
