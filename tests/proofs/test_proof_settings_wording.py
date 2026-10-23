"""Proof: messages name settings as Properties does, file paths say where they are, the jar names its driver."""

import shutil
import zipfile

import yaml
from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFormLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QToolBar, QToolButton, QWidget)

from conftest import _h2_jar
from proofs.conftest import ZOOM_MARGIN
from tests.test_editor import drag_port_to_port

# Taken as this file is imported, before the win fixture answers message boxes itself: Validate's box is then
# shown as in the app.
REAL_WARNING = QMessageBox.warning
CONNECTIONS = """\
appserver: {kind: ssh, host: server01.example.com, username: me}
backup: {kind: ssh, host: server02.example.com, username: me}
mailer: {kind: smtp, host: mail.example.com, port: 25, sender: reports@example.com}
"""
SERVERS = ["This computer", "appserver", "backup"]  # where a file block's path can be (mailer holds no files)


def menu_action(win, menu: str, item: str):
    menu = next(a.menu() for a in win.menuBar().actions() if a.text() == menu)
    return next(a for a in menu.actions() if a.text() == item)


def add_block(win, title: str, block: str, beside: str, dx: float, dy: float):
    """Double-click `title` in the BLOCKS list, then drag the new block to dx, dy from the block `beside`."""
    tree, tab = win.palette_panel.tree, win.current
    item = tree.findItems(title, Qt.MatchExactly | Qt.MatchRecursive)[0]
    tree.scrollToItem(item)
    at = tree.visualItemRect(item).center()
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # a double click: a click, then the
    QTest.mouseDClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, at)  # second one (QTest sends only that)
    assert block in tab.scene.nodes, f"{title} was not added as {block}"
    node, there = tab.scene.nodes[block].sceneBoundingRect(), tab.scene.nodes[beside].sceneBoundingRect()
    grip = QPointF(node.center().x(), node.top() + 10)  # its title
    a = tab.view.mapFromScene(grip)
    b = tab.view.mapFromScene(grip + there.topLeft() - node.topLeft() + QPointF(dx, dy))
    QTest.mousePress(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(tab.view.viewport(), (a + b) / 2)
    QTest.mouseMove(tab.view.viewport(), b)
    QTest.mouseRelease(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, b)


def click_block(win, block: str):
    tab = win.current
    at = tab.view.mapFromScene(tab.scene.nodes[block].sceneBoundingRect().center())
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QApplication.processEvents()
    assert win.properties.block_id == block, f"{block} is not the block Properties shows"


def row(root: QWidget, label: str) -> tuple[QLabel, QWidget]:
    """The label and the field of the form row in `root` labelled `label`."""
    for form in root.findChildren(QFormLayout):
        for r in range(form.rowCount()):
            name, field = form.itemAt(r, QFormLayout.LabelRole), form.itemAt(r, QFormLayout.FieldRole)
            if name and field and isinstance(name.widget(), QLabel) and name.widget().text() == label:
                return name.widget(), field.widget()
    raise AssertionError(f"no row {label!r}")


def setting(win, label: str) -> tuple[QLabel, QWidget]:
    """The selected block's setting labelled `label`, in Properties as it is now (it is built again as the block
    changes)."""
    return row(win.properties.widget(), label)


def where(win, label: str) -> QComboBox:
    """The list of where a path is, in the setting labelled `label` (e.g. Source on)."""
    return setting(win, label)[1].findChild(QComboBox)


def choices(combo: QComboBox) -> list[str]:
    return [combo.itemText(i) for i in range(combo.count())]


def properties_down_to(win, last: QWidget) -> QRect:
    """For a close-up of Properties from its tab down to `last`: a close-up adds a margin around what it shows,
    and this one starts at the tab, so it shows nothing of the toolbar above."""
    QTest.qWait(100)  # Properties laid out (as p.shot waits before it takes the screenshot)
    tabs, bottom = win.right_tabs.mapTo(win, QPoint(0, 0)), last.mapTo(win, QPoint(0, last.height())).y()
    return QRect(tabs.x(), tabs.y(), win.right_tabs.width(), bottom - tabs.y()).adjusted(
        ZOOM_MARGIN, ZOOM_MARGIN, -ZOOM_MARGIN, 0)


def problem(win) -> str:
    """The message under the block's name in Properties ('' when it shows none)."""
    error = win.properties.error
    return error.text() if error.isVisibleTo(win.properties) else ""


def type_in(edit: QLineEdit, text: str):
    """Type `text` in a field (replacing what it held) and press Enter."""
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, text)
    QTest.keyClick(edit, Qt.Key_Return)
    QApplication.processEvents()


def click_noting_questions(button) -> list[str]:
    """Click `button`; a window that then opens and waits for an answer is closed (so nothing waits forever) and
    its title returned."""
    already, asked = QApplication.activeModalWidget(), []

    def close_new():
        opened = QApplication.activeModalWidget()
        if opened is not None and opened is not already:
            asked.append(opened.windowTitle() or type(opened).__name__)
            opened.reject()

    timer = QTimer(interval=50)
    timer.timeout.connect(close_new)
    timer.start()
    try:
        QTest.mouseClick(button, Qt.LeftButton)
    finally:
        timer.stop()
    return asked


def test_settings_are_named_as_on_screen(proof, win, qtbot, tmp_path, tmp_path_factory, monkeypatch):
    p = proof("Plain setting names, where each file is, the jar's driver class",
              "Copy|Delete|Move files blocks are not clear to me. They say 'config.source is required' <-- what? "
              "why? makes no sense, not to mention I don't want to deal with config files ... Also I don't know if "
              "these are local, remote, what? · Make the JDBC Connection automatically traverse the jar looking for "
              "the Driver Class and fills that field automatically (similar to how dbvisualizer does it)")
    p.idea("Messages name each setting as Properties shows it: “Source is required”. Each path of Copy, Move and "
           "Delete Files says where it is: this computer or one of your servers. And choosing a JDBC driver's jar "
           "fills in its driver class.")
    p.idea("The same names are used under the block's name in Properties, by Validate, by a run and on the "
           "command line. No message says “config.”")
    p.idea("Source on, Destination on and Path on start at This computer (its folders and network folders) and "
           "list your server connections. The path is then just the path.")
    p.idea("If the jar names several driver classes, you choose one; if none (an old driver), a note says to type "
           "it. A class you typed is replaced only if you say yes.")
    p.why_simplest("Properties and the checks every block shares (missing, a wrong value, an unknown connection or "
                   "parameter) take a setting's name from the same place, so they cannot disagree, now or for blocks "
                   "added later.")
    p.why_simplest("Where a path is, is one more setting beside it: the same kind of list other blocks use to pick "
                   "their database or server, with This computer first. You pick; nothing is written in a special "
                   "form.")
    p.why_simplest("A JDBC driver made for Java 6 or later names its driver class in a short list inside its jar: "
                   "Java itself finds drivers that way. Taskloom reads that list as from a zip file, so no Java has "
                   "to start and it takes a moment.")
    p.alternative("Keep writing the server in the path (appserver:~/outbox/report.csv)",
                  "The old way. Only the hover help on Source and Path, and the SSH help in Connections, said how to "
                  "write it. The field looked like any path on this computer, so you could not tell where it was. A "
                  "list shows the choice.")
    p.alternative("Separate blocks for files here and files on a server",
                  "Server to here, here to server, server to server: a block for each. One choice per path covers "
                  "them all with the same three blocks.")
    p.alternative("Look through every class in the jar for one that is a driver",
                  "That means opening each of the driver's classes, thousands in a big jar, and working out which "
                  "are drivers, with Java or by taking its class files apart. Some jars have more than one (older "
                  "names kept), so you would still pick. The list in the jar is the driver's own answer.")
    p.alternative("Rewrite each message by hand",
                  "Most come from the few checks every block shares: one rule names the setting in all of them, and "
                  "in blocks added later. Written one by one, they could drift from the panel.")
    p.fits("The list of where a path is, is the connection list other blocks have, with Edit… and New… (New… "
           "opens Tools > Connections). It is checked like any connection: if that server's connection is gone, "
           "Validate says so, naming Source on.")
    p.fits("The command line (taskloom validate, run, check) prints the same messages. A flow with a path written "
           "the old way is not run as a folder on this computer: it stops, saying to choose the server in Source "
           "on.")
    p.fits("The Driver class is the field Run Query has always used to load the driver: now filled in for you, "
           "and still yours to change.")
    jar = tmp_path_factory.mktemp("drivers") / "h2-2.3.232.jar"  # the H2 database's driver, as you downloaded it
    shutil.copyfile(_h2_jar(), jar)
    (win.home.root / "connections.yaml").write_text(CONNECTIONS, encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "copy-files.yaml"), ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(jar)], "Jar files (*.jar)"))
    validate = win.findChild(QToolBar, "run-toolbar").widgetForAction(win.validate_action)

    # --- the message names the setting as Properties shows it ---------------------------------------------------
    p.step("Start a new flow and add a Copy Files block. File > New flow, then double-click Copy Files in the "
           "BLOCKS list on the left, drag the block beside the Manual Start, and click it. Nothing is filled in yet.")
    menu_action(win, "&File", "New flow").trigger()
    tab = win.current
    add_block(win, "Copy Files", "copy", beside="start", dx=280, dy=0)
    click_block(win, "copy")
    source = setting(win, "Source")
    p.shot(win, "① The new Copy Files block. ② Under its name, Properties says what is missing in its own words: "
                "Source is required. ③ Source is the field it means, just below.",
           highlight=["copy", win.properties.error, [*source]], badges=True,
           zoom=properties_down_to(win, source[1]))
    p.check("Copy Files, just added, says “Source is required”, and Source is one of its fields",
            tab.doc.blocks["copy"]["type"] == "files.copy" and problem(win) == "Source is required"
            and source[0].text() == "Source")

    p.step("Connect the Manual Start to Copy Files and press Validate. It is in the toolbar.")
    p.note("Validate saves a new flow first, so it asks where: the proof answers the Save window with "
           "copy-files.yaml, in a folder of its own.")
    drag_port_to_port(tab, "start", "start", "copy", "after")
    assert "start.start -> copy.after" in tab.doc.data["edges"], "not connected"
    said = []

    def look_at_problems(box):
        said.append(box.text())
        text = next(label for label in box.findChildren(QLabel) if label.text() == box.text())
        p.shot(box, "Validate says the same, naming the block and the setting: copy: Source is required.",
               highlight=[text])
        box.accept()

    with monkeypatch.context() as real:
        real.setattr(QMessageBox, "warning", REAL_WARNING)
        p.while_open(lambda: QTest.mouseClick(validate, Qt.LeftButton), look_at_problems)
    p.check("Validate says “copy: Source is required”", said == ["copy: Source is required"])
    log = win.log.text.toPlainText()
    p.shot(win, "① The Log, under the canvas, keeps what Validate said, in the same words.",
           highlight=[(win.log.text, "Source is required")], badges=True,
           zoom=[win.log.showing, (win.log.text, "Source is required")])
    p.check("The Log says the same: copy: Source is required", "copy: Source is required" in log)
    p.check("No message says “config.”", "config." not in log + "".join(said) + problem(win))

    # --- where each path is -----------------------------------------------------------------------------------
    p.step("Open the Source on list, just above Source. Tools > Connections has two servers, appserver and backup, "
           "and a mail server, mailer.")
    source_on = where(win, "Source on")
    p.check("Source on and Destination on both start at This computer",
            source_on.currentText() == where(win, "Destination on").currentText() == "This computer")
    source_on.showPopup()
    popup = source_on.view()
    qtbot.waitUntil(popup.isVisible, timeout=5_000)
    p.shot(popup.window(), "The list offers ① This computer (its folders, and network folders) and ② your two "
                           "servers. The mail server is not offered: files are not kept there.", over=win,
           highlight=[(popup, 0), [(popup, 1), (popup, 2)]], badges=True,
           zoom=properties_down_to(win, setting(win, "Destination")[1]))
    p.check("The Source on list offers This computer, appserver and backup, and not the mail server",
            choices(source_on) == SERVERS)

    p.step("Choose appserver and type the two paths. The Source is ~/outbox/report.csv, and the Destination "
           "C:/inbox/, on This computer.")
    index = popup.model().index(source_on.findText("appserver"), 0)
    QTest.mouseClick(popup.viewport(), Qt.LeftButton, Qt.NoModifier, popup.visualRect(index).center())
    qtbot.waitUntil(lambda: not popup.isVisible(), timeout=5_000)
    type_in(setting(win, "Source")[1], "~/outbox/report.csv")
    type_in(setting(win, "Destination")[1], "C:/inbox/")
    source_on, source, destination_on, destination = (
        setting(win, label) for label in ("Source on", "Source", "Destination on", "Destination"))
    p.shot(win, "① Source on says appserver: the report is on that server, at ~/outbox/report.csv. ② The "
                "Destination is on this computer. Nothing is missing now, so Properties shows no message.",
           highlight=[[*source_on, *source], [*destination_on, *destination]], badges=True,
           zoom=properties_down_to(win, destination[1]))
    p.check("Properties shows the source ~/outbox/report.csv on appserver, the destination C:/inbox/ on This "
            "computer", [where(win, "Source on").currentText(), source[1].text(), where(win, "Destination on")
                         .currentText(), destination[1].text()] == ["appserver", "~/outbox/report.csv",
                                                                    "This computer", "C:/inbox/"])
    p.check("The block keeps them as shown, and Properties shows no message any more", problem(win) == ""
            and tab.doc.blocks["copy"]["config"] == {"source_server": "appserver", "source": "~/outbox/report.csv",
                                                     "destination": "C:/inbox/"})
    tips = {label: setting(win, label)[0].toolTip() for label in ("Source on", "Source")}
    p.text("Hovering over Source on and Source says what goes there:",
           "\n\n".join(f"{label}: {tip}" for label, tip in tips.items()))
    p.check("Hovering over Source on and Source says what goes there", "This computer" in tips["Source on"]
            and "server" in tips["Source on"] and "~/outbox/sales.csv" in tips["Source"])

    p.step("Add Move Files and Delete Files the same way, and click each.")
    for title, block, paths, missing in (
            ("Move Files", "move", [("Source on", "Source"), ("Destination on", "Destination")], "Source"),
            ("Delete Files", "delete", [("Path on", "Path")], "Path")):
        add_block(win, title, block, beside="start" if block == "move" else "copy", dx=0, dy=170)
        click_block(win, block)
        rows = [[*setting(win, on), *setting(win, path)] for on, path in paths]
        named = " and ".join(f"{n} {on}" for n, (on, _) in zip("①②", paths))
        error = "③" if len(paths) > 1 else "②"  # the message's badge
        p.shot(win, f"{title}: {named} {'say where each path is' if len(paths) > 1 else 'says where the path is'}: "
                    f"This computer, to begin with. {error} It says what is missing the same way: {missing} is "
                    "required.",
               highlight=[*rows, win.properties.error], badges=True, zoom=properties_down_to(win, rows[-1][-1]))
        p.check(f"{title}: {' and '.join(on for on, _ in paths)} {'each ' if len(paths) > 1 else ''}offer"
                f"{'' if len(paths) > 1 else 's'} This computer, appserver and backup, and start"
                f"{'' if len(paths) > 1 else 's'} at This computer",
                all(choices(where(win, on)) == SERVERS and where(win, on).currentText() == "This computer"
                    for on, _ in paths))
        p.check(f"{title} says “{missing} is required”", problem(win) == f"{missing} is required")

    # --- the driver class, from the jar ---------------------------------------------------------------------
    p.step("Add a connection named warehouse. In Tools > Connections…, press New… and type its name: a new "
           "connection is a database (JDBC) one.")

    def fill_in(dialog):
        p.while_open(lambda: next(b for b in dialog.findChildren(QPushButton) if b.text() == "New…").click(),
                     lambda ask: type_in(ask.findChild(QLineEdit), "warehouse"))
        kind, driver, jars = (row(dialog, label) for label in ("Kind", "Driver class", "Driver .jar file(s)"))
        browse = jars[1].findChild(QToolButton)
        p.shot(dialog, "warehouse is a database (JDBC) connection. ① Its Driver class is empty. ② The … button "
                       "chooses the driver's jar. The help above says choosing the jar fills in the driver class.",
               highlight=[[*driver], browse], badges=True)
        p.check("warehouse is a database (JDBC) connection, with no driver class yet",
                kind[1].currentText() == "Database (JDBC)" and driver[1].text() == "")

        p.step("Choose the driver's jar. Click … next to Driver .jar file(s), choose the H2 database's driver, "
               "h2-2.3.232.jar, and press Save.")
        p.note("The window to choose a file is your system's own: the proof gives it the jar, as you would by "
               "clicking through your folders.")
        asked = click_noting_questions(browse)
        p.shot(dialog, "① The jar is chosen, and ② the Driver class is filled in by itself: org.h2.Driver.",
               highlight=[[*jars], [*driver]], badges=True)
        with zipfile.ZipFile(jar) as archive:
            listed = archive.read("META-INF/services/java.sql.Driver").decode("utf-8").strip()
        p.text("Inside the jar, the list Java reads to find its driver (META-INF/services/java.sql.Driver) says:",
               listed)
        p.check("The Driver class is filled in with org.h2.Driver, the class the jar names",
                jars[1].findChild(QLineEdit).text() == str(jar) and driver[1].text() == listed == "org.h2.Driver")
        p.check("Nothing was asked: the jar names one driver class", asked == [])
        next(b for b in dialog.findChildren(QPushButton) if b.text().replace("&", "") == "Save").click()

    p.while_open(lambda: menu_action(win, "&Tools", "Connections…").trigger(), fill_in)
    saved = yaml.safe_load((win.home.root / "connections.yaml").read_text(encoding="utf-8"))["warehouse"]
    p.check("Saved: warehouse has the driver class org.h2.Driver and the jar",
            saved["driver"] == "org.h2.Driver" and saved["jars"] == [str(jar)])
    tab.doc.dirty = False  # Move and Delete Files were added only to be shown: closing the window must not ask
