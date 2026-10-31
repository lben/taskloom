"""Proof: a JDBC connection asks for its driver jars first, fills in the Driver class however they are given, and
warns when it works only through another connection's jars. See README.md here."""

import html
import re
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QComboBox, QDialogButtonBox, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QToolButton)

from conftest import _h2_jar, _java_home
from proofs.test_proof_settings_wording import menu_action, row
from tests.test_jdbc_fields import (DRIVER_CLASS, JARS, JDBC_ROWS, click_test, driver_jar, field, focus, offered,
                                    paste_in, press, shown_labels, shown_rows, type_in)

# Taken as this file is imported, before the editor's test fixture answers question boxes itself: the question
# about replacing a Driver class you typed is then shown as in the app.
REAL_QUESTION = QMessageBox.question
ASKED = ("JDBC Connection: Driver .jar file(s): This field should be shown first so that it gets selected first by the "
         "user, then below the Driver class will get autofilled and then URL, User name and Password. — Decided with "
         "you: typing or pasting a jar's path fills in the Driver class too, and a connection that relies on another "
         "connection's jars gets a warning.")
H2_DRIVER = "org.h2.Driver"  # the class the real H2 jar names
OLD_DRIVER = "org.example.old.Driver"  # an older driver's class: its jar names none
TYPED = "com.example.Driver"  # a Driver class from your notes, not the one the jar names
CLASSES = ["org.example.jdbc.LegacyDriver", "org.example.jdbc.Driver"]  # what example-jdbc.jar names, in its order
CHOSEN = "org.example.jdbc.Driver"
WARNING = "org.h2.Driver is not in this connection's own jars"  # how the warning starts


def answering(action, *answers) -> int:
    """Do action(), as you would. Each window it opens and waits on is given to the next of `answers`, which looks at
    it and answers it; one left open, and any window beyond them, is closed, so nothing waits forever. How many
    windows opened."""
    already = QApplication.activeModalWidget()  # the Connections window
    seen, errors, busy = [], [], [False]

    def poll():
        opened = QApplication.activeModalWidget()
        if busy[0] or opened is None or opened is already or any(opened is s for s in seen):
            return
        busy[0] = True  # (an answer waits for what it does: this is not called again meanwhile)
        seen.append(opened)
        try:
            if len(seen) <= len(answers):
                answers[len(seen) - 1](opened)
        except BaseException as e:  # raised again below
            errors.append(e)
        finally:
            busy[0] = False
        if opened.isVisible():
            opened.reject()

    timer = QTimer(interval=50)
    timer.timeout.connect(poll)
    timer.start()
    try:
        action()
    finally:
        timer.stop()
    if errors:
        raise errors[0]
    return len(seen)


def activate(qtbot, dialog):
    """The Connections window is the one you type in (again, once a window over it has closed)."""
    dialog.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is dialog, timeout=5_000)


def new_connection(qtbot, dialog, name: str):
    """Press New… and type the name, then Enter: a new connection is a database (JDBC) one."""
    def name_it(ask):
        edit = ask.findChild(QLineEdit)
        QTest.keyClicks(edit, name)
        QTest.keyClick(edit, Qt.Key_Return)

    assert answering(lambda: press(dialog, "New…"), name_it) == 1, "New… asked no name"
    assert dialog.current == name and field(dialog, JARS).text() == "", f"{name} is not the connection shown"
    activate(qtbot, dialog)


def click_connection(qtbot, dialog, name: str):
    """Click the connection `name` in the list on the left."""
    names = dialog.findChild(QListWidget)
    (item,) = names.findItems(name, Qt.MatchExactly)
    QTest.mouseClick(names.viewport(), Qt.LeftButton, Qt.NoModifier, names.visualItemRect(item).center())
    qtbot.waitUntil(lambda: dialog.current == name, timeout=5_000)


def note(dialog, starts: str):
    """The note the window shows that starts with `starts` (None when it shows none)."""
    return next((label for label in shown_labels(dialog) if label.text().startswith(starts)), None)


def under(dialog, label: str):
    """What the window shows just under the row labelled `label` (a note, or the next row's field)."""
    rows = shown_rows(dialog)
    at = next(i for i, (name, _) in enumerate(rows) if name == label)
    return rows[at + 1][1] if at + 1 < len(rows) else None


def said(label) -> str:
    """A label's text as you read it: without its colour, each line break as one (also where Windows ends a line
    with two characters)."""
    text = html.unescape(re.sub(r"<[^>]+>", "", label.text().replace("<br>", "\n")))
    return "\n".join(line.strip() for line in text.strip().splitlines())


def jar_file(path, *entries):
    """A small .jar (a zip file) holding these files, each a few bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n\r\n")
        for entry in entries:
            jar.writestr(entry, b"\xca\xfe\xba\xbe")
    return path


# `env` before `win`: its Taskloom folder is the editor's (the same temporary one), and it is made first.
def test_a_jdbc_connection_asks_for_its_jars_first(proof, env, win, qtbot, tmp_path_factory, monkeypatch):
    p = proof("A JDBC connection asks for its driver jars first", ASKED)
    p.idea("A database (JDBC) connection in Tools > Connections now starts with its Driver .jar file(s). Below come "
           "the Driver class, then URL, User name and Password, and Tab goes through them in that order.")
    p.idea("The Driver class is filled in from the jars however you give them: chosen with the … button, typed or "
           "pasted. A typed or pasted path is read once you are done with the field: when you leave it (Tab, or a "
           "click in another field) or press Enter there. Enter there does only that: it does not save and close "
           "the window.")
    p.idea("The rules are the … button's: a jar naming several driver classes lets you choose one; a different class "
           "you typed is replaced only if you say yes; a jar naming none (an older driver) shows a note under the "
           "Driver class to type it. New: a path that does not exist is said just under the jar field.")
    p.idea("A connection whose own jars do not hold its Driver class, while another connection's jars do, shows a "
           "warning under its Driver class. It names that connection: this one works only because that one lists "
           "the jar, and removing it breaks this one. Save and test connection says the same once it connects.")
    p.why_simplest("The form already had all these rows; only their order changed. Its rows are made in one order "
                   "for every kind of connection, and each kind shows just its own. The jars now come first, and as "
                   "the other kinds have no jars, their rows stay as they were.")
    p.why_simplest("A typed or pasted path goes through the same steps as the … button: the same reading of the "
                   "jar, the same question before replacing a class you typed, the same list when there are several. "
                   "It is read once you are done with the field, and only if you changed it: going through it with "
                   "Tab asks nothing again.")
    p.why_simplest("Whether a jar holds a driver is read from the jar itself, as from a zip file, without starting "
                   "Java. A driver made for Java 6 or later names its class in a short list inside its jar, the list "
                   "Java reads too. For the warning, Taskloom looks in each connection's jars for the class's own "
                   "file. That takes a moment, and needs no Java while you edit.")
    p.why_simplest("Taskloom still gives Java the jars of every JDBC connection together, as one class path: that is "
                   "why a connection with no jars of its own can work at all. The warning makes that visible; it "
                   "changes nothing about how connections run.")
    p.alternative("Read the typed path at every key",
                  "A path half typed is no jar. The window would look for files at every key, and could ask about "
                  "your class, or offer a list, before you had finished typing.")
    p.alternative("Give each connection only its own jars",
                  "Java takes its class path once, when it starts, and a run starts it once for all its blocks: a "
                  "flow reading two databases needs both drivers in that one Java. Keeping each connection's jars "
                  "apart would need a separate loader inside Java for each one, a much larger change. The warning "
                  "tells you what a connection depends on, so you can give it its own jar.")
    p.alternative("Refuse to save or test a connection whose own jars lack its class",
                  "It does connect today, through the other connection's jar: refusing would break connections that "
                  "work. The warning leaves the choice to you.")
    p.alternative("Ask Java whether the jar holds the class",
                  "Java takes seconds to start, and would have to be found just to edit a connection. Reading the jar "
                  "as a zip file answers at once, the way the Driver class is filled in from it.")
    p.alternative("Hide the Driver class until a jar fills it in",
                  "An older driver names no class in its jar: you type it, from the driver's documentation. So the "
                  "field stays where you can type, and what is in it stays yours to change.")
    p.fits("Choosing the jar with … already filled in the Driver class (an earlier change you asked for). Typed and "
           "pasted jars now do the same, by the same rules.")
    p.fits("Run Query loads its driver the way Save and test connection does: by the Driver class, from the jars of "
           "every connection. The warning describes exactly that.")
    p.fits("Save and test connection runs taskloom connection test with the connection's name, as you would on the "
           "command line, so the command line prints the same warning.")
    p.fits("Server (SSH) and Email server (SMTP) connections keep their rows as they were.")

    java_home = _java_home()
    if not java_home:
        pytest.skip("Java is not installed: Save and test connection connects through Java")
    (win.home.root / "settings.yaml").write_text(f"java_home: '{Path(java_home).as_posix()}'\n", encoding="utf-8")
    drivers = tmp_path_factory.mktemp("drivers")
    h2 = drivers / "h2-2.3.232.jar"  # the H2 database's driver, as you downloaded it
    shutil.copyfile(_h2_jar(), h2)
    old = driver_jar(drivers / "old-jdbc.jar")  # an older driver: its class, but no list naming it
    licence = jar_file(drivers / "old-jdbc-license.jar", "org/example/old/License.class")
    typo = drivers / "old-jdbc-licence.jar"  # mistyped: there is no such file
    example = drivers / "example-jdbc.jar"
    driver_jar(example, *CLASSES)
    warned_then = {}  # the warning report_db showed as you made it

    def edit(dialog):
        activate(qtbot, dialog)

        # --- 1. the rows, jars first --------------------------------------------------------------------------
        p.step("Open Tools > Connections…, press New… and name the connection warehouse. A new connection is a "
               "database (JDBC) one.")
        new_connection(qtbot, dialog, "warehouse")
        jars, driver = field(dialog, JARS), field(dialog, DRIVER_CLASS)  # (the same fields for each connection)
        rows = [row(dialog, label) for label in JDBC_ROWS]
        p.shot(dialog, "① warehouse's rows, top to bottom: Driver .jar file(s) first, then Driver class, URL, User "
                       "name and Password. A note shows under the Driver class when a jar names no class (step 4).",
               highlight=[[label for label, _ in rows]], badges=True, zoom=[rows[0][0], rows[-1][1]])
        p.check("The rows are Kind, then Driver .jar file(s), Driver class, URL, User name and Password, in that "
                "order", [name for name, _ in shown_rows(dialog) if name] == ["Kind", *JDBC_ROWS])

        stops = []
        fields = [(label, field(dialog, label)) for label in JDBC_ROWS]
        focus(qtbot, row(dialog, "Kind")[1])
        for _ in range(12):  # Tab, from Kind, down to Password
            QTest.keyClick(QApplication.focusWidget(), Qt.Key_Tab)
            now = QApplication.focusWidget()
            stops.append(next((label for label, f in fields if f is now),
                              f"its {now.text()} button" if isinstance(now, QToolButton) else "?"))
            if stops[-1] == "Password":
                break
        p.text("Pressing Tab from Kind, the field it goes to each time:", " → ".join(stops))
        p.check("Tab follows the same order, from Kind: Driver .jar file(s), Driver class, URL, User name, Password",
                [s for s in stops if not s.startswith("its ")] == JDBC_ROWS)

        # --- 2. a pasted path fills in the Driver class -------------------------------------------------------
        p.step("Paste the path of the H2 database's driver, h2-2.3.232.jar, into Driver .jar file(s).")
        p.note("H2 is a small database written in Java; its jar is the real driver. The other jars in this proof "
               "are small ones it makes, laid out as a driver's jar is (the list naming its classes, and their "
               "files), to show each case; they hold no working driver.")
        paste_in(qtbot, jars, str(h2))
        p.shot(dialog, "Before: ① the path is pasted, and you are still in the field. ② The Driver class is still "
                       "empty: the path is read once you are done with the field.",
               highlight=[list(row(dialog, JARS)), list(row(dialog, DRIVER_CLASS))], badges=True,
               zoom=[row(dialog, JARS)[0], row(dialog, "URL")[1]])
        p.check("The jar field holds the pasted path, and the Driver class is still empty",
                jars.text() == str(h2) and driver.text() == "")

        p.step("Press Tab to leave the field, then type the URL, jdbc:h2:mem:warehouse, and the User name, sa.")
        asked = answering(lambda: QTest.keyClick(jars, Qt.Key_Tab))
        filled = driver.text()
        type_in(qtbot, field(dialog, "URL"), "jdbc:h2:mem:warehouse")
        type_in(qtbot, field(dialog, "User name"), "sa")
        p.shot(dialog, "After: ② the Driver class is filled in by itself, org.h2.Driver, the class ① the H2 jar "
                       "names. Nothing was asked: the jar names one class.",
               highlight=[list(row(dialog, JARS)), list(row(dialog, DRIVER_CLASS))], badges=True,
               zoom=[row(dialog, JARS)[0], row(dialog, "User name")[1]])
        p.check("Leaving the field filled in the Driver class org.h2.Driver, from the jar",
                filled == driver.text() == H2_DRIVER)
        p.check("Nothing was asked", asked == 0 and win.message_boxes == [])

        # --- 3. a path that does not exist; a jar naming no class ---------------------------------------------
        p.step("Add a connection legacy_db, for an older driver that comes as two jars. Type both paths, separated "
               "by ;, with a typo in the second (licence for license), and press Tab.")
        new_connection(qtbot, dialog, "legacy_db")
        type_in(qtbot, jars, f"{old}; {typo}")
        asked = answering(lambda: QTest.keyClick(jars, Qt.Key_Tab))
        not_found, no_class = note(dialog, "Not found"), note(dialog, "No driver class found")
        p.shot(dialog, "① Just under the jar field: “Not found (check the path)”, with the mistyped path. ② Just "
                       "under the Driver class, the note for an older driver: its jar names no class, so type it in "
                       "Driver class. The Driver class stays empty.",
               highlight=[x for x in (not_found, no_class) if x is not None], badges=True,
               zoom=[row(dialog, JARS)[0], row(dialog, "URL")[1]])
        p.check("① Just under the jar field, the window says the mistyped jar is not found, and names only it",
                not_found is not None and str(typo) in not_found.text() and str(old) not in not_found.text()
                and under(dialog, JARS) is not_found)
        p.check("② Just under the Driver class, a note says the jar names no driver class: type it in Driver class",
                no_class is not None and "type it in Driver class" in no_class.text()
                and under(dialog, DRIVER_CLASS) is no_class)
        p.check("Nothing was asked, and the Driver class stays empty",
                asked == 0 and win.message_boxes == [] and driver.text() == "")

        p.step("Put the typo right and press Tab, then type the Driver class the driver's documentation names, "
               "org.example.old.Driver.")
        type_in(qtbot, jars, f"{old}; {licence}")
        asked = answering(lambda: QTest.keyClick(jars, Qt.Key_Tab))
        type_in(qtbot, driver, OLD_DRIVER)
        QTest.keyClick(driver, Qt.Key_Tab)
        p.shot(dialog, "① Both jars are found: the “Not found” line is gone. ② The Driver class is the one you "
                       "typed; the note under it stays, as the jars still name no class.",
               highlight=[list(row(dialog, JARS)), list(row(dialog, DRIVER_CLASS))], badges=True,
               zoom=[row(dialog, JARS)[0], row(dialog, "URL")[1]])
        p.check("Put right, the “Not found” line is gone", note(dialog, "Not found") is None)
        p.check("The Driver class is the one typed, org.example.old.Driver, and nothing was asked",
                driver.text() == OLD_DRIVER and asked == 0 and win.message_boxes == [])

        # --- 4. a class you typed is replaced only if you say so; several classes, a list -----------------------
        p.step("Add a connection sales_db. Type the Driver class your notes give, com.example.Driver, then type "
               "the path of its jar, example-jdbc.jar, and press Enter.")
        new_connection(qtbot, dialog, "sales_db")
        type_in(qtbot, driver, TYPED)
        type_in(qtbot, jars, str(example))
        questions = []

        def say_no(box):
            assert isinstance(box, QMessageBox), f"not a question: {box.windowTitle()}"
            questions.append(box.text())
            text = next(label for label in box.findChildren(QLabel) if label.text() == box.text())
            p.shot(box, "The window asks first: the jar names other classes than the one you typed. Use the one "
                        "the jar names? Click No.", over=dialog, highlight=[text, box.button(QMessageBox.No)],
                   badges=True)
            QTest.mouseClick(box.button(QMessageBox.No), Qt.LeftButton)

        with monkeypatch.context() as real:
            real.setattr(QMessageBox, "question", REAL_QUESTION)
            asked = answering(lambda: QTest.keyClick(jars, Qt.Key_Return), say_no)
        p.check("Asked once, naming the jar's two classes and the one you typed", asked == 1 and questions
                and all(name in questions[0] for name in [*CLASSES, TYPED]))
        p.check("Enter did not save and close the window: it is still open, and nothing is saved yet",
                dialog.isVisible() and not (win.home.root / "connections.yaml").exists())
        activate(qtbot, dialog)
        p.shot(dialog, "Answered No: ① the Driver class is still the one you typed, com.example.Driver. The window "
                       "is still open: Enter did not save and close it.",
               highlight=[list(row(dialog, DRIVER_CLASS))], badges=True,
               zoom=[row(dialog, JARS)[0], row(dialog, "URL")[1]])
        p.check("The class you typed is kept", driver.text() == TYPED)

        p.step("Paste the same path over it and press Tab, and this time answer Yes. The jar names two driver "
               "classes: choose org.example.jdbc.Driver.")
        p.note("A typed path is read again only when you change it; pasting it again counts as a change.")
        paste_in(qtbot, jars, str(example))
        lists = []

        def say_yes(box):
            assert isinstance(box, QMessageBox), f"not a question: {box.windowTitle()}"
            QTest.mouseClick(box.button(QMessageBox.Yes), Qt.LeftButton)

        def choose_class(ask):
            combo = ask.findChild(QComboBox)
            assert combo is not None, f"no list to choose from: {ask.windowTitle()}"
            lists.append(offered(ask))
            combo.showPopup()
            view = combo.view()
            qtbot.waitUntil(view.isVisible, timeout=5_000)
            p.shot(view.window(), "The jar names two driver classes: you choose the one to use, ① "
                                  "org.example.jdbc.Driver.", over=ask,
                   highlight=[(view, combo.findText(CHOSEN))], badges=True, zoom=ask.rect())
            index = view.model().index(combo.findText(CHOSEN), 0)
            QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, view.visualRect(index).center())
            qtbot.waitUntil(lambda: not view.isVisible(), timeout=5_000)
            QTest.mouseClick(ask.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok), Qt.LeftButton)

        with monkeypatch.context() as real:
            real.setattr(QMessageBox, "question", REAL_QUESTION)
            asked = answering(lambda: QTest.keyClick(jars, Qt.Key_Tab), say_yes, choose_class)
        activate(qtbot, dialog)
        p.check("Answered Yes, a list offers the jar's two driver classes", asked == 2 and lists == [CLASSES])
        p.shot(dialog, "① The Driver class is the one chosen, org.example.jdbc.Driver.",
               highlight=[list(row(dialog, DRIVER_CLASS))], badges=True,
               zoom=[row(dialog, JARS)[0], row(dialog, "URL")[1]])
        p.check("The Driver class is the one chosen from the list", driver.text() == CHOSEN)

        # --- 5. a warning when it works only through another connection's jars ------------------------------------
        p.step("Add a connection report_db with no jars of its own. Type its Driver class, org.h2.Driver, and press "
               "Tab, then its URL, jdbc:h2:mem:report, and User name, sa.")
        new_connection(qtbot, dialog, "report_db")
        type_in(qtbot, driver, H2_DRIVER)
        asked = answering(lambda: QTest.keyClick(driver, Qt.Key_Tab))
        type_in(qtbot, field(dialog, "URL"), "jdbc:h2:mem:report")
        type_in(qtbot, field(dialog, "User name"), "sa")
        warning = note(dialog, WARNING)
        p.shot(dialog, "① report_db has no jars of its own, and ② its Driver class is org.h2.Driver, the class in "
                       "warehouse's jar. ③ A warning under it says so: it works only because warehouse lists a jar "
                       "holding that class (Java gets the jars of every connection), so removing warehouse breaks "
                       "this connection.",
               highlight=[list(row(dialog, JARS)), list(row(dialog, DRIVER_CLASS)),
                          *([warning] if warning is not None else [])], badges=True,
               zoom=[row(dialog, JARS)[0], warning if warning is not None else row(dialog, "URL")[1]])
        warned = warned_then["text"] = warning.text() if warning is not None else ""
        p.check("A warning, just under the Driver class, says report_db works only because warehouse lists a jar "
                "holding org.h2.Driver, and that removing warehouse breaks it",
                warning is not None and under(dialog, DRIVER_CLASS) is warning
                and "works only because warehouse lists a jar holding it" in warned
                and "removing warehouse breaks this connection" in warned)
        p.check("No other connection is named in it, and nothing was asked",
                "legacy_db" not in warned and "sales_db" not in warned and asked == 0 and win.message_boxes == [])

        p.step("Click warehouse in the list on the left and press Save and test connection.")
        p.note("Save and test connection starts Java. The proof's Taskloom folder names this computer's Java folder "
               "(Tools > Settings, Java folder), as you would once. jdbc:h2:mem: makes an H2 database in memory for "
               "the test: no database server is needed.")
        click_connection(qtbot, dialog, "warehouse")
        click_test(dialog, qtbot)
        p.shot(dialog, "warehouse's own jar holds its class: ① no warning under its Driver class, and ② the test "
                       "says just that it connected, to H2.",
               highlight=[list(row(dialog, DRIVER_CLASS)), dialog.test_result], badges=True,
               zoom=[row(dialog, JARS)[0], dialog.test_result])
        p.check("No warning for warehouse, the connection whose jar holds the class",
                note(dialog, WARNING) is None and not [label for label in shown_labels(dialog)
                                                       if "works only because" in label.text()])
        p.check("Its test connects to H2, and says nothing more",
                said(dialog.test_result).startswith("✓ Connected to H2") and "\n" not in said(dialog.test_result))

        p.step("Click report_db and press Save and test connection.")
        click_connection(qtbot, dialog, "report_db")
        click_test(dialog, qtbot)
        result = said(dialog.test_result)
        p.shot(dialog, "report_db connects, through warehouse's jar. ① Its test says so: it connected to H2, and on "
                       "the next line the same warning, naming warehouse.",
               highlight=[dialog.test_result], badges=True,
               zoom=[dialog.test_button, dialog.test_result])
        p.check("The test connects to H2 and then gives the same warning as the window, naming warehouse",
                result.startswith("✓ Connected to H2") and result.split("\n", 1)[-1] == warned)
        cli = env.taskloom("connection", "test", "report_db")
        p.text("The same test on the command line, taskloom connection test report_db, prints:", cli.stdout.strip())
        p.check("The command line prints the same", cli.returncode == 0 and cli.stdout.strip() == result[2:])

        p.step("Press Save, then open Tools > Connections… again and click report_db.")
        press(dialog, "Save")

    p.while_open(lambda: menu_action(win, "&Tools", "Connections…").trigger(), edit)
    saved = yaml.safe_load((win.home.root / "connections.yaml").read_text(encoding="utf-8"))
    p.check("Saved: warehouse with the H2 jar and its class, report_db with no jars and org.h2.Driver",
            saved["warehouse"]["jars"] == [str(h2)] and saved["warehouse"]["driver"] == H2_DRIVER
            and saved["report_db"]["jars"] == [] and saved["report_db"]["driver"] == H2_DRIVER)
    p.check("Saved: legacy_db with both jars and the class typed, sales_db with the class chosen",
            saved["legacy_db"]["jars"] == [str(old), str(licence)] and saved["legacy_db"]["driver"] == OLD_DRIVER
            and saved["sales_db"]["jars"] == [str(example)] and saved["sales_db"]["driver"] == CHOSEN)

    # --- 6. the other kinds keep their rows -----------------------------------------------------------------------
    ssh_rows, smtp_rows = [], []

    def other_kinds(dialog):
        activate(qtbot, dialog)
        names = dialog.findChild(QListWidget)
        listed = [names.item(i).text() for i in range(names.count())]
        click_connection(qtbot, dialog, "report_db")
        warning = note(dialog, WARNING)
        p.shot(dialog, "① The four connections are saved. ② report_db is as you left it, with no jars and the "
                       "Driver class org.h2.Driver, and the warning naming warehouse is there again.",
               highlight=[names, [*row(dialog, JARS), *([warning] if warning is not None else [])]], badges=True)
        p.check("Opened again, the window lists the four connections saved",
                listed == ["warehouse", "legacy_db", "sales_db", "report_db"])
        p.check("report_db shows what was saved, and the same warning",
                [field(dialog, label).text() for label in (JARS, DRIVER_CLASS, "URL")]
                == ["", H2_DRIVER, "jdbc:h2:mem:report"] and warning is not None
                and warning.text() == warned_then["text"])

        p.step("Press New…, name the connection appserver, and choose Server (SSH) in Kind.")
        new_connection(qtbot, dialog, "appserver")
        kind = row(dialog, "Kind")[1]
        kind.setCurrentIndex(kind.findData("ssh"))
        ssh_rows.extend(name for name, _ in shown_rows(dialog) if name)
        rows = [row(dialog, label) for label in ssh_rows[1:]]
        p.shot(dialog, "① A Server (SSH) connection has its rows as before: Host, Port, User name, Key file and "
                       "Password. No jars, no Driver class.",
               highlight=[[label for label, _ in rows]], badges=True, zoom=[row(dialog, "Kind")[0], rows[-1][1]])
        kind.setCurrentIndex(kind.findData("smtp"))
        smtp_rows.extend(name for name, _ in shown_rows(dialog) if name)
        press(dialog, "Cancel")

    p.while_open(lambda: menu_action(win, "&Tools", "Connections…").trigger(), other_kinds)
    p.check("Server (SSH) shows Kind, Host, Port, User name, Key file and Password, as before",
            ssh_rows == ["Kind", "Host", "Port", "User name", "Key file", "Password"])
    p.check("Email server (SMTP) shows Kind, Host, Port, Security, User name, Send as and Password, as before",
            smtp_rows == ["Kind", "Host", "Port", "Security", "User name", "Send as", "Password"])
