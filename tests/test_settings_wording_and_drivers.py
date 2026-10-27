"""Users never meet internal names: a block's settings are named in messages as Properties shows them; Copy, Move and
Delete Files show for each path whether it is on this computer or on a server (chosen from your SSH connections);
and the Connections window fills in a JDBC driver's class from the driver jar you choose.

Run for real: `taskloom validate | run | check`, the offscreen editor (Properties, Validate, Run, the Connections
window) and a real SSH server."""

import os
import re
import sqlite3
import subprocess
import sys
import zipfile

import pytest
import yaml
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFormLayout, QGroupBox, QInputDialog, QLabel,
                               QLineEdit, QListView, QMessageBox, QPushButton, QToolButton, QWidget)

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="the test SSH server runs commands in /bin/sh")
THIS_COMPUTER = "This computer"

# One mistake of each kind in a block's settings, with what Properties calls the setting.
BROKEN_FLOW = """\
blocks:
  start: {type: trigger.manual}
  copy:
    type: files.copy
    config: {destination: "out/"}
  code:
    type: logic.python
    config: {code: "output = 1", use_environment: "yes"}
  query:
    type: db.run_query
    config: {connection: nope, sql: "select * from sales where day = :day_key"}
  mail:
    type: email.send
    config: {connection: mailer, subject: "Sales {region}", message: "Total {"}
  cmd:
    type: servers.ssh_command
    config: {connection: mailer, command: uptime}
edges:
  - start.start -> copy.after
  - start.start -> code.after
  - start.start -> query.after
  - start.start -> mail.after
  - start.start -> cmd.after
"""
MAILER = "mailer: {kind: smtp, host: 127.0.0.1, port: 2525, sender: taskloom@example.com}\n"
EXPECTED = [  # (block, the setting as Properties labels it, what else the message has)
    ("copy", "Source", "required"),  # what is missing
    ("code", "Run in my Python environment", "expected true or false"),  # a wrong value
    ("query", "Connection", "'nope'"),  # an unknown connection
    ("query", "SQL", ":day_key"),  # an unknown bind parameter
    ("mail", "Subject", "{region}"),  # an unknown parameter in a template
    ("mail", "Message", ""),  # a template that cannot be read
    ("cmd", "Connection", "'mailer'"),  # a connection of another kind
]


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def assert_named_as_shown(text):
    """Each mistake of BROKEN_FLOW is told naming the block and the setting as Properties labels it."""
    assert "config." not in text, text
    assert re.search(r"\bcopy: ['\"]?Source\b.*\brequired\b", text), text  # e.g. copy: Source is required
    lines = text.splitlines()
    for block, label, detail in EXPECTED:
        assert any(re.search(rf"\b{block}\b", line) and label in line and detail in line for line in lines), \
            (block, label, detail, text)


def settle():
    for _ in range(3):
        QApplication.processEvents()


def clean(win):
    """Leave no unsaved changes: closing the window at the end of the test must not ask to save them."""
    for tab in win.tabs_list():
        tab.doc.dirty = False


def select_only(tab, block):
    tab.scene.clearSelection()
    tab.scene.nodes[block].setSelected(True)
    settle()


def labelled(root, label):
    """The field on the form row labelled `label` (a box's text field, e.g. a path and its … button)."""
    for form in root.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            name, value = form.itemAt(row, QFormLayout.LabelRole), form.itemAt(row, QFormLayout.FieldRole)
            if name and value and isinstance(name.widget(), QLabel) and name.widget().text() == label:
                widget = value.widget()
                return widget.findChild(QLineEdit) if type(widget) is QWidget and widget.findChild(QLineEdit) else widget
    raise AssertionError(f"no field {label!r}")


def shown_texts(root) -> set[str]:
    """What the labels in `root` show (e.g. the message under a block's name in Properties)."""
    settle()
    return {label.text() for label in root.findChildren(QLabel) if label.isVisibleTo(root) and label.text()}


def setting_labels(win) -> set[str]:
    """The names Properties shows for the selected block's settings (rows and boxes, not its name or retry)."""
    body = win.properties.widget()
    retry = [box for box in body.findChildren(QGroupBox) if box.title().startswith("Retry")]
    names = {box.title() for box in body.findChildren(QGroupBox) if box not in retry}
    for form in body.findChildren(QFormLayout):
        if form.parentWidget() in retry:
            continue
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.LabelRole)
            if item and isinstance(item.widget(), QLabel) and item.widget().text():
                names.add(item.widget().text())
    return names - {"Name"}


# --- 1. a setting is named as Properties shows it ----------------------------------------------------------------

@pytest.mark.parametrize("command", ["validate", "run", "check"])
def test_the_command_line_names_each_setting_as_properties_shows_it(env, command):
    write(env.home / "connections.yaml", MAILER)
    flow = write(env.root / "flow.yaml", BROKEN_FLOW)

    result = env.taskloom(command, flow)

    assert result.returncode == 2, result.stdout + result.stderr
    assert_named_as_shown(result.stdout + result.stderr)


def test_the_editor_names_each_setting_as_properties_shows_it(win, tmp_path):
    """The user's case: Copy, Move and Delete Files just added say what is missing in the words of Properties; and
    the messages under a block, of Validate and of Run never say config."""
    from test_editor import add_from_palette

    tab = win.new_flow()
    try:
        for title, block in (("Copy Files", "copy"), ("Move Files", "move"), ("Delete Files", "delete")):
            add_from_palette(win, title)
            select_only(tab, block)
            texts = shown_texts(win.properties.widget())
            problems = [t for t in texts if "required" in t]
            assert problems and not any("config." in t for t in texts), (title, texts)
            labels = setting_labels(win)
            assert any(label in problems[0] for label in labels), (title, problems, labels)
            if block != "delete":
                assert "Source" in problems[0], problems
    finally:
        clean(win)

    write(win.home.root / "connections.yaml", MAILER)
    tab = win.open_flow(write(tmp_path / "broken.yaml", BROKEN_FLOW))
    select_only(tab, "code")
    texts = shown_texts(win.properties.widget())
    assert any("Run in my Python environment" in t and "expected true or false" in t for t in texts), texts
    assert not any("config." in t for t in texts), texts

    win.validate(tab)
    win.run_flow(tab)

    said = list(win.message_boxes)
    win.message_boxes.clear()
    assert [(kind, title) for kind, (title, _) in said] == [("warning", "The flow has problems")] * 2
    for _, (_, text) in said:
        assert_named_as_shown(text)
    assert_named_as_shown(win.log.text.toPlainText())
    assert not tab.runs  # refused before it starts


def test_a_wrong_value_typed_in_properties_is_told_with_the_name_properties_shows(win, tmp_path):
    flow = write(tmp_path / "query.yaml", "blocks:\n  start: {type: trigger.manual}\n  query:\n    type: db.run_query\n"
                                          "    config: {sql: select 1}\nedges: ['start.start -> query.after']\n")
    tab = win.open_flow(flow)
    select_only(tab, "query")
    body = win.properties.widget()
    try:
        for typed in ("abc", "0"):  # not a number; a number below the smallest allowed
            before = shown_texts(body)
            edit = labelled(body, "Chunk size")
            edit.setText(typed)
            edit.editingFinished.emit()

            told = shown_texts(body) - before
            assert told, f"nothing said about {typed!r}"
            assert all("Chunk size" in t and "chunk_size" not in t and "config." not in t for t in told), told
    finally:
        clean(win)


# --- 2. where each path of the file blocks is: this computer or a server -------------------------------------------

def locations(win) -> list[tuple[QComboBox, QLineEdit]]:
    """Each path of the selected file block, top to bottom as Properties shows it: the choice of where it is
    (This computer or a server) and the path's own field."""
    settle()
    body = win.properties.widget()

    def top(w):
        return w.mapTo(body, QPoint(0, 0)).y()

    def other(edit):  # the retry box's fields, or the text field inside a choice
        parent = edit.parentWidget()
        while parent is not None and parent is not body:
            if isinstance(parent, QComboBox) or (isinstance(parent, QGroupBox) and parent.title().startswith("Retry")):
                return True
            parent = parent.parentWidget()
        return False

    name = labelled(body, "Name")
    wheres = sorted((c for c in body.findChildren(QComboBox) if c.isVisibleTo(body) and c.findText(THIS_COMPUTER) >= 0),
                    key=top)
    paths = sorted((e for e in body.findChildren(QLineEdit) if e.isVisibleTo(body) and e is not name and not other(e)),
                   key=top)
    assert len(wheres) == len(paths), ([items(c) for c in wheres], [e.text() for e in paths])
    return list(zip(wheres, paths))


def items(combo) -> list[str]:
    return [combo.itemText(i) for i in range(combo.count())]


def tip(widget, root) -> str:
    """The help shown when hovering over the widget (or the box it is in)."""
    while widget is not None and widget is not root:
        if widget.toolTip():
            return widget.toolTip()
        widget = widget.parentWidget()
    return ""


def set_locations(win, tab, block, wanted):
    """In Properties, choose where each path of `block` is and type it: wanted = [(where, path), ...]."""
    select_only(tab, block)
    for i, (where, path) in enumerate(wanted):
        combo = locations(win)[i][0]
        if combo.currentText() != where:
            index = combo.findText(where)
            assert index >= 0, (where, items(combo))
            combo.setCurrentIndex(index)
            combo.activated.emit(index)
            combo.textActivated.emit(where)
        edit = locations(win)[i][1]
        edit.setText(path)
        edit.editingFinished.emit()
    settle()


@pytest.mark.parametrize("title, block_id, count", [("Copy Files", "copy", 2), ("Move Files", "move", 2),
                                                    ("Delete Files", "delete", 1)])
def test_each_path_of_a_file_block_shows_this_computer_or_one_of_your_ssh_connections(win, title, block_id, count):
    from test_editor import add_from_palette

    write(win.home.root / "connections.yaml",
          "appserver: {kind: ssh, host: server01.example.com, username: me}\n"
          "backup: {kind: ssh, host: server02.example.com, username: me}\n"
          "warehouse: {kind: jdbc, driver: org.example.Driver, url: 'jdbc:example://db', jars: []}\n" + MAILER)
    tab = win.new_flow()
    try:
        add_from_palette(win, title)
        select_only(tab, block_id)
        body = win.properties.widget()

        found = locations(win)

        assert len(found) == count, title
        for where, path in found:
            assert where.currentText() == THIS_COMPUTER  # a new block's paths are on this computer
            offered = items(where)
            assert {THIS_COMPUTER, "appserver", "backup"} <= set(offered), offered
            assert not any("warehouse" in o or "mailer" in o for o in offered), offered  # servers only (SSH)
            help_text = tip(path, body)
            assert help_text and "connection:path" not in help_text, help_text
        if count == 2:
            assert tip(labelled(body, "Overwrite"), body), "Overwrite says nothing about what it does"
    finally:
        clean(win)


def cli(home, *args, cwd):
    return subprocess.run([sys.executable, "-m", "taskloom.cli", *map(str, args)], cwd=cwd, capture_output=True,
                          text=True, errors="replace", timeout=120,
                          env={**os.environ, "TASKLOOM_HOME": str(home),
                               "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"})


def said_about(home, result, block) -> str:
    """What a command said about `block`: its lines, and its error in the run it recorded (if one started)."""
    lines = [line for line in (result.stdout + result.stderr).splitlines() if re.search(rf"\b{block}\b", line)]
    db = home / "history.db"
    if db.exists():
        with sqlite3.connect(db) as con:
            lines += [e for (e,) in con.execute("SELECT error FROM blocks WHERE block=? AND error IS NOT NULL "
                                                "AND run_id=(SELECT MAX(id) FROM runs)", (block,))]
    return "\n".join(lines)


@POSIX
def test_files_on_a_server_chosen_in_properties_are_fetched_sent_and_deleted(server, win, qtbot, tmp_path):
    """The usual job, set up in Properties only: fetch a report from a server, send it on to another folder there,
    delete it from the server's outbox."""
    write(server.home_path / "outbox" / "report.csv", "day,total\n1,10\n")
    flow = write(tmp_path / "files.yaml", """\
blocks:
  start: {type: trigger.manual}
  fetch: {type: files.copy}
  send: {type: files.move}
  clean: {type: files.delete}
edges:
  - start.start -> fetch.after
  - fetch.files -> send.after
  - send.files -> clean.after
""")
    tab = win.open_flow(flow)
    try:
        set_locations(win, tab, "fetch", [("appserver", "~/outbox/report.csv"), (THIS_COMPUTER, "inbox/")])
        set_locations(win, tab, "send", [(THIS_COMPUTER, "inbox/report.csv"), ("appserver", "~/archive/")])
        set_locations(win, tab, "clean", [("appserver", "~/outbox/report.csv")])
        assert win.save(tab)
    finally:
        clean(win)

    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.runs and not tab.running and tab.last is not None, timeout=60_000)

    assert tab.last["status"] == "success", win.log.text.toPlainText()
    assert (server.home_path / "archive" / "report.csv").read_text(encoding="utf-8") == "day,total\n1,10\n"
    assert not (server.home_path / "outbox" / "report.csv").exists()
    assert (tmp_path / "inbox").is_dir() and not (tmp_path / "inbox" / "report.csv").exists()  # moved on
    for block, shown in (("fetch", ["appserver", THIS_COMPUTER]), ("send", [THIS_COMPUTER, "appserver"]),
                         ("clean", ["appserver"])):
        select_only(tab, block)
        assert [where.currentText() for where, _ in locations(win)] == shown, block

    # The server's connection renamed since: the flow says which connection is missing, in the words of Properties.
    connections = win.home.root / "connections.yaml"
    connections.write_text(connections.read_text(encoding="utf-8").replace("appserver:", "app_server:", 1),
                           encoding="utf-8")
    result = cli(win.home.root, "run", flow, cwd=tmp_path)
    assert result.returncode != 0
    told = said_about(win.home.root, result, "fetch")
    assert "appserver" in told and "config." not in told, result.stdout + result.stderr + told


@POSIX
def test_a_flow_with_paths_written_connection_colon_path_fails_saying_what_to_change(env, server):
    """That form is gone: a flow that still has it is not run as if the path were on this computer."""
    write(server.home_path / "outbox" / "report.csv", "r")
    write(env.root / "out" / "a.csv", "a")
    flow = write(env.root / "flow.yaml", """\
blocks:
  start: {type: trigger.manual}
  fetch:
    type: files.copy
    config: {source: "appserver:~/outbox/report.csv", destination: "inbox/"}
  upload:
    type: files.copy
    config: {source: "out/a.csv", destination: "appserver:~/incoming/"}
  clean:
    type: files.delete
    config: {path: "appserver:~/outbox/report.csv"}
edges:
  - start.start -> fetch.after
  - start.start -> upload.after
  - start.start -> clean.after
""")

    result = env.taskloom("run", flow)

    assert result.returncode != 0, "the old form was taken as it was"
    for block, label in (("fetch", "Source"), ("upload", "Destination"), ("clean", "")):
        told = said_about(env.home, result, block)
        assert "appserver" in told and label in told and "config." not in told, (block, result.stderr, told)
    assert (server.home_path / "outbox" / "report.csv").read_text(encoding="utf-8") == "r"
    assert not (server.home_path / "incoming").exists()
    assert not (env.root / "inbox").exists()
    assert not any("appserver" in p.name for p in env.root.iterdir()), "made a local folder named after the server"


# --- 3. the driver class, from the driver jar ----------------------------------------------------------------------

def driver_jar(path, services: str | None):
    """A driver .jar; `services`: its META-INF/services/java.sql.Driver (None: an old driver without one)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as jar:
        jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n\r\n")
        if services is not None:
            jar.writestr("META-INF/services/java.sql.Driver", services)
        jar.writestr("org/example/jdbc/Driver.class", b"\xca\xfe\xba\xbe")
    return path


def press(dialog, text):
    (button,) = [b for b in dialog.findChildren(QPushButton) if b.text().replace("&", "") == text]
    button.click()


def new_jdbc_connection(win, monkeypatch):
    """Tools > Connections, New… warehouse: a database (JDBC) connection."""
    from taskloom.editor.dialogs import ConnectionsDialog

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("warehouse", True)))
    dialog = ConnectionsDialog(win.home, win)
    dialog.show()
    press(dialog, "New…")
    assert labelled(dialog, "Kind").currentData() == "jdbc"
    return dialog


def driver_class(dialog) -> str:
    field = labelled(dialog, "Driver class")
    return field.currentText() if isinstance(field, QComboBox) else field.text()


def choose_jars(dialog, monkeypatch, jars, answer=None) -> list:
    """Click the jar field's … and choose `jars`; each dialog that then opens (and waits for you) is passed to
    answer(dialog), or closed. Returns the dialogs that opened."""
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        staticmethod(lambda *a, **k: ([str(j) for j in jars], "Jar files (*.jar)")))
    (browse,) = [b for b in dialog.findChildren(QToolButton) if b.text() == "…" and b.isVisibleTo(dialog)]
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
        browse.click()
    finally:
        on[0] = False
    settle()
    return seen


def offered(dialog) -> list[str]:
    """The choices a dialog lists (in a drop-down or a list)."""
    found = [items(c) for c in dialog.findChildren(QComboBox)]
    found += [[view.model().index(r, 0).data() for r in range(view.model().rowCount())]
              for view in dialog.findChildren(QListView) if view.isVisibleTo(dialog)]  # (not a drop-down's own list)
    return [text for texts in found for text in texts if text]


def pick(name, lists: list):
    """Answer a 'choose one' dialog: note what it lists, choose `name`, OK."""
    def answer(dialog):
        lists.append(offered(dialog))
        for combo in dialog.findChildren(QComboBox):
            if combo.findText(name) >= 0:
                combo.setCurrentIndex(combo.findText(name))
                break
        else:
            for view in dialog.findChildren(QListView):
                rows = [r for r in range(view.model().rowCount()) if view.model().index(r, 0).data() == name]
                if rows:
                    view.setCurrentIndex(view.model().index(rows[0], 0))
                    break
            else:
                return
        dialog.accept()
    return answer


def saved_driver(win) -> str:
    return yaml.safe_load((win.home.root / "connections.yaml").read_text(encoding="utf-8"))["warehouse"].get("driver")


def test_choosing_the_driver_jar_fills_in_the_driver_class_it_lists(win, tmp_path, monkeypatch):
    """A driver chosen with the jar it needs: the one that lists a driver class gives it."""
    needs = driver_jar(tmp_path / "jars" / "needs-1.0.jar", None)
    driver = driver_jar(tmp_path / "jars" / "example-jdbc-4.2.jar", "# JDBC 4 driver\norg.example.jdbc.Driver\n")
    dialog = new_jdbc_connection(win, monkeypatch)

    opened = choose_jars(dialog, monkeypatch, [needs, driver])

    assert opened == []  # one class: nothing to ask
    assert driver_class(dialog) == "org.example.jdbc.Driver"
    press(dialog, "Save")
    assert saved_driver(win) == "org.example.jdbc.Driver"


def test_a_real_jdbc_4_driver_jar_gives_its_driver_class(win, h2_server, monkeypatch):
    dialog = new_jdbc_connection(win, monkeypatch)

    assert choose_jars(dialog, monkeypatch, [h2_server["jar"]]) == []

    assert driver_class(dialog) == "org.h2.Driver"


def test_a_jar_listing_several_driver_classes_lets_you_choose_one(win, tmp_path, monkeypatch):
    jar = driver_jar(tmp_path / "example-jdbc.jar", "org.example.jdbc.Driver\norg.example.jdbc.LegacyDriver\n")
    dialog = new_jdbc_connection(win, monkeypatch)
    lists = []

    opened = choose_jars(dialog, monkeypatch, [jar], pick("org.example.jdbc.LegacyDriver", lists))

    assert len(opened) == 1 and lists, "no list to choose the driver class from"
    assert {"org.example.jdbc.Driver", "org.example.jdbc.LegacyDriver"} <= set(lists[0]), lists
    assert driver_class(dialog) == "org.example.jdbc.LegacyDriver"
    press(dialog, "Save")
    assert saved_driver(win) == "org.example.jdbc.LegacyDriver"


def test_a_jar_without_a_driver_class_leaves_the_field_to_you_and_says_so(win, tmp_path, monkeypatch):
    jar = driver_jar(tmp_path / "old-jdbc.jar", None)
    dialog = new_jdbc_connection(win, monkeypatch)
    before = shown_texts(dialog)

    choose_jars(dialog, monkeypatch, [jar])

    field = labelled(dialog, "Driver class")
    notes = (shown_texts(dialog) - before) | {field.toolTip(), field.placeholderText()}
    notes |= {text for _, (_, text) in win.message_boxes}
    win.message_boxes.clear()
    assert driver_class(dialog) == "" and field.isEnabled()
    assert any(re.search(r"driver", n, re.I) and re.search(r"\b(found|find)\b", n, re.I) for n in notes), notes
    field.setText("org.example.OldDriver")
    press(dialog, "Save")
    assert saved_driver(win) == "org.example.OldDriver"


def test_a_driver_class_you_typed_is_replaced_only_when_you_say_so(win, tmp_path, monkeypatch):
    jar = driver_jar(tmp_path / "example-jdbc.jar", "org.example.jdbc.Driver\n")
    dialog = new_jdbc_connection(win, monkeypatch)
    labelled(dialog, "Driver class").setText("com.example.MyDriver")
    asked = []

    def reply(yes):
        monkeypatch.setattr(QMessageBox, "question", staticmethod(
            lambda *a, **k: asked.append(a[1:3]) or (QMessageBox.Yes if yes else QMessageBox.No)))

        def answer(box):
            if isinstance(box, QMessageBox):
                asked.append(box.text())
                roles = (QMessageBox.YesRole, QMessageBox.AcceptRole) if yes else (QMessageBox.NoRole,
                                                                                   QMessageBox.RejectRole)
                next(b for b in box.buttons() if box.buttonRole(b) in roles).click()
        return answer

    choose_jars(dialog, monkeypatch, [jar], reply(False))

    assert asked, "not asked whether to replace the driver class you typed"
    assert driver_class(dialog) == "com.example.MyDriver"

    asked.clear()
    choose_jars(dialog, monkeypatch, [jar], reply(True))

    assert asked and driver_class(dialog) == "org.example.jdbc.Driver"
    press(dialog, "Save")
    assert saved_driver(win) == "org.example.jdbc.Driver"
