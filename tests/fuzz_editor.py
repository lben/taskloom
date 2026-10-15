"""Random real mouse/keyboard use of the editor, in its own process (a hard crash = non-zero exit).

    python tests/fuzz_editor.py SEED STEPS TASKLOOM_FOLDER
Prints "ok" at the end if the editor survived and the saved flow is valid.
"""

import os
import random
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QInputDialog, QLineEdit,  # noqa: E402
                               QMessageBox, QToolButton)

from taskloom.config import Home  # noqa: E402
from taskloom.editor.canvas import TEXT_ITEM  # noqa: E402
from taskloom.editor.dialogs import ConnectionsDialog  # noqa: E402
from taskloom.editor.window import MainWindow  # noqa: E402
from taskloom.editor.document import FlowDocument  # noqa: E402
from taskloom.flow import FlowError, load_flow  # noqa: E402
from taskloom.registry import discover  # noqa: E402

seed, steps, folder = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
rnd = random.Random(seed)
app = QApplication([])
for name in ("information", "warning", "question"):  # dialogs would wait for a click
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Discard))
QInputDialog.getText = staticmethod(lambda *a, **k: (f"conn{rnd.randint(0, 10 ** 6)}", True))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(folder / "fuzz.yaml"), ""))
ConnectionsDialog.exec = lambda self: (self._save(), 1)[1]

window = MainWindow(Home(folder / "home"))
window.resize(1400, 900)
window.show()
tab = window.new_flow()
view = tab.view
types = sorted(discover(None).blocks)


def at(item, local):
    return view.mapFromScene(item.mapToScene(local))


for step in range(steps):
    nodes = list(tab.scene.nodes.values())
    action = rnd.choice(["add", "add", "move", "connect", "connect", "select", "edit", "toggle", "choice",
                         "connection", "delete", "undo", "redo", "copy", "save", "zoom", "note", "note"])
    if action == "add" or not nodes:
        window.palette_panel.add_requested.emit(rnd.choice(types))
    elif action == "move":
        n = rnd.choice(nodes)
        a = at(n, n.boundingRect().topLeft().toPoint() + QPoint(60, 14))
        b = a + QPoint(rnd.randint(-80, 80), rnd.randint(-60, 60))
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, a)
        QTest.mouseMove(view.viewport(), b)
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, b)
    elif action == "connect":
        outs = [p for n in nodes for (_, out), p in n.ports.items() if out]
        ins = [p for n in nodes for (_, out), p in n.ports.items() if not out]
        if outs and ins:
            a, b = view.mapFromScene(rnd.choice(outs).scenePos()), view.mapFromScene(rnd.choice(ins).scenePos())
            QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, a)
            QTest.mouseMove(view.viewport(), b)
            QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, b)
    elif action == "select":
        tab.scene.clearSelection()
        rnd.choice(nodes).setSelected(True)
    elif action == "edit":
        edits = [e for e in window.properties.findChildren(QLineEdit) if e.isVisible() and e.isEnabled()]
        if edits:
            e = rnd.choice(edits)
            e.setFocus()
            e.setText(rnd.choice(["", "x", "42", "data/sales.csv", "{day}", "a, b", "-1", "3.5"]))
            e.editingFinished.emit()
    elif action == "toggle":
        boxes = [b for b in window.properties.findChildren(QCheckBox) if b.isVisible()]
        if boxes:
            QTest.mouseClick(rnd.choice(boxes), Qt.LeftButton)
    elif action == "choice":
        combos = [c for c in window.properties.findChildren(QComboBox) if c.isVisible() and c.count() > 1]
        if combos:
            c = rnd.choice(combos)
            c.setCurrentIndex(rnd.randrange(c.count()))
            c.activated.emit(c.currentIndex())
    elif action == "connection":
        buttons = [b for b in window.properties.findChildren(QToolButton) if b.text() in ("New…", "Edit…") and b.isEnabled()]
        if buttons:
            QTest.mouseClick(rnd.choice(buttons), Qt.LeftButton)
    elif action == "delete":
        tab.scene.clearSelection()
        rnd.choice(nodes).setSelected(True)
        window.current.scene.delete_selected()
    elif action == "undo":
        tab.doc.undo()
    elif action == "redo":
        tab.doc.redo()
    elif action == "copy":
        tab.scene.clearSelection()
        rnd.choice(nodes).setSelected(True)
        window.copy_blocks()
        window.paste_blocks()
    elif action == "save":
        window.save(tab)
    elif action == "zoom":
        view.center_on_flow()
    elif action == "note":
        notes = list(tab.scene.note_items.values())
        if not notes:
            window.palette_panel.add_requested.emit(TEXT_ITEM)
        else:
            n = rnd.choice(notes)
            what = rnd.choice(["write", "resize", "move", "delete"])
            center = at(n, n.boundingRect().center().toPoint())
            if what == "write":
                QTest.mouseDClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, center)
                QTest.keyClicks(view, rnd.choice(["hi", "", "Title"]))
                QTest.keyClick(view, rnd.choice([Qt.Key_Escape, Qt.Key_Backspace, Qt.Key_Delete, Qt.Key_Return]))
                if rnd.random() < 0.5:  # or click elsewhere to finish
                    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
            elif what == "resize":
                tab.scene.clearSelection()
                n.setSelected(True)
                a = at(n, n._handle().center().toPoint())
                b = a + QPoint(rnd.randint(-80, 120), 0)
                QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, a)
                QTest.mouseMove(view.viewport(), b)
                QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, b)
            elif what == "move":
                b = center + QPoint(rnd.randint(-80, 80), rnd.randint(-60, 60))
                QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, center)
                QTest.mouseMove(view.viewport(), b)
                QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, b)
            else:
                tab.scene.clearSelection()
                n.setSelected(True)
                window.current.scene.delete_selected()
    app.processEvents()

# The document is consistent, and what it saves reopens exactly as it was.
blocks = tab.doc.blocks
for src, sp, dst, dp in tab.doc.edges():
    assert src in blocks and dst in blocks, (src, dst)
app.processEvents()
assert set(tab.scene.nodes) == set(blocks)
assert set(tab.scene.note_items) == set(tab.doc.notes)
window.save(tab)
assert FlowDocument.load(folder / "fuzz.yaml").data == tab.doc.data
try:
    load_flow(folder / "fuzz.yaml")
except FlowError:
    pass  # random settings can make an unfinished flow (e.g. a Schedule with no time): fine, it reopens
tab.doc.dirty = False
print("ok")
