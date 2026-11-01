"""File → Commit History…: a window of its own with the Git history of a flow file (the commits git._versions finds).
On its left, a search over the commits (their messages, then the outputs of the latest run of each version, then the
code of its blocks, then the rest of its flow file) and the cards of the commits it finds; on its right, a read-only
preview of the flow as it was at the selected commit, with the outputs of the latest run made with exactly that
version, and Restore this version."""

from __future__ import annotations

import copy
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolButton,
                               QVBoxLayout, QWidget)

from ..block import fields
from ..config import Home
from ..flow import flow_file_data, version_id
from ..history import History
from . import git
from .canvas import FlowScene, is_dark
from .document import FlowDocument
from .panels import INVALID, FindToggles, find_pattern

CARD = Qt.UserRole  # a card's (Version, where the search found it)
PAD = 6


@dataclass
class Version:
    """A commit that changed the flow file: its id, message and author date (local time); the file's bytes then and
    what they hold (None: not a flow file, `problem` says why); the latest finished run made with exactly those bytes
    (None: none) and its blocks' results."""
    id: str
    message: str
    when: str
    data: bytes
    flow: dict | None
    problem: str
    run: dict | None
    results: dict

    @property
    def subject(self) -> str:
        return self.message.split("\n", 1)[0]

    @property
    def body(self) -> str:
        """The rest of the message, on one line."""
        return " ".join(self.message.partition("\n")[2].split())


def load_versions(path: Path, home: Home) -> list[Version]:
    """The versions of the flow file `path`, newest first (none: no repository, or the file never committed)."""
    found = git._versions(path)
    if found is None:
        return []
    history = History(home.history_db) if home.history_db.exists() else None  # (none yet: no run was made)
    try:
        runs = history.version_runs(str(path)) if history else {}
        versions = []
        for commit, mode, blob in found[0]:
            try:
                flow, problem = flow_file_data(blob.data), ""
            except ValueError as e:
                flow, problem = None, str(e)
            if not isinstance(flow, dict):
                flow, problem = None, problem or "not a flow file"
            run = runs.get(version_id(blob.data))
            results = ({block: {**info, "run_id": run["id"]} for block, info in history.blocks(run["id"]).items()}
                       if run else {})
            versions.append(Version(commit.id.decode(), commit.message.decode("utf-8", "replace").strip(),
                                    dt.datetime.fromtimestamp(commit.author_time).strftime("%Y-%m-%d %H:%M:%S"),
                                    blob.data, flow, problem, run, results))
        return versions
    finally:
        if history:
            history.close()


def _found(pattern: re.Pattern, value) -> bool:
    """Whether `pattern` matches in `value` (an empty match is none, as in the find bar)."""
    return value is not None and any(m.end() > m.start() for m in pattern.finditer(str(value)))


def where_found(version: Version, pattern: re.Pattern, registry) -> tuple[int, str] | None:
    """Where `pattern` is found in `version`, the first of: (0) its message, (1) the outputs of its run (each block's
    result summary and previews), (2) the code of its blocks (the value of each code setting), (3) anything else in its
    flow file. None: nowhere."""
    if _found(pattern, version.message):
        return 0, "in the message"
    for block, result in version.results.items():
        if any(_found(pattern, value) for value in [result.get("summary"), *(result.get("previews") or {}).values()]):
            return 1, f"in the outputs of {block}"
    blocks = (version.flow or {}).get("blocks")
    for block, spec in (blocks.items() if isinstance(blocks, dict) else ()):
        cls = registry.get(spec.get("type")) if isinstance(spec, dict) else None
        config = spec.get("config") if cls else None
        if isinstance(config, dict) and any(isinstance(f, fields.Code) and _found(pattern, config.get(key))
                                            for key, f in cls.config.items()):
            return 2, f"in the code of {block}"
    if _found(pattern, version.data.decode("utf-8", "replace")):
        return 3, "in the flow file"
    return None


class CardDelegate(QStyledItemDelegate):
    """A commit's card: its message's first line in bold, then the rest (on one line, shortened to fit), where the
    search found it, and at the bottom right its author date and short id."""

    def sizeHint(self, option, index):
        version, where = index.data(CARD)
        return QSize(10, (2 + bool(version.body) + bool(where)) * option.fontMetrics.lineSpacing() + 3 * PAD)

    def paint(self, painter, option, index):
        version, where = index.data(CARD)
        shown = QStyleOptionViewItem(option)
        self.initStyleOption(shown, index)
        shown.text = ""
        option.widget.style().drawControl(QStyle.CE_ItemViewItem, shown, painter, option.widget)  # (its selection)
        palette = option.palette
        selected = bool(option.state & QStyle.State_Selected)
        text = palette.highlightedText().color() if selected else palette.text().color()
        muted = text if selected else palette.placeholderText().color()
        box = QRectF(option.rect).adjusted(2 * PAD, PAD, -2 * PAD, -2 * PAD)
        line = option.fontMetrics.lineSpacing()
        painter.save()
        bold = QFont(option.font)
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(text)
        painter.drawText(QRectF(box.left(), box.top(), box.width(), line), Qt.AlignLeft | Qt.AlignVCenter,
                         QFontMetrics(bold).elidedText(version.subject, Qt.ElideRight, int(box.width())))
        painter.setFont(option.font)
        top = box.top() + line
        for words, pen in ((version.body, text), (where, muted)):
            if words:
                painter.setPen(pen)
                painter.drawText(QRectF(box.left(), top, box.width(), line), Qt.AlignLeft | Qt.AlignVCenter,
                                 option.fontMetrics.elidedText(words, Qt.ElideRight, int(box.width())))
                top += line
        painter.setPen(muted)
        painter.drawText(QRectF(box.left(), top, box.width(), line), Qt.AlignRight | Qt.AlignVCenter,
                         f"{version.when} · {version.id[:7]}")
        painter.restore()


class Preview(QGraphicsView):
    """The flow as it was at a commit, drawn as on the canvas and fitted to the pane. Nothing in it can be changed:
    the view gives the blocks and texts no click, key, drag or drop (only their tooltips show), and it has no menu."""

    def __init__(self):
        super().__init__()
        self.setInteractive(False)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # (all of it shows)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()

    def fit(self):
        """All of the flow in view, not bigger than on the canvas."""
        if self.scene() is None:
            return
        rect = self.scene().itemsBoundingRect().adjusted(-30, -30, 30, 30)
        self.fitInView(rect, Qt.KeepAspectRatio)
        if self.transform().m11() > 1:
            self.resetTransform()
            self.centerOn(rect.center())


class CommitHistory(QWidget):
    """The window of a flow file's Git history (`path`), not modal. Its search field with the ".*" (regular
    expressions, on as it opens) and "Aa" (match case, off) toggles filters the cards at once, grouped by where the
    pattern is found (see where_found); the first card is selected, Up and Down (also in the field) move through them.
    Restore this version sends the selected version's flow, and what the status bar says, by `restore`."""

    restore = Signal(object, str)  # not dict: Qt would pass it as a QVariantMap, which sorts every mapping's keys

    def __init__(self, path: Path, registry, home: Home, parent: QWidget):
        super().__init__(parent, Qt.Window)
        self.path, self.registry, self.home = path, registry, home
        self.versions: list[Version] = []
        self.none = ""  # what the list says when there are no commits
        self._scene: FlowScene | None = None
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"{path.name} — Commit History")
        self.resize(1100, 680)
        self.toggles = FindToggles(self)  # its own, as it opens: not the find bars'
        self.field = QLineEdit(placeholderText="Search the messages, then the outputs, the code, the flow file")
        self.field.installEventFilter(self)  # (Up and Down)
        search = QHBoxLayout()
        search.addWidget(self.field, 1)
        for action in (self.toggles.regex, self.toggles.case):
            button = QToolButton(autoRaise=True)
            button.setDefaultAction(action)
            search.addWidget(button)
            action.toggled.connect(self.search)
        self.empty = QLabel(wordWrap=True, textFormat=Qt.PlainText)  # no commits, or none found
        self.list = QListWidget()
        self.list.setItemDelegate(CardDelegate(self.list))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.itemSelectionChanged.connect(self._show_selected)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        column.addLayout(search)
        column.addWidget(self.empty)
        column.addWidget(self.list, 1)
        self.run_line = QLabel(wordWrap=True, textFormat=Qt.PlainText)  # which run's outputs the preview shows
        self.preview = Preview()
        self.restore_button = QPushButton("Restore this version", toolTip=(
            "Put this version into the flow's tab, as a change you can undo; Save or Save & Commit to keep it"))
        self.restore_button.clicked.connect(self._restore)
        right = QWidget()
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.run_line)
        column.addWidget(self.preview, 1)
        column.addWidget(self.restore_button, 0, Qt.AlignRight)
        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([400, 700])
        QVBoxLayout(self).addWidget(splitter)
        self.field.textChanged.connect(self.search)
        self.load()

    def load(self):
        """Read the history again (e.g. after a Save & Commit): the search kept."""
        self.versions = load_versions(self.path, self.home)
        self.none = (f"No commits of {self.path.name} yet: File → Save & Commit… commits it, and each commit is a "
                     "version you can come back to here.")
        if not self.versions:
            try:
                git.commit_target(self.path)
            except git.GitError as e:  # e.g. a repository that cannot be read: not "none yet"
                self.none = f"No commits of {self.path.name} can be shown: {e}."
        self.search()

    def search(self):
        """The cards of the commits the field's text is found in, grouped by where (all of them if it is empty), the
        first one selected."""
        pattern, problem = find_pattern(self.field.text(), self.toggles)
        red = f"color: {INVALID[is_dark()]};" if problem else ""
        if self.field.styleSheet() != red:
            self.field.setStyleSheet(red)
        self.field.setToolTip(problem)
        if not self.field.text():
            found = [(version, "") for version in self.versions]
        else:
            places = [(place, version) for version in (self.versions if pattern else [])
                      if (place := where_found(version, pattern, self.registry))]
            found = [(version, where) for (_, where), version in sorted(places, key=lambda p: p[0][0])]  # (stable)
        self.list.clear()
        for version, where in found:
            item = QListWidgetItem(f"{version.message}\n{where}\n{version.when} {version.id[:7]}")
            item.setData(CARD, (version, where))
            item.setToolTip(version.message)
            self.list.addItem(item)
        self.empty.setText(problem or ("No commit matches." if self.versions else self.none))
        self.empty.setVisible(not found)
        if found:
            self.list.setCurrentRow(0)
        else:
            self._show_selected()  # (emptying the list says nothing)

    def eventFilter(self, watched, event):
        if watched is self.field and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Up, Qt.Key_Down):
            row = self.list.currentRow() + (1 if event.key() == Qt.Key_Down else -1)
            if 0 <= row < self.list.count():
                self.list.setCurrentRow(row)
            return True
        return super().eventFilter(watched, event)

    def selected(self) -> Version | None:
        items = self.list.selectedItems()
        return items[0].data(CARD)[0] if items else None

    def _show_selected(self):
        """The selected version in the preview, with the outputs of its run; Restore for it."""
        version = self.selected()
        flow = copy.deepcopy(version.flow) if version and version.flow else None
        scene = FlowScene(FlowDocument(flow, self.path), self.registry)
        for block, info in (version.results if flow else {}).items():
            scene.show_result(block, info)
        self.preview.setScene(scene)
        self._scene = scene  # (only now the one before goes)
        self.preview.fit()
        if version is None:
            said = ""
        elif flow is None:
            said = f"This version of {self.path.name} cannot be shown: {version.problem}"
        elif version.run:
            said = (f"Outputs of run #{version.run['id']}, {version.run['started'].replace('T', ' ')[:19]}: the latest "
                    "run made with exactly this version")
        else:
            said = "No run was made with exactly this version"
        self.run_line.setText(said)
        self.restore_button.setEnabled(flow is not None)

    def _restore(self):
        version = self.selected()
        if version is not None and version.flow is not None:
            self.restore.emit(copy.deepcopy(version.flow), f"Restored the version of {version.when} "
                                                           f"({version.subject}): Save or Save & Commit to keep it")
