"""Tools > Connections, a database (JDBC) connection: its driver jar(s) come first and fill in the Driver class however
they are given (chosen with …, typed or pasted), and the window warns when the connection works only because another
connection lists the jar holding its driver.

Run for real: the offscreen editor's Connections window, typed into key by key, and its Test button (a real H2)."""

import os
import re
import zipfile

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFormLayout, QInputDialog, QLabel,  # noqa: E402
                               QLineEdit, QListView, QListWidget, QMessageBox, QPushButton, QToolButton, QWidget)

JARS, DRIVER_CLASS = "Driver .jar file(s)", "Driver class"
JDBC_ROWS = [JARS, DRIVER_CLASS, "URL", "User name", "Password"]
DRIVER, OTHER = "org.example.jdbc.Driver", "org.example.other.Driver"


# --- helpers ---------------------------------------------------------------------------------------------------------

def settle():
    for _ in range(3):
        QApplication.processEvents()


def driver_jar(path, *classes):
    """A driver .jar holding `classes`, each listed in META-INF/services/java.sql.Driver as a JDBC 4 driver lists its
    class (none: an older driver, which lists nothing)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as jar:
        jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n\r\n")
        if classes:
            jar.writestr("META-INF/services/java.sql.Driver", "".join(f"{name}\n" for name in classes))
        for name in classes or ("org.example.old.Driver",):
            jar.writestr(name.replace(".", "/") + ".class", b"\xca\xfe\xba\xbe")
    return path


def jdbc(jars, driver=DRIVER, db="db") -> dict:
    return {"kind": "jdbc", "driver": driver, "url": f"jdbc:example://localhost/{db}",
            "jars": [j.as_posix() for j in jars]}


def save_connections(win, conns: dict):
    win.home.root.mkdir(parents=True, exist_ok=True)
    (win.home.root / "connections.yaml").write_text(yaml.safe_dump(conns, sort_keys=False), encoding="utf-8")


def saved(win) -> dict:
    return yaml.safe_load((win.home.root / "connections.yaml").read_text(encoding="utf-8"))


def press(dialog, text):
    (button,) = [b for b in dialog.findChildren(QPushButton) if b.text().replace("&", "") == text]
    button.click()


def open_connections(win, qtbot, monkeypatch, select=None, new=None):
    """Tools > Connections, shown and active (to type in); `new`: then New… names a connection so."""
    from taskloom.editor.dialogs import ConnectionsDialog

    dialog = ConnectionsDialog(win.home, win, select=select)
    qtbot.addWidget(dialog)
    dialog.show()
    if new:
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (new, True)))
        press(dialog, "New…")
    dialog.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is dialog, timeout=5000)
    settle()
    return dialog


def _widgets(item) -> list:
    """The widgets a form item shows: its widget and those in it, or those of its layout."""
    if item is None:
        return []
    if item.widget() is not None:
        return [item.widget(), *item.widget().findChildren(QWidget)]
    layout = item.layout()
    return [w for i in range(layout.count()) for w in _widgets(layout.itemAt(i))] if layout is not None else []


def _label(form, row) -> str:
    item = form.itemAt(row, QFormLayout.LabelRole)
    return item.widget().text() if item is not None and isinstance(item.widget(), QLabel) else ""


def shown_rows(dialog) -> list[tuple[str, QWidget]]:
    """The form's rows as shown, top to bottom: (label, the field's first widget)."""
    settle()
    rows = []
    for form in dialog.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            shown = _widgets(form.itemAt(row, QFormLayout.FieldRole)) or _widgets(
                form.itemAt(row, QFormLayout.SpanningRole))
            if shown and shown[0].isVisibleTo(dialog):
                rows.append((_label(form, row), shown[0]))
    return rows


def field(dialog, label) -> QWidget:
    """The shown field labelled `label` (the text field in it, e.g. of a path and its … button)."""
    for name, widget in shown_rows(dialog):
        if name == label:
            return widget if isinstance(widget, QLineEdit) else widget.findChild(QLineEdit) or widget
    raise AssertionError(f"no field {label!r} shown: {[name for name, _ in shown_rows(dialog)]}")


def row_of(dialog, widget) -> int:
    """The form row the widget is shown on."""
    for form in dialog.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            for role in (QFormLayout.LabelRole, QFormLayout.FieldRole, QFormLayout.SpanningRole):
                if any(w is widget for w in _widgets(form.itemAt(row, role))):
                    return row
    return -1


def shown_labels(dialog) -> list[QLabel]:
    settle()
    return [label for label in dialog.findChildren(QLabel) if label.isVisibleTo(dialog) and label.text()]


def mentioning(dialog, word) -> list[str]:
    """What the window shows that names `word` (e.g. a connection)."""
    return [label.text() for label in shown_labels(dialog) if re.search(rf"\b{re.escape(word)}\b", label.text())]


def answering(action, answer=None) -> list:
    """Do action(); each window that opens meanwhile and waits for you is passed to answer(window), or closed.
    Returns the windows that opened."""
    seen, on = [], [True]

    def poll():
        if not on[0]:
            return
        opened = QApplication.activeModalWidget()
        if opened is not None and all(opened is not d for d in seen):
            seen.append(opened)
            try:
                if answer:
                    answer(opened)
            finally:
                if opened.isVisible():
                    opened.reject()
        QTimer.singleShot(20, poll)

    QTimer.singleShot(0, poll)
    try:
        action()
    finally:
        on[0] = False
    settle()
    return seen


def focus(qtbot, widget):
    widget.setFocus(Qt.MouseFocusReason)
    qtbot.waitUntil(widget.hasFocus, timeout=5000)


def type_in(qtbot, edit, text):
    """Click into the field and type `text` over what it holds, key by key."""
    focus(qtbot, edit)
    edit.selectAll()
    QTest.keyClicks(edit, text)
    settle()


def paste_in(qtbot, edit, text):
    """Click into the field and paste `text` over what it holds (the keyboard's paste)."""
    QApplication.clipboard().setText(text)
    focus(qtbot, edit)
    edit.selectAll()
    QTest.keySequence(edit, QKeySequence(QKeySequence.Paste))
    settle()


def leave(edit, answer=None) -> list:
    """Tab to the next field; returns the windows that opened meanwhile (see answering)."""
    return answering(lambda: QTest.keyClick(edit, Qt.Key_Tab), answer)


def choose_jars(dialog, monkeypatch, jars, answer=None) -> list:
    """Click the jar field's … and choose `jars`; returns the windows that opened meanwhile (see answering)."""
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        staticmethod(lambda *a, **k: ([str(j) for j in jars], "Jar files (*.jar)")))
    (browse,) = [b for b in dialog.findChildren(QToolButton) if b.text() == "…" and b.isVisibleTo(dialog)]
    return answering(browse.click, answer)


def offered(window) -> list[str]:
    """The choices a window lists (in a drop-down or a list)."""
    found = [[c.itemText(i) for i in range(c.count())] for c in window.findChildren(QComboBox)]
    found += [[view.model().index(r, 0).data() for r in range(view.model().rowCount())]
              for view in window.findChildren(QListView) if view.isVisibleTo(window)]
    return [text for texts in found for text in texts if text]


def pick(name, lists: list):
    """Answer a 'choose one' window: note what it lists, choose `name`, OK."""
    def answer(window):
        lists.append(offered(window))
        for combo in window.findChildren(QComboBox):
            if combo.findText(name) >= 0:
                combo.setCurrentIndex(combo.findText(name))
                break
        else:
            for view in window.findChildren(QListView):
                rows = [r for r in range(view.model().rowCount()) if view.model().index(r, 0).data() == name]
                if rows:
                    view.setCurrentIndex(view.model().index(rows[0], 0))
                    break
            else:
                return
        window.accept()
    return answer


def select(dialog, name):
    """Click the connection `name` in the window's list."""
    names = dialog.findChild(QListWidget)
    (item,) = names.findItems(name, Qt.MatchExactly)
    names.setCurrentItem(item)
    settle()


def click_test(dialog, qtbot) -> str:
    """Click Save and test connection; what it says when done."""
    press(dialog, "Save and test connection")
    (button,) = [b for b in dialog.findChildren(QPushButton) if b.text() == "Save and test connection"]
    qtbot.waitUntil(lambda: button.isEnabled() and dialog.test_result.text()
                    and "Testing" not in dialog.test_result.text(), timeout=60_000)
    return dialog.test_result.text()


# --- 1. the jars first, then the driver class, URL, user name and password -----------------------------------------

def test_a_jdbc_connection_shows_its_jars_first_then_driver_class_url_user_name_and_password_and_tabs_so(
        win, qtbot, monkeypatch):
    """Spec 1: row order Driver .jar file(s), Driver class, URL, User name, Password; Tab order follows; the Driver
    class stays editable."""
    dialog = open_connections(win, qtbot, monkeypatch, new="report_db")

    assert [name for name, _ in shown_rows(dialog) if name] == ["Kind", *JDBC_ROWS]

    fields = [field(dialog, name) for name in JDBC_ROWS]
    focus(qtbot, field(dialog, "Kind"))
    reached = []
    for _ in range(20):  # Tab from Kind through the form
        QTest.keyClick(QApplication.focusWidget(), Qt.Key_Tab)
        settle()
        now = QApplication.focusWidget()
        hit = next((name for name, f in zip(JDBC_ROWS, fields)
                    if now is f or (now not in (None, dialog) and (now.isAncestorOf(f) or f.isAncestorOf(now)))), None)
        if hit and hit not in reached:
            reached.append(hit)
        if len(reached) == len(JDBC_ROWS):
            break
    assert reached == JDBC_ROWS

    driver = field(dialog, DRIVER_CLASS)
    assert driver.isEnabled() and not driver.isReadOnly()
    type_in(qtbot, driver, "org.example.MyDriver")
    assert driver.text() == "org.example.MyDriver"


@pytest.mark.parametrize("how", ["chosen", "typed"])  # (chosen with the field's …)
def test_the_note_for_a_jar_naming_no_driver_class_is_just_under_the_driver_class_and_does_not_say_above(
        win, qtbot, monkeypatch, tmp_path, how):
    """Spec 1: the "not found" note sits just under the Driver class and no longer says "above" (the class is now
    below the jars); spec 2: a typed jar gives the note by the same rule as the … button."""
    old = driver_jar(tmp_path / "drivers" / "old-jdbc.jar")
    dialog = open_connections(win, qtbot, monkeypatch, new="report_db")
    before = {label.text() for label in shown_labels(dialog)}

    if how == "typed":
        jars = field(dialog, JARS)
        type_in(qtbot, jars, str(old))
        assert leave(jars) == []
    else:
        assert choose_jars(dialog, monkeypatch, [old]) == []

    rows = shown_rows(dialog)
    at = next(i for i, (name, _) in enumerate(rows) if name == DRIVER_CLASS)
    assert at + 1 < len(rows), rows
    name, note = rows[at + 1]
    assert isinstance(note, QLabel), f"no note just under the Driver class: the next row is {name!r}"
    assert note.text() not in before and re.search(r"driver", note.text(), re.I), note.text()
    assert not re.search(r"\babove\b", note.text(), re.I), note.text()
    assert field(dialog, DRIVER_CLASS).text() == ""


# --- 2. typed or pasted jars fill in the driver class once the field is done ----------------------------------------

def test_pasting_the_jar_path_fills_in_the_driver_class_once_you_leave_the_field(win, qtbot, monkeypatch, tmp_path):
    """Spec 2, the user's scenario: paste the jar path into Driver .jar file(s) (shown first), go on, and the Driver
    class below is filled in from the jar; not while the field is still being edited."""
    jar = driver_jar(tmp_path / "drivers" / "example-jdbc-4.2.jar", DRIVER)
    dialog = open_connections(win, qtbot, monkeypatch, new="report_db")
    jars, driver = field(dialog, JARS), field(dialog, DRIVER_CLASS)

    paste_in(qtbot, jars, str(jar))

    assert jars.text() == str(jar)
    assert driver.text() == "", "filled in while the field was still being edited"

    assert leave(jars) == []  # one class: nothing to ask

    assert driver.text() == DRIVER
    press(dialog, "Save")
    assert saved(win)["report_db"]["driver"] == DRIVER
    assert saved(win)["report_db"]["jars"] == [str(jar)]


def test_typing_jar_paths_and_pressing_enter_fills_in_the_driver_class_they_name(win, qtbot, monkeypatch, tmp_path):
    """Spec 2: typed path(s) (several, separated by ;) fill in the Driver class on Enter, by the … button's rules:
    the one jar that names a class gives it."""
    needs = driver_jar(tmp_path / "drivers" / "needs-1.0.jar")
    jar = driver_jar(tmp_path / "drivers" / "example-jdbc-4.2.jar", DRIVER)
    dialog = open_connections(win, qtbot, monkeypatch, new="report_db")
    jars, driver = field(dialog, JARS), field(dialog, DRIVER_CLASS)

    type_in(qtbot, jars, f"{needs}; {jar}")

    assert driver.text() == "", "filled in on a keystroke"

    opened = answering(lambda: QTest.keyClick(jars, Qt.Key_Return))

    assert dialog.isVisible(), "Enter saved and closed the Connections window"
    yaml_file = win.home.root / "connections.yaml"
    assert not yaml_file.exists() or "report_db" not in (saved(win) or {}), "Enter saved the connection"
    assert opened == []
    assert driver.text() == DRIVER
    press(dialog, "Save")
    assert saved(win)["report_db"]["driver"] == DRIVER


@pytest.mark.parametrize("done", ["Tab", "Enter"])
def test_typed_jars_naming_several_driver_classes_let_you_choose_one_once_typing_is_done(
        win, qtbot, monkeypatch, tmp_path, done):
    """Spec 2: a list to choose from, as with the … button, and only once the field is done (left or Enter; not per
    keystroke); Enter only finishes the field, it does not save and close the window."""
    jar = driver_jar(tmp_path / "drivers" / "example-jdbc.jar", DRIVER, "org.example.jdbc.LegacyDriver")
    dialog = open_connections(win, qtbot, monkeypatch, new="report_db")
    jars, driver = field(dialog, JARS), field(dialog, DRIVER_CLASS)
    lists = []

    while_typing = answering(lambda: type_in(qtbot, jars, str(jar)), pick("org.example.jdbc.LegacyDriver", lists))

    assert while_typing == [] and driver.text() == ""

    answer = pick("org.example.jdbc.LegacyDriver", lists)
    opened = leave(jars, answer) if done == "Tab" else answering(lambda: QTest.keyClick(jars, Qt.Key_Return), answer)

    assert len(opened) == 1 and lists, "no list to choose the driver class from"
    assert {DRIVER, "org.example.jdbc.LegacyDriver"} <= set(lists[0]), lists
    assert driver.text() == "org.example.jdbc.LegacyDriver"
    assert dialog.isVisible()
    press(dialog, "Save")
    assert saved(win)["report_db"]["driver"] == "org.example.jdbc.LegacyDriver"


def test_a_driver_class_you_typed_is_replaced_by_typed_jars_only_when_you_say_so(win, qtbot, monkeypatch, tmp_path):
    """Spec 2: the … button's rule for a different class already typed (asks before replacing) holds for typed
    jars too."""
    jar = driver_jar(tmp_path / "drivers" / "example-jdbc.jar", DRIVER)
    newer = driver_jar(tmp_path / "drivers" / "example-jdbc-4.3.jar", DRIVER)
    dialog = open_connections(win, qtbot, monkeypatch, new="report_db")
    jars, driver = field(dialog, JARS), field(dialog, DRIVER_CLASS)
    type_in(qtbot, driver, "com.example.MyDriver")
    leave(driver)
    asked = []

    def reply(yes):
        monkeypatch.setattr(QMessageBox, "question", staticmethod(
            lambda *a, **k: asked.append(a[1:3]) or (QMessageBox.Yes if yes else QMessageBox.No)))

        def answer(box):
            if isinstance(box, QMessageBox):
                asked.append(box.text())
                roles = ((QMessageBox.YesRole, QMessageBox.AcceptRole) if yes
                         else (QMessageBox.NoRole, QMessageBox.RejectRole))
                next(b for b in box.buttons() if box.buttonRole(b) in roles).click()
        return answer

    type_in(qtbot, jars, str(jar))
    leave(jars, reply(False))

    assert asked, "not asked whether to replace the driver class you typed"
    assert driver.text() == "com.example.MyDriver"

    asked.clear()
    type_in(qtbot, jars, str(newer))  # (another path: asked again whatever was asked before)
    leave(jars, reply(True))

    assert asked and driver.text() == DRIVER


def test_a_typed_jar_path_that_does_not_exist_is_said_next_to_the_field(win, qtbot, monkeypatch, tmp_path):
    """Spec 2: a path that does not exist is said so next to the jar field (no crash, not silently nothing); put
    right, the class is filled in and the remark goes."""
    missing = tmp_path / "drivers" / "missing-jdbc.jar"
    jar = driver_jar(tmp_path / "drivers" / "example-jdbc.jar", DRIVER)
    dialog = open_connections(win, qtbot, monkeypatch, new="report_db")
    jars, driver = field(dialog, JARS), field(dialog, DRIVER_CLASS)

    type_in(qtbot, jars, str(missing))
    opened = leave(jars)

    assert opened == []  # said in the window, not in another one to close
    told = [label for label in shown_labels(dialog) if "missing-jdbc.jar" in label.text()]
    assert told, [label.text() for label in shown_labels(dialog)]
    jar_row, driver_row = row_of(dialog, jars), row_of(dialog, driver)
    assert all(jar_row <= row_of(dialog, label) < driver_row for label in told), \
        ([(label.text(), row_of(dialog, label)) for label in told], jar_row, driver_row)
    assert driver.text() == ""

    type_in(qtbot, jars, str(jar))
    leave(jars)

    assert driver.text() == DRIVER
    assert not [label.text() for label in shown_labels(dialog) if "missing-jdbc.jar" in label.text()]


# --- 3. a warning when it works only through another connection's jars ----------------------------------------------

@pytest.mark.parametrize("own", ["no jars", "a jar of another driver"])
def test_a_connection_whose_driver_class_is_only_in_another_connections_jars_warns_naming_it(
        win, qtbot, monkeypatch, tmp_path, own):
    """Spec 3: the warning names the connection whose jars hold the class (not one whose jars hold another class),
    on that connection only."""
    own_jars = [] if own == "no jars" else [driver_jar(tmp_path / "report" / "other-jdbc.jar", OTHER)]
    save_connections(win, {  # (the one whose jars hold another class listed first)
        "nightly_db": jdbc([driver_jar(tmp_path / "drivers" / "other-jdbc.jar", OTHER)], OTHER, db="nightly"),
        "warehouse": jdbc([driver_jar(tmp_path / "drivers" / "example-jdbc.jar", DRIVER)], db="warehouse"),
        "report_db": jdbc(own_jars, DRIVER, db="report"),
    })

    dialog = open_connections(win, qtbot, monkeypatch, select="report_db")

    assert mentioning(dialog, "warehouse"), [label.text() for label in shown_labels(dialog)]
    assert not mentioning(dialog, "nightly_db")

    for other in ("warehouse", "nightly_db"):  # their own jars hold their classes
        select(dialog, other)
        assert not [t for name in ("warehouse", "nightly_db", "report_db") for t in mentioning(dialog, name)], other

    select(dialog, "report_db")
    assert mentioning(dialog, "warehouse")


@pytest.mark.parametrize("own", ["the same jar", "a copy of the jar"])
def test_no_warning_when_the_connections_own_jars_hold_its_driver_class(win, qtbot, monkeypatch, tmp_path, own):
    """Spec 3: not shown when this connection's own jars hold the class, even when another connection, listed
    first, has a jar holding it too."""
    shared = driver_jar(tmp_path / "drivers" / "example-jdbc.jar", DRIVER)
    mine = shared if own == "the same jar" else driver_jar(tmp_path / "report" / "example-jdbc.jar", DRIVER)
    save_connections(win, {"warehouse": jdbc([shared], db="warehouse"), "report_db": jdbc([mine], db="report")})

    dialog = open_connections(win, qtbot, monkeypatch, select="report_db")

    assert not mentioning(dialog, "warehouse"), mentioning(dialog, "warehouse")


def test_the_warning_follows_the_driver_class_and_jars_you_type(win, qtbot, monkeypatch, tmp_path):
    """Spec 3 while editing: a class typed with no jars of its own that only another connection's jar holds warns;
    once its own jar is given, the warning goes."""
    save_connections(win, {"warehouse": jdbc([driver_jar(tmp_path / "drivers" / "example-jdbc.jar", DRIVER)])})
    own = driver_jar(tmp_path / "report" / "example-jdbc.jar", DRIVER)
    dialog = open_connections(win, qtbot, monkeypatch, new="report_db")
    jars, driver = field(dialog, JARS), field(dialog, DRIVER_CLASS)
    assert not mentioning(dialog, "warehouse")

    type_in(qtbot, driver, DRIVER)
    leave(driver)

    assert mentioning(dialog, "warehouse"), [label.text() for label in shown_labels(dialog)]

    type_in(qtbot, jars, str(own))
    assert leave(jars) == []  # its class is the one typed: nothing to ask

    assert driver.text() == DRIVER
    assert not mentioning(dialog, "warehouse"), mentioning(dialog, "warehouse")
    press(dialog, "Save")
    assert saved(win)["report_db"]["jars"] == [str(own)] and saved(win)["report_db"]["driver"] == DRIVER


def test_test_says_the_connection_works_only_through_another_connections_jars(
        jdbc_env, h2_server, win, qtbot, monkeypatch):
    """Spec 3, Test: a connection with no jars of its own connects through warehouse's H2 jar (one class path), and
    Test says so naming warehouse; once its own jars hold the driver, Test says nothing of warehouse."""
    conns = yaml.safe_load((jdbc_env.home / "connections.yaml").read_text(encoding="utf-8"))
    conns["report_db"] = {"kind": "jdbc", "driver": "org.h2.Driver", "jars": [],
                          "url": f"jdbc:h2:tcp://localhost:{h2_server['port']}/mem:report_through_warehouse",
                          "properties": {"user": "sa"}}
    (jdbc_env.home / "connections.yaml").write_text(yaml.safe_dump(conns, sort_keys=False), encoding="utf-8")
    dialog = open_connections(win, qtbot, monkeypatch, select="report_db")

    tested = click_test(dialog, qtbot)

    assert "✓" in tested and "Connected to H2" in tested, tested
    assert re.search(r"\bwarehouse\b", tested), tested

    jars = field(dialog, JARS)
    type_in(qtbot, jars, str(h2_server["jar"]))
    assert leave(jars) == []
    tested = click_test(dialog, qtbot)

    assert "✓" in tested and "Connected to H2" in tested, tested
    assert not re.search(r"\bwarehouse\b", tested), tested


# --- 4. other kinds unchanged ---------------------------------------------------------------------------------------

def test_ssh_and_email_connections_keep_their_rows(win, qtbot, monkeypatch):
    """Spec 4: non-JDBC kinds unchanged (the shared form is reordered for JDBC only)."""
    dialog = open_connections(win, qtbot, monkeypatch, new="appserver")
    kind = field(dialog, "Kind")

    kind.setCurrentIndex(kind.findData("ssh"))
    assert [name for name, _ in shown_rows(dialog) if name] == ["Kind", "Host", "Port", "User name", "Key file",
                                                                "Password"]
    kind.setCurrentIndex(kind.findData("smtp"))
    assert [name for name, _ in shown_rows(dialog) if name] == ["Kind", "Host", "Port", "Security", "User name",
                                                                "Send as", "Password"]
