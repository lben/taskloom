"""The editor's main window."""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import yaml
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QMainWindow, QMenu, QMessageBox, QSizePolicy,
                               QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .. import __version__, upgrade
from .. import params as P
from ..block import AFTER
from ..config import Home
from ..engine import block_inputs, input_ports
from ..flow import FlowError, load_flow, read_flow_file, run_clock, schedules_of, validate
from ..schedule import Trigger
from ..history import History, run_label
from ..launch import frozen, runner_command
from ..scheduler import (acquire_lock, paused, running_scheduler_version, set_paused, set_start_at_login,
                         startup_file, startup_script_outdated, version_tuple)
from ..registry import discover
from .canvas import FlowScene, FlowView, value_html
from . import dialogs
from .dialogs import AskDialog, ConnectionsDialog, OverlapDialog, PythonEnvDialog, RunDialog, SettingsDialog
from .document import FlowDocument
from .panels import (FlowSettingsPanel, FlowTabsStatus, HistoryPanel, LogPanel, Palette, ParametersPanel,
                     PreviewPanel, PropertiesPanel, scheduler_icon)
from .runs import RunProcess
from .safety import CRASH_LOG, Safety, pid_alive

CLIPBOARD_KEY = "taskloom_blocks"
FLOW_FILTER = "Taskloom flows (*.yaml *.yml)"


def _short(text: str, n: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


MAX_LOG_ENTRIES = 20000  # the Log keeps earlier runs' messages, up to this many lines


def _finished_line(run_id: int, status: str, ts: str) -> dict:
    return {"ts": ts, "level": "DONE" if status == "success" else "ERROR", "message": f"run #{run_id} {status}",
            "header": True}


def schedule_countdown(config: dict, now: dt.datetime, scheduled: bool, scheduler_on: bool,
                       unsaved: bool) -> tuple[str, str]:
    """(Text under a Schedule block, its tooltip): when it starts the flow next."""
    if config.get("active", True) is False:
        return "not active", "This schedule is not active: it does not start the flow. Tick Active to use it."
    timing = {k: config[k] for k in ("cron", "at") if config.get(k) not in (None, "")}
    try:
        trigger = Trigger({"schedule": timing})
    except ValueError:
        return "needs a time", "Set its cron (repeating times) or at (one time)."
    upcoming, t = [], now
    while len(upcoming) < 5 and (t := trigger.next_after(t)) is not None:
        upcoming.append(t)
    if not upcoming:
        return "no more runs", "Its time has passed."
    tip = "Next runs:<br>" + "<br>".join(f"{t:%a %d %b %Y %H:%M}" for t in upcoming)
    if unsaved or not scheduled:
        return f"next {upcoming[0]:%a %H:%M} · save to schedule", tip + "<br><br>Save the flow to schedule this."
    if not scheduler_on:  # no time: none comes while it is off
        return "scheduler service off", ("The scheduler service is off, so this schedule does not start the flow. "
                                         "The Scheduler service button (top right) turns it on.")
    left = int((upcoming[0] - now).total_seconds())
    days, rest = divmod(left, 86400)
    clock = f"{rest // 3600:02d}:{rest % 3600 // 60:02d}:{rest % 60:02d}"
    return f"next run in {f'{days}d ' if days else ''}{clock}", tip


def _seen_key(path) -> str:
    """Where editor.ini remembers the newest run of a flow you have seen."""
    return "seen/" + hashlib.sha1(str(path).encode()).hexdigest()[:16]


def _started_line(run_id: int, ts: str, params: str = "") -> dict:
    return {"ts": ts, "level": "INFO", "message": f"{run_label(run_id, ts)} started" + (f" {params}" if params else ""),
            "header": True}


class HoverMenuButton(QToolButton):
    """A button whose menu opens when you hover it (or click it)."""

    def __init__(self, text: str):
        super().__init__(text=text)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setAutoRaise(True)
        self._hover = QTimer(self, singleShot=True, interval=300)
        self._hover.timeout.connect(lambda: self.underMouse() and self.menu() and not self.menu().isEmpty()
                                    and self.showMenu())

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hover.start()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hover.stop()


class FlowTab(QWidget):
    """One open flow: its document, canvas, runs and what they reported."""

    def __init__(self, doc: FlowDocument, registry):
        super().__init__()
        self.doc = doc
        self.scene = FlowScene(doc, registry)
        self.view = FlowView(self.scene)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.runs: list[RunProcess] = []
        self.queued: list[dict] = []
        self.tab_id = uuid.uuid4().hex[:12]  # names this tab's autosaved copy
        self.autosave = QTimer(self, singleShot=True, interval=1500)
        self.entries: list[dict] = []  # log of the runs shown on the canvas
        self.block_info: dict[str, dict] = {}  # block -> status, tables, …, run_id: the result it shows
        self.shown_run: RunProcess | None = None  # the run going whose events the canvas shows
        self.run_blocks: list[str] = []  # the blocks that run takes part in
        # Show each block's latest result (incl. scheduled runs), unless an older run was opened: among the runs
        # after since_run (the newest you had seen when the flow was opened).
        self.following_latest = True
        self.since_run = 0
        self.seen_up_to: int | None = None  # the newest run of this flow you have seen (or skipped) here
        # Its runs to show once they have finished, even if a newer one is seen first: those still going (in the
        # history) when it last looked, and those begun just before one of yours.
        self.going: set[int] = set()
        self.last: dict | None = None  # last finished run of this flow, from the history
        self.line_results: list[tuple[str, dict | None]] = []  # each start block, its line's last finished run
        self.scheduled = False  # listed for the scheduler
        self.next_run: dt.datetime | None = None  # when the scheduler runs it next

    @property
    def running(self) -> bool:
        return any(r.running for r in self.runs)

    def result_run(self, block: str) -> int | None:
        """The run the result a block shows comes from (None: it shows none)."""
        return (self.block_info.get(block) or {}).get("run_id")

    def list_status(self) -> tuple[str | None, str]:
        if self.running:
            done = sum(1 for b in self.run_blocks if (self.block_info.get(b) or {}).get("status") not in (None, "running"))
            return "running", f"running · {done} of {len(self.run_blocks)} blocks done" if self.run_blocks else "running"
        if self.line_results:  # one line per start block: how its line last ended
            ended = [run["status"] for _, run in self.line_results if run]
            return ("failed" if "failed" in ended else "success" if ended else None), "\n".join(
                f"{start}: {_ended(run) if run else 'not run yet'}" for start, run in self.line_results)
        if self.last:
            return self.last["status"], _ended(self.last)
        return None, "not run yet" if self.doc.path else "unsaved"


def _ended(run: dict) -> str:
    """'ok 2026-10-20 10:28:20' (when it finished), 'failed …'."""
    when = (run.get("finished") or run["started"] or "").replace("T", " ")[:19]  # date and seconds
    return f"{'ok' if run['status'] == 'success' else run['status']} {when}"


class MainWindow(QMainWindow):
    def __init__(self, home: Home | None = None):
        super().__init__()
        self.home = home or Home.default()
        self.registry = discover(self.home.blocks_dir)
        # Editor preferences live next to the rest of the user's Taskloom files.
        self.settings = QSettings(str(self.home.root / "editor.ini"), QSettings.IniFormat)
        self.safety = Safety(self.home.root)
        self._quitting = False
        self.resize(1440, 900)

        self.tabs = QTabWidget(tabsClosable=True, movable=True, documentMode=True)
        self.setCentralWidget(self.tabs)

        self.palette_panel = Palette(self.registry)
        self._dock("Blocks", self.palette_panel, Qt.LeftDockWidgetArea)
        self.flow_status = FlowTabsStatus(self.tabs)  # each flow's last result and next run, on its tab

        self.properties = PropertiesPanel(self.home)
        self.parameters = ParametersPanel(self.home)
        self.flow_settings = FlowSettingsPanel(self.home)
        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self.properties, "Properties")
        self.right_tabs.addTab(self.parameters, "Parameters")
        self.right_tabs.addTab(self.flow_settings, "Flow")
        self.inspector = self._dock("Inspector", self.right_tabs, Qt.RightDockWidgetArea)

        self.log = LogPanel()
        self.preview = PreviewPanel()
        self.history_panel = HistoryPanel()
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.addTab(self.log, "Log")
        self.bottom_tabs.addTab(self.preview, "Data preview")
        self.bottom_tabs.addTab(self.history_panel, "Run history")
        self.output = self._dock("Output", self.bottom_tabs, Qt.BottomDockWidgetArea)

        self._make_actions()
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabCloseRequested.connect(lambda i: self.close_tab(self.tabs.widget(i)))
        self.palette_panel.add_requested.connect(lambda t: self.current and self.current.view.add_at_center(t))
        self.history_panel.open_run.connect(self.show_history_run)
        self.log.filter_cleared.connect(lambda: self.current and self.current.scene.clearSelection())
        self._countdown = QTimer(self, interval=1000)
        self._countdown.timeout.connect(self._show_countdowns)
        self._countdown.start()
        # Bottom left: the flows running now (from here, on schedule or from a terminal); open one from it.
        self._schedules_cache: dict = {}  # flow file -> (modified, its schedules)
        self.running_flows = HoverMenuButton("No flows running")
        self.running_flows.setMenu(QMenu(self.running_flows))
        self.running_flows.setToolTip("The flows running now, also on schedule or from a terminal: choose one to "
                                      "open it")
        self.statusBar().addWidget(self.running_flows)
        self._offered_restart = False  # asked once per session to restart an older scheduler
        if frozen() and sys.platform == "win32":
            self._update_start_at_login()
        self._poll = QTimer(self, interval=2000)
        self._poll.timeout.connect(self._refresh_statuses)
        self._poll.start()
        if self.registry.warnings:
            self.statusBar().showMessage(f"{len(self.registry.warnings)} problem(s) with blocks: "
                                         + "; ".join(self.registry.warnings), 30000)

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_sized", False):
            self._sized = True
            self.resizeDocks([self.output], [220], Qt.Vertical)
            self.resizeDocks([self.inspector], [470], Qt.Horizontal)
            QTimer.singleShot(0, self, lambda: self.current and self.current.view.center_on_flow())
            QTimer.singleShot(0, self, self._check_after_update)
            QTimer.singleShot(0, self, self._start_scheduler_if_needed)

    def _check_after_update(self):
        """The first time a version starts: check your flows, and offer to fix what it can."""
        if self.settings.value("checked_version") == __version__:
            return
        self.settings.setValue("checked_version", __version__)
        self.check_flows(intro=f"Taskloom {__version__} checked your open and scheduled flows.", quiet=True)

    def check_flows(self, intro: str = "", quiet: bool = False):
        """Check the open and scheduled flows; offer to fix (after a backup) what this version can fix."""
        paths = [t.doc.path for t in self.tabs_list() if t.doc.path is not None]
        try:
            paths += self.home.scheduled_flows()
        except ValueError:
            pass
        paths = [p for p in dict.fromkeys(Path(p).resolve() for p in paths) if p.exists()]
        reports = [upgrade.check(p, self.registry, self.home) for p in paths]
        if quiet and all(r.ok for r in reports):
            return
        if not dialogs.CheckFlowsDialog(reports, self, intro).exec():
            return
        done, skipped = [], []
        for report in (r for r in reports if r.fixes):
            tab = next((t for t in self.tabs_list() if t.doc.path == report.path), None)
            if tab is not None and tab.doc.dirty:
                skipped.append(report.path.name)  # it checked the file, not your unsaved changes
                continue
            backup = upgrade.fix(report, self.home)
            done.append(f"{report.path.name} (backup: {backup})")
            if tab is not None:
                tab.doc.restore(report.fixed)
                tab.doc.dirty = False
                self._on_doc_changed(tab)
        QMessageBox.information(self, "Check flows",
                                ("Fixed:\n" + "\n".join(done) if done else "Nothing was fixed.")
                                + ("\n\nNot fixed, because they have unsaved changes (save them and check again): "
                                   + ", ".join(skipped) if skipped else ""))

    def _dock(self, title, widget, area) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(title)
        dock.setWidget(widget)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        dock.setTitleBarWidget(QWidget())
        self.addDockWidget(area, dock)
        return dock

    def _make_actions(self):
        def act(menu, text, slot, shortcut=None):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(slot)
            menu.addAction(action)
            return action

        bar = self.menuBar()
        m = bar.addMenu("&File")
        act(m, "New flow", self.new_flow, QKeySequence.New)
        act(m, "Open…", self.open_dialog, QKeySequence.Open)
        act(m, "Save", lambda: self.save(self.current), QKeySequence.Save)
        act(m, "Save as…", lambda: self.save(self.current, ask=True), QKeySequence.SaveAs)
        act(m, "Close flow", lambda: self.close_tab(self.current), QKeySequence.Close)
        m.addSeparator()
        act(m, "Quit", self.close, QKeySequence.Quit)
        m = bar.addMenu("&Edit")
        act(m, "Undo", lambda: self.current and self.current.doc.undo(), QKeySequence.Undo)
        act(m, "Redo", lambda: self.current and self.current.doc.redo(), QKeySequence.Redo)
        m.addSeparator()
        # Only on the canvas: elsewhere (the Log, text fields) Ctrl+C and Ctrl+V copy and paste text.
        for action in (act(m, "Copy", self.copy_blocks, QKeySequence.Copy),
                       act(m, "Paste", self.paste_blocks, QKeySequence.Paste)):
            action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            self.tabs.addAction(action)
        act(m, "Add text", lambda: self.add_text(self.current))
        delete = act(m, "Delete", lambda: self.current and self.current.scene.delete_selected())
        delete.setShortcuts([QKeySequence.Delete, QKeySequence(Qt.Key_Backspace)])
        delete.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.tabs.addAction(delete)
        m = bar.addMenu("&Run")
        self.run_action = act(m, "▶ Run now", lambda: self.run_flow(self.current), "F5")
        self.stop_action = act(m, "Stop", lambda: self.stop(self.current), "Shift+F5")
        self.validate_action = act(m, "Validate", lambda: self.validate(self.current), "F7")
        m = bar.addMenu("&Tools")
        act(m, "Connections…", self.edit_connections)
        act(m, "Settings…", lambda: dialogs.open_dialog(self, SettingsDialog, self.home))
        act(m, "Python environment…", lambda: dialogs.open_dialog(self, PythonEnvDialog, self.home))
        act(m, "Check flows…", lambda: self.check_flows())
        toolbar = self.addToolBar("Run")
        toolbar.setObjectName("run-toolbar")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.addAction(self.validate_action)
        toolbar.addAction(self.stop_action)
        toolbar.addAction(self.run_action)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        self.scheduler_action = QAction(self)
        self.scheduler_action.triggered.connect(self.toggle_scheduler)
        toolbar.addAction(self.scheduler_action)
        self._show_scheduler_state(False)

    # --- tabs -----------------------------------------------------------------------

    @property
    def current(self) -> FlowTab | None:
        return self.tabs.currentWidget()

    def tabs_list(self) -> list[FlowTab]:
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def add_tab(self, doc: FlowDocument) -> FlowTab:
        tab = FlowTab(doc, self.registry)
        tab.scene.block_selected.connect(lambda block_id, t=tab: self._on_block_selected(t, block_id))
        tab.scene.block_menu.connect(lambda block_id, pos, t=tab: self._block_menu(t, block_id, pos))
        tab.scene.note_selected.connect(lambda note_id, t=tab: self._on_note_selected(t, note_id))
        tab.scene.canvas_menu.connect(lambda at, pos, t=tab: self._canvas_menu_for(t, at).exec(pos))
        doc.changed.connect(lambda t=tab: self._on_doc_changed(t))
        doc.changed.connect(tab.autosave.start)
        tab.autosave.timeout.connect(lambda t=tab: self._autosave(t))
        self.tabs.addTab(tab, doc.title)
        self._refresh_schedule(tab)
        self._load_last_run(tab)
        self.flow_status.update_tab(tab)
        self.tabs.setCurrentWidget(tab)
        QTimer.singleShot(0, tab.view.center_on_flow)
        self._save_session()
        return tab

    # --- keeping work safe ------------------------------------------------------------

    def _autosave(self, tab: FlowTab):
        """Copy unsaved changes to the recovery folder (the flow file is only written on Save)."""
        if tab.doc.dirty:
            self.safety.write_copy(tab.tab_id, tab.doc.path, tab.doc.data)
        else:
            self.safety.drop_copy(tab.tab_id)

    def _save_session(self):
        if not self._quitting:
            self.settings.setValue("session/flows", [str(t.doc.path) for t in self.tabs_list() if t.doc.path])

    def restore_session(self, paths=()):
        """Reopen the flows that were open last time, then bring back unsaved changes left by an
        editor that did not close normally (a crash, or the computer restarting)."""
        crashed = self.safety.start()
        previous = self.settings.value("session/flows") or []
        previous = [previous] if isinstance(previous, str) else list(previous)
        for path in dict.fromkeys([*previous, *map(str, paths)]):
            if Path(path).exists():
                self.open_flow(Path(path))
        recovered = []
        for copy in self.safety.orphaned_copies():
            path = Path(copy["path"]) if copy.get("path") else None
            tab = next((t for t in self.tabs_list() if path and t.doc.path == path), None)
            if tab is None:
                tab = self.add_tab(FlowDocument(copy["data"], path))
                tab.doc.dirty = True
            else:
                tab.doc.restore(copy["data"])
            self._on_doc_changed(tab)
            self._autosave(tab)
            copy["file"].unlink(missing_ok=True)
            recovered.append(tab.doc.title)
        if crashed or recovered:
            text = "Taskloom did not close normally last time." if crashed else ""
            if recovered:
                text += f" Unsaved changes were recovered in: {', '.join(recovered)} (not saved yet)."
            if crashed:
                text += f" Details, if any, are in {self.home.root / CRASH_LOG}."
            QMessageBox.information(self, "Work recovered", text.strip())

    def show_error(self, details: str):
        """An unexpected error: tell the user instead of disappearing (it is also in crash.log)."""
        QMessageBox.warning(self, "Something went wrong",
                            "Taskloom hit an unexpected error but is still running. Save your work.\n\n"
                            f"{details[-1500:]}\n\nThe details are in {self.home.root / CRASH_LOG}.")

    def new_flow(self) -> FlowTab:
        """A new flow, with a Manual Start: a flow starts from a start block (swap it for a Schedule to run
        it at set times)."""
        doc = FlowDocument()
        doc.data["blocks"]["start"] = {"type": "trigger.manual", "ui": {"x": -280, "y": 0}}  # on the left
        return self.add_tab(doc)

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open flow", str(Path.cwd()), FLOW_FILTER)
        if path:
            self.open_flow(Path(path))

    def open_flow(self, path: Path) -> FlowTab | None:
        path = Path(path).resolve()
        for tab in self.tabs_list():
            if tab.doc.path == path:
                self.tabs.setCurrentWidget(tab)
                return tab
        try:
            doc = FlowDocument.load(path)
        except Exception as e:
            QMessageBox.warning(self, "Cannot open flow", str(e))
            return None
        return self.add_tab(doc)

    def save(self, tab: FlowTab | None, ask: bool = False) -> bool:
        if tab is None:
            return False
        self._finish_editing(tab)
        path = tab.doc.path
        if ask or path is None:
            suggested = str(path or Path.cwd() / f"{tab.doc.data.get('name') or 'flow'}.yaml")
            chosen, _ = QFileDialog.getSaveFileName(self, "Save flow", suggested, FLOW_FILTER)
            if not chosen:
                return False
            path = Path(chosen)
        before = tab.doc.path
        try:
            tab.doc.save(path.resolve())
        except OSError as e:  # e.g. a read-only file, or a folder you may not write to
            QMessageBox.warning(self, "Cannot save flow", f"{e}\n\nThe changes are not saved.")
            return False
        if tab.doc.path != before:
            tab.seen_up_to = None  # another file: which of its runs you saw is read again
        self.safety.drop_copy(tab.tab_id)
        self._load_last_run(tab)
        self._sync_schedule(tab)
        self._save_session()
        return True

    def edit_connections(self):
        dialogs.open_dialog(self, ConnectionsDialog, self.home)
        self.properties.refresh()  # new or renamed connections show up in the block's lists

    def close_tab(self, tab: FlowTab | None) -> bool:
        if tab is None or not self._may_close(tab):
            return False
        self._remove_tab(tab)
        return True

    def _may_close(self, tab: FlowTab) -> bool:
        """Its unsaved changes saved or discarded, as you answer; False if you cancel (or saving fails)."""
        self._finish_editing(tab)  # what is being typed is an unsaved change too
        if tab.doc.dirty:
            answer = QMessageBox.question(self, "Unsaved changes", f"Save changes to {tab.doc.title}?",
                                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Cancel or answer == QMessageBox.Save and not self.save(tab):
                return False
        return True

    def _remove_tab(self, tab: FlowTab):
        tab.autosave.stop()
        self.safety.drop_copy(tab.tab_id)
        self.tabs.removeTab(self.tabs.indexOf(tab))
        self._save_session()

    def _on_tab_changed(self, index):
        tab = self.current
        if tab is None:
            self.properties.show_block(None, self.registry, None)
            return
        self.parameters.show_doc(tab.doc)
        self.flow_settings.show_doc(tab.doc)
        self._on_block_selected(tab, (tab.scene.selected_block_ids() or [None])[0])
        notes = tab.scene.selected_note_ids()
        if not tab.scene.selected_block_ids() and len(notes) == 1:
            self._on_note_selected(tab, notes[0])
        self.log.set_entries(list(tab.entries))
        self._refresh_history(tab)
        self._update_title()

    def _on_doc_changed(self, tab: FlowTab):
        self.tabs.setTabText(self.tabs.indexOf(tab), tab.doc.title + (" *" if tab.doc.dirty else ""))
        self._refresh_schedule(tab)
        self.flow_status.update_tab(tab)
        if tab is self.current:
            self.properties.on_doc_changed()
            self.parameters.on_doc_changed()
            self.flow_settings.refresh()
            self._update_title()

    def _update_title(self):
        tab = self.current
        self.setWindowTitle(f"Taskloom — {tab.doc.title}{' *' if tab.doc.dirty else ''}" if tab else "Taskloom")

    def _on_block_selected(self, tab: FlowTab, block_id):
        if tab is not self.current:
            return
        self.properties.show_block(tab.doc, self.registry, block_id)
        if block_id is not None:
            self.right_tabs.setCurrentWidget(self.properties)
        self.log.set_filter(block_id)
        if block_id is not None:
            self.preview.show_outputs(block_id, tab.block_info.get(block_id))

    # --- copy and paste -------------------------------------------------------------

    def copy_blocks(self):
        tab = self.current
        ids = tab.scene.selected_block_ids() if tab else []
        if not ids:
            return
        edges = [t for t in tab.doc.data["edges"] if any(t.startswith(f"{i}.") for i in ids)]
        data = {CLIPBOARD_KEY: {i: tab.doc.blocks[i] for i in ids}, "edges": edges}
        QApplication.clipboard().setText(yaml.safe_dump(data, sort_keys=False))

    def paste_blocks(self):
        tab = self.current
        if tab is None:
            return
        try:
            data = yaml.safe_load(QApplication.clipboard().text())
        except yaml.YAMLError:
            return
        if isinstance(data, dict) and isinstance(data.get(CLIPBOARD_KEY), dict):
            new_ids = tab.doc.paste_blocks(data[CLIPBOARD_KEY], data.get("edges") or [], 40, 40)
            tab.scene.clearSelection()
            for block_id in new_ids:
                tab.scene.nodes[block_id].setSelected(True)

    # --- running --------------------------------------------------------------------

    def _finish_editing(self, tab: FlowTab):
        """Before saving, running or closing a flow: what you are typing (a field in Properties, a text on
        the canvas) goes into it first."""
        if self.properties.doc is tab.doc:
            self.properties._finish_editing()
        tab.scene.commit_editing()

    def _ready_to_run(self, tab: FlowTab):
        """Save and validate; returns the loaded flow or None."""
        self._finish_editing(tab)
        if tab.doc.dirty or tab.doc.path is None:
            if not self.save(tab):
                return None
        try:
            flow = load_flow(tab.doc.path)
            errors, warnings = validate(flow, self.registry, self.home)
        except (FlowError, ValueError) as e:
            errors, warnings, flow = getattr(e, "errors", [str(e)]), [], None
        now = dt.datetime.now().isoformat(timespec="milliseconds")
        for message in warnings:
            self._log(tab, {"ts": now, "level": "WARNING", "message": message})
        if errors:
            for message in errors:
                self._log(tab, {"ts": now, "level": "ERROR", "message": message})
            self.bottom_tabs.setCurrentWidget(self.log)
            QMessageBox.warning(self, "The flow has problems", "\n".join(errors[:12]))
            return None
        return flow

    def validate(self, tab: FlowTab | None):
        if tab is not None and self._ready_to_run(tab):
            self.statusBar().showMessage(f"{tab.doc.title}: no problems found", 5000)

    def run_flow(self, tab: FlowTab | None, start: str | None = None):
        """Run the flow: from its Manual Start blocks (all of its start blocks if it has none), or from the
        start block `start` (e.g. right-click a Schedule block: as if its time had come)."""
        if tab is None:
            return
        extra = ["--trigger", start] if start else []
        if start is None and not upgrade.start_blocks(tab.doc.data, self.registry):
            # A flow starts from a start block: offer the one that runs it as before (every block).
            if not dialogs.offer_manual_start(self, tab.doc.title):
                return
            tab.doc.add_manual_start(self.registry)
        flow = self._ready_to_run(tab)
        if flow is None:
            return
        if start is None and self.settings.value("ask_before_run_now", "yes") != "no":
            go, never = dialogs.confirm_run_now(self, flow.name, self._run_now_says(flow))
            if never:
                self.settings.setValue("ask_before_run_now", "no")
            if not go:
                return
        overrides = {}
        if flow.params:
            _, funcs = run_clock(flow, self.home)
            try:
                computed = P.resolve(flow.params, {}, funcs)
            except ValueError as e:
                QMessageBox.warning(self, "Parameters", str(e))
                return
            dialog = RunDialog(flow.name, flow.params, computed, self)
            if not dialog.exec():
                return
            overrides = dialog.overrides()
        if tab.running:
            key = f"overlap/{tab.doc.path}"
            choice = self.settings.value(key)
            if choice is None:
                current = next(r for r in reversed(tab.runs) if r.running)
                detail = f"Run #{current.run_id} has not finished yet." if current.run_id else "It has just started."
                dialog = OverlapDialog(flow.name, detail, self)
                if not dialog.exec():
                    return
                choice = dialog.choice
                if dialog.remember.isChecked():
                    self.settings.setValue(key, choice)
            if choice == OverlapDialog.SKIP:
                return
            if choice == OverlapDialog.QUEUE:
                tab.queued.append((overrides, extra))
                self.statusBar().showMessage("Queued: it starts when the current run finishes", 5000)
                return
        self.start_run(tab, overrides, extra)

    def _run_now_says(self, flow) -> str:
        """What Run now runs in this flow."""
        starts = [b for b, spec in flow.blocks.items()
                  if (cls := self.registry.get(spec.type)) is not None and cls.is_trigger]
        manual = [b for b in starts if flow.blocks[b].type == "trigger.manual"]
        if manual:
            which = f"its Manual Start block {manual[0]}" if len(manual) == 1 else \
                f"its Manual Start blocks ({', '.join(manual)})"
            return (f"It runs from {which}: the blocks connected after it run; blocks connected to no start "
                    "block do not. To run from one start block only, right-click it.")
        if len(starts) == 1:
            return (f"It has no Manual Start block, so it starts from its Schedule block {starts[0]}, as if its "
                    "time had come.")
        return (f"It has no Manual Start block, so it starts from each of its Schedule blocks "
                f"({', '.join(starts)}) at once, as if their times had come. To start from one of them only, "
                "right-click it.")

    def start_run(self, tab: FlowTab, overrides: dict, extra_args=(), rerun: bool = False) -> RunProcess:
        """Start a run of the flow. Only the blocks it takes part in are cleared (when it starts, it says
        which; one refused before it starts clears none); the others keep the results they show. `rerun`: it
        runs one block again (or a block and what follows it)."""
        run = RunProcess(str(tab.doc.path), overrides, self.home, self, extra_args)
        run.event.connect(lambda e, t=tab, r=run: self._on_event(t, r, e))
        run.output.connect(lambda line, t=tab, r=run: self._on_output(t, r, line))
        run.finished.connect(lambda status, t=tab, r=run: self._on_finished(t, r, status))
        tab.runs.append(run)
        if not tab.following_latest:  # an older run was on the canvas: each block's latest result again
            self._show_results(tab)
        tab.shown_run, tab.run_blocks = run, []
        if not rerun:
            tab.entries = tab.entries[-MAX_LOG_ENTRIES:]  # its messages follow the earlier runs'
            if tab is self.current:
                self.log.set_entries(list(tab.entries))
                self.bottom_tabs.setCurrentWidget(self.log)
        run.start()
        self.flow_status.update_tab(tab)
        return run

    def _clear_blocks(self, tab: FlowTab, blocks):
        for block in blocks:
            tab.block_info.pop(block, None)
            tab.scene.set_status(block, None)
            tab.scene.set_values(block, {})

    def _run_label(self, run_id: int) -> str:
        history = self._history()
        try:
            return history.label(run_id)
        finally:
            history.close()

    def _canvas_menu_for(self, tab: FlowTab, at) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Add text here", lambda: self.add_text(tab, at))
        return menu

    def add_text(self, tab: FlowTab | None, at=None):
        """Put a text (a title, a remark) on the canvas and select it, to write it in Properties."""
        if tab is None:
            return
        if at is None:
            at = tab.view.mapToScene(tab.view.viewport().rect().center())
        tab.scene.add_note_at(at)

    def _on_note_selected(self, tab: FlowTab, note_id: str):
        if tab is self.current:
            self.properties.show_note(tab.doc, note_id)
            self.right_tabs.setCurrentWidget(self.properties)

    def _block_menu(self, tab: FlowTab, block_id: str, pos):
        self._block_menu_for(tab, block_id).exec(pos)

    def _block_menu_for(self, tab: FlowTab, block_id: str) -> QMenu:
        menu = QMenu(self)
        cls = self.registry.get((tab.doc.blocks.get(block_id) or {}).get("type"))
        if cls is not None and cls.is_trigger:  # a start block: the flow starts from it (nothing to retry in it)
            label = ("Run the flow now, as if its time had come" if cls.type_id == "trigger.schedule"
                     else "Run the flow from here")
            menu.addAction(label, lambda: self.run_flow(tab, start=block_id))
            return menu
        run_id = tab.result_run(block_id)
        shown = f" ({self._run_label(run_id)})" if run_id else ""
        rerun = menu.addAction(f"Run only this block, with the same inputs{shown}")
        rerun.setEnabled(run_id is not None and not tab.running)
        rerun.triggered.connect(lambda: self.rerun_block(tab, block_id))
        choose = menu.addAction("Run only this block, with inputs I choose…")
        choose.setEnabled(not tab.running)
        choose.triggered.connect(lambda: self.run_block_with_inputs(tab, block_id))
        onwards = menu.addAction(f"Run from this block on, with the same inputs{shown}")
        onwards.setToolTip("This block and every block after it; the blocks before it are not run again")
        onwards.setEnabled(run_id is not None and not tab.running)
        onwards.triggered.connect(lambda: self.rerun_block(tab, block_id, on=True))
        choose_on = menu.addAction("Run from this block on, with inputs I choose…")
        choose_on.setEnabled(not tab.running)
        choose_on.triggered.connect(lambda: self.run_block_with_inputs(tab, block_id, on=True))
        results = menu.addAction("Results of this block…")
        results.setToolTip("Its results in the earlier runs: when each run started, what started it, its values")
        results.setEnabled(tab.doc.path is not None)
        results.triggered.connect(lambda: self.show_block_results(tab, block_id))
        return menu

    def show_block_results(self, tab: FlowTab, block_id: str):
        """A block's results in the runs it took part in; one of those runs can be put on the canvas."""
        history = self._history()
        try:
            results = history.block_results(str(tab.doc.path), block_id)
        finally:
            history.close()
        dialog = dialogs.BlockResultsDialog(block_id, results, self)
        if dialog.exec() and dialog.run_id is not None:
            self.show_history_run(dialog.run_id, tab)

    def rerun_block(self, tab: FlowTab, block_id: str, on: bool = False):
        """Run one block again (`on`: and every block after it), with the inputs it got in the run of the result
        it shows (e.g. after fixing it); the blocks before it are not run again."""
        run_id = tab.result_run(block_id)
        if run_id is None:
            QMessageBox.information(self, "Run the flow first", "This block shows no result to take the inputs from.")
            return
        if tab.running:
            QMessageBox.information(self, "A run is going", "Wait for it to finish, or stop it, first.")
            return
        flow = self._ready_to_run(tab)  # saves your fix and checks the flow
        if flow is None:
            return
        self.start_run(tab, {}, ["--from" if on else "--only", block_id, "--inputs-from", str(run_id)], rerun=True)

    def run_block_with_inputs(self, tab: FlowTab, block_id: str, on: bool = False):
        """Run one block (`on`: and the blocks after it) with input values you type for it (filled in with
        what reached it in the run of the result it shows; showing none, in the latest run among the results
        the blocks connected into it show)."""
        if tab.running:
            QMessageBox.information(self, "A run is going", "Wait for it to finish, or stop it, first.")
            return
        flow = self._ready_to_run(tab)
        if flow is None:
            return
        names = [name for name in input_ports(flow, self.registry, block_id) if name != AFTER]
        run_id = tab.result_run(block_id) or max(
            filter(None, (tab.result_run(src) for src, _, dst, _ in tab.doc.edges() if dst == block_id)), default=None)
        values, missing, typed = {}, {}, {}
        if run_id is not None:
            try:
                values, missing, typed = block_inputs(flow, self.registry, self.home, block_id, run_id)
            except Exception:  # e.g. that run's files were cleaned up: start from nothing
                run_id = None
        dialog = dialogs.ChooseInputsDialog(block_id, names, values, missing,
                                            self._run_label(run_id) if run_id is not None else None, self, typed)
        if not dialog.exec():
            return
        args = ["--from" if on else "--only", block_id]
        # Start from that run (the rest of the flow's results carry over), unless that run has
        # nothing for a block without inputs: running it as it was then would be refused.
        if run_id is not None and (names or not missing):
            args += ["--inputs-from", str(run_id)]
        for name, text in dialog.given().items():
            args += ["--input", f"{name}={text}"]
        self.start_run(tab, {}, args, rerun=True)

    def stop(self, tab: FlowTab | None):
        if tab is None:
            return
        tab.queued.clear()
        for run in tab.runs:
            if run.running:
                run.stop()
        self.statusBar().showMessage("Stopping…", 3000)

    def _log(self, tab: FlowTab, entry: dict):
        tab.entries.append(entry)
        if tab is self.current:
            self.log.append(entry)

    def _on_output(self, tab, run, line):
        if run is tab.shown_run and line.strip():
            self._log(tab, {"ts": dt.datetime.now().isoformat(), "level": "ERROR", "message": line})

    def _on_event(self, tab: FlowTab, run: RunProcess, e: dict):
        kind = e.get("event")
        if kind == "ask":  # questions are answered whichever run is on screen
            self._ask(run, e)
            return
        if run is not tab.shown_run:
            return
        block = e.get("block")
        if kind == "run_started":
            tab.following_latest = True
            # The runs of the flow since the editor last looked (another line's, e.g. started by the scheduler as
            # you pressed Run now) are still to show, once they have finished.
            history = self._history()
            try:
                tab.going |= {r["id"] for r in history.runs(str(tab.doc.path), limit=20)
                              if (tab.seen_up_to or 0) < r["id"] < e["run_id"]}
            finally:
                history.close()
            self._mark_seen(tab, e["run_id"])
            tab.run_blocks = e["blocks"]
            self._clear_blocks(tab, e["blocks"])  # the blocks it takes part in; the others keep what they show
            params = " ".join(f"{k}={v}" for k, v in e.get("params", {}).items())
            self._log(tab, _started_line(e["run_id"], e["ts"], params))
        elif kind == "block_started":
            tab.block_info[block] = {"status": "running", "run_id": e["run_id"]}
            text = "running" if e["attempt"] == 1 else f"running · attempt {e['attempt']}"
            tab.scene.set_status(block, "running", text)
        elif kind == "log":
            self._log(tab, e)
        elif kind == "block_finished":
            self._apply_block_result(tab, block, e)
        elif kind == "run_finished":
            self._log(tab, _finished_line(e["run_id"], e["status"], e["ts"]))
        self.flow_status.update_tab(tab)

    def _apply_block_result(self, tab: FlowTab, block: str, info: dict):
        """Show a block's result in run info["run_id"] (an event of it, or its row in the history)."""
        status = info.get("status")
        tab.block_info[block] = {"status": status, "tables": info.get("tables") or {},
                                 "previews": info.get("previews") or {}, "error": info.get("error"),
                                 "run_id": info.get("run_id")}
        values = dict(info.get("previews") or {})
        if status == "success":
            values["done"] = "finished successfully"
        elif status == "failed" and info.get("error"):
            values["error"] = info["error"]
        tab.scene.set_values(block, values)
        attempts = info.get("attempts") or 0
        retried = f" · retried {attempts - 1}×" if attempts > 1 else ""
        if status == "success":
            text = "✓ " + _short(info.get("summary") or "done", 48) + retried
            # Hovering it shows each output as the connections do (e.g. JSON laid out).
            tip = "".join(f"<b>{html.escape(port)}</b><pre>{value_html(value)}</pre>"
                          for port, value in (info.get("previews") or {}).items())
            tip = (tip or html.escape(info.get("summary") or "done")) + html.escape(retried)
        elif status == "failed":
            text = "✕ " + _short(info.get("error") or "failed", 48)
            tip = f"<pre>{value_html(info.get('error') or 'failed')}</pre>"
        elif status == "skipped" and info.get("summary"):
            text, tip = f"skipped · {info['summary']}", html.escape(f"skipped: {info['summary']}")
        else:
            text = tip = status
        tab.scene.set_status(block, status, text, tip)
        if tab is self.current and block in tab.scene.selected_block_ids():
            self.preview.show_outputs(block, tab.block_info[block])

    def _ask(self, run: RunProcess, e: dict):
        dialog = AskDialog(e.get("block", ""), e.get("prompt", ""), e.get("kind", "text"),
                           e.get("options") or [], e.get("default"), self)
        dialog.setObjectName("ask-dialog")
        dialog.finished.connect(lambda result: run.answer(e["ask_id"], dialog.value if result else None))
        dialog.open()

    def _on_finished(self, tab: FlowTab, run: RunProcess, status: str):
        if run is tab.shown_run:
            for block, info in tab.block_info.items():
                if info.get("status") == "running":  # the process died mid-block
                    self._apply_block_result(tab, block, {"status": "failed", "run_id": info["run_id"],
                                                          "error": "the run process stopped unexpectedly"})
        self._load_last_run(tab)
        if tab is self.current:
            self._refresh_history(tab)
        if tab.queued and not tab.running:
            self.start_run(tab, *tab.queued.pop(0))
        self.flow_status.update_tab(tab)

    # --- history --------------------------------------------------------------------

    def _history(self) -> History:
        return History(self.home.history_db)

    def _load_last_run(self, tab: FlowTab):
        if tab.doc.path is None:
            return
        lines = tab.doc.lines(self.registry)
        runs, going, tab.line_results = [], set(), [(start, None) for start in lines]
        if self.home.history_db.exists():
            history = self._history()
            runs = history.runs(str(tab.doc.path), limit=20)
            going = {r["id"] for r in runs if r["status"] == "running"}
            runs = [r for r in runs if r["status"] != "running"]
            # How each start block's line last ended: in a run it started, or in a re-run of a block of its line.
            tab.line_results = [(start, history.last_run(str(tab.doc.path), [start, *(f"{how} {b}" for b in line
                                                                                     for how in ("only", "from"))]))
                                for start, line in lines.items()]
            history.close()
        tab.last = runs[0] if runs else None
        self.flow_status.update_tab(tab)
        if tab.seen_up_to is None:
            # Just opened: runs you saw before start nothing (a flow opens clean); none yet: every run to
            # come is new.
            stored = self.settings.value(_seen_key(tab.doc.path))
            tab.seen_up_to = tab.since_run = int(stored) if stored is not None else runs[0]["id"] if runs else 0
            self._mark_seen(tab, tab.seen_up_to)
        if tab.following_latest and not tab.running:
            # Each run you have not seen yet (e.g. started by the scheduler, also while this flow was closed),
            # oldest first: its messages after the ones already shown, its results on its blocks, and it in Run
            # history. A run older than one seen (another line's) is new once it has finished.
            unseen = [r["id"] for r in reversed(runs) if r["id"] > tab.seen_up_to or r["id"] in tab.going]
            tab.going = going
            for run_id in unseen:
                self._show_log(tab, run_id, keep_log=True)
            if unseen:
                self._show_results(tab)
                if tab is self.current:
                    self._refresh_history(tab)

    def _mark_seen(self, tab: FlowTab, run_id: int):
        tab.seen_up_to = max(tab.seen_up_to or 0, run_id)
        if tab.doc.path is not None:
            key = _seen_key(tab.doc.path)
            self.settings.setValue(key, max(int(self.settings.value(key) or 0), run_id))

    def _refresh_statuses(self):
        for tab in self.tabs_list():
            self._refresh_schedule(tab)
            if not tab.running:
                self._load_last_run(tab)
        self._show_scheduler_state(self.scheduler_running())
        self._show_running_flows()

    def _show_running_flows(self):
        """The flows running now, wherever they were started; a run whose process is gone is recorded as failed."""
        if not self.home.history_db.exists():
            return
        history = self._history()
        try:
            running = []
            for run in history.running():
                if run["pid"] is None:
                    continue  # from before runs recorded their process: cannot tell
                if pid_alive(run["pid"]):
                    running.append(run)
                else:
                    history.mark_crashed(run["id"], dt.datetime.now().isoformat(timespec="milliseconds"))
        finally:
            history.close()
        count = len(running)
        self.running_flows.setText("No flows running" if not count else
                                   f"{count} flow{'s' if count > 1 else ''} running")
        menu = self.running_flows.menu()
        menu.clear()
        today = dt.date.today().isoformat()
        try:
            scheduled = {Path(p).resolve() for p in self.home.scheduled_flows()}
        except ValueError:
            scheduled = set()
        for run in running:
            started = run["started"].replace("T", " ")[:19]
            since = started[11:] if started.startswith(today) else started
            text = f"{run['flow']}  ·  running since {since} ({run_label(run['id'], None)})"
            path = Path(run["flow_path"]).resolve()
            upcoming = self._next_scheduled(path) if path in scheduled else None  # (the service runs only those)
            if upcoming is not None:  # its soonest schedule, which comes only while the service is on
                text += (f"  ·  next run {upcoming:%Y-%m-%d %H:%M:%S}" if self.flow_status.scheduler_on else
                         "  ·  scheduler service off")
            menu.addAction(text, lambda path=run["flow_path"]: self.open_flow(Path(path)))

    def _next_scheduled(self, path: Path) -> dt.datetime | None:
        """When the soonest of a flow's schedules comes next (from its saved file), or None."""
        try:
            mtime = path.stat().st_mtime
            cached = self._schedules_cache.get(path)
            if cached is None or cached[0] != mtime:
                data = read_flow_file(path) or {}
                cached = self._schedules_cache[path] = (mtime, schedules_of(data)[0] if isinstance(data, dict) else [])
        except (OSError, ValueError):  # e.g. not UTF-8, or not YAML
            return None
        now = dt.datetime.now()
        return min((t for t in (trigger.next_after(now) for trigger in cached[1]) if t), default=None)

    # --- scheduling -----------------------------------------------------------------

    def _refresh_schedule(self, tab: FlowTab):
        try:
            tab.scheduled = tab.doc.path is not None and tab.doc.path in self.home.scheduled_flows()
        except ValueError:
            tab.scheduled = False
        now = dt.datetime.now()
        times = [t.next_after(now) for t in schedules_of(tab.doc.data)[0]] if tab.scheduled else []
        tab.next_run = min((t for t in times if t), default=None)
        self.flow_status.update_tab(tab)

    def _sync_schedule(self, tab: FlowTab):
        """A saved flow with a Schedule block is run by the scheduler; without one it is not."""
        scheduled = bool(schedules_of(tab.doc.data)[0])
        try:
            if scheduled != (tab.doc.path in self.home.scheduled_flows()):
                self.home.set_scheduled(tab.doc.path, scheduled)
        except ValueError as e:  # e.g. edited by hand: writing it again would lose the flows it lists
            if scheduled:
                QMessageBox.warning(self, "Flow not scheduled", f"This flow is saved, but not scheduled: {e}\n\n"
                                    "Put that file right, then save the flow again.")
            scheduled = False
        self._refresh_schedule(tab)
        if scheduled and not self.scheduler_running():
            if paused(self.home):
                self.statusBar().showMessage("This flow is scheduled, but the scheduler service is off (you turned "
                                             "it off): turn it on (top right) to run it.", 10000)
            else:
                self.start_scheduler()

    def _start_scheduler_if_needed(self):
        """Flows are scheduled: the scheduler service runs them (unless you turned it off)."""
        try:
            scheduled = bool(self.home.scheduled_flows())
        except ValueError:
            scheduled = False
        if scheduled and not paused(self.home) and not self.scheduler_running():
            self.start_scheduler()

    def scheduler_running(self) -> bool:
        """The scheduler holds its lock file while it runs."""
        try:
            acquire_lock(self.home).close()  # we got it, so nobody holds it
            return False
        except RuntimeError:
            return True

    def _show_countdowns(self):
        """Under each Schedule block of the flow on screen: when it starts the flow next."""
        tab = self.current
        if tab is None:
            return
        now = dt.datetime.now().replace(microsecond=0)
        for block_id, spec in tab.doc.blocks.items():
            if spec.get("type") == "trigger.schedule":
                tab.scene.set_countdown(block_id, *schedule_countdown(spec.get("config") or {}, now, tab.scheduled,
                                                                      self.flow_status.scheduler_on, tab.doc.dirty))

    def _show_scheduler_state(self, running: bool):
        if running != self.flow_status.scheduler_on:  # the tabs say when their flows run next only while it is on
            self.flow_status.scheduler_on = running
            for tab in self.tabs_list():
                self.flow_status.update_tab(tab)
        self.scheduler_action.setIcon(scheduler_icon(running))
        self.scheduler_action.setText("Scheduler service on" if running else "Scheduler service off")
        version = running_scheduler_version(self.home) if running else None
        older = running and version_tuple(version) < version_tuple(__version__)
        self.scheduler_action.setToolTip(
            (f"The scheduler service is from an older Taskloom ({version or 'before 0.14.1'}); this is "
             f"{__version__}. Turn it off and on again here to run this version." if older else
             "The scheduler service runs in the background, also while the editor is closed (its icon is in the "
             "tray), and starts your scheduled flows at their times. It starts by itself when a flow is "
             "scheduled. Click to turn it off: no scheduled flow runs until you turn it on again.") if running else
            "The scheduler service is off: scheduled flows do not run. Click to turn it on.")
        if older and not self._offered_restart:
            self._offered_restart = True
            answer = QMessageBox.question(
                self, "Restart the scheduler service?",
                f"The scheduler service running is from an older Taskloom ({version or 'before 0.14.1'}); this is "
                f"Taskloom {__version__}. It may not read flows saved by this version (it says they cannot be "
                "read). Restart it with this version?")
            if answer == QMessageBox.Yes:
                self.restart_scheduler()

    def restart_scheduler(self):
        """Stop the running scheduler, then start this version's once it has stopped."""
        try:
            os.kill(int((self.home.root / "scheduler.pid").read_text()), signal.SIGTERM)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Cannot stop the scheduler service", f"{e}\n\nTurn it off from its tray "
                                "icon, then turn it on here (top right).")
            return
        deadline = time.monotonic() + 15

        def when_stopped():
            if not self.scheduler_running():
                self.start_scheduler()
            elif time.monotonic() < deadline:
                QTimer.singleShot(300, self, when_stopped)
            else:
                QMessageBox.warning(self, "Cannot restart the scheduler service", "It did not stop. Turn it off "
                                    "from its tray icon, then turn it on here (top right).")

        QTimer.singleShot(300, self, when_stopped)

    def _update_start_at_login(self):
        """If you start the scheduler at login, it starts this version from now on (not an older one)."""
        try:
            path = startup_file()
            if path.exists() and startup_script_outdated(path.read_text(encoding="utf-8"), self.home):
                set_start_at_login(self.home, True)
                self.statusBar().showMessage(f"The scheduler service now starts at login with Taskloom {__version__}",
                                             10000)
        except (OSError, RuntimeError) as e:
            self.statusBar().showMessage(f"Could not update starting the scheduler service at login: {e}", 10000)

    def toggle_scheduler(self):
        if not self.scheduler_running():
            self.start_scheduler(by_you=True)
            return
        answer = QMessageBox.question(self, "Turn off the scheduler service?",
                                      "No scheduled flow runs until you turn it on again (it does not start by "
                                      "itself meanwhile). Runs already going continue.")
        if answer == QMessageBox.Yes:
            self.pause_scheduler()

    def pause_scheduler(self):
        """Turn the scheduler service off, and keep it off until you turn it on."""
        set_paused(self.home, True)
        self.stop_scheduler()

    def stop_scheduler(self):
        try:
            os.kill(int((self.home.root / "scheduler.pid").read_text()), signal.SIGTERM)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Cannot stop the scheduler service", str(e))
            return
        QTimer.singleShot(1500, self, lambda: self._show_scheduler_state(self.scheduler_running()))
        self.statusBar().showMessage("Scheduler service off", 5000)

    def start_scheduler(self, by_you: bool = False):
        if by_you:
            set_paused(self.home, False)  # you turned it on: it starts by itself again when needed
        if self.scheduler_running():
            self.statusBar().showMessage("The scheduler service is already on", 5000)
            return
        self._offered_restart = True  # this version's: nothing to offer
        subprocess.Popen(
            runner_command() + ["scheduler"], env={**os.environ, "TASKLOOM_HOME": str(self.home.root)},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=sys.platform != "win32",
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.statusBar().showMessage("Scheduler service on: it runs your scheduled flows in the background", 8000)
        QTimer.singleShot(2000, self, lambda: self._show_scheduler_state(self.scheduler_running()))

    def _refresh_history(self, tab: FlowTab):
        if tab.doc.path is None or not self.home.history_db.exists():
            self.history_panel.show_runs([])
            return
        history = self._history()
        self.history_panel.show_runs(history.runs(str(tab.doc.path)))
        history.close()

    def show_history_run(self, run_id: int, tab: FlowTab | None = None):
        """Show a past run on purpose: only its results on the canvas (the newest run: each block's latest
        result again, following the runs to come), its messages in the Log."""
        tab = tab or self.current
        if tab is None:
            return
        tab.shown_run = None
        newest = tab.last is None or run_id >= tab.last["id"]
        if newest:  # it counts even if you had seen it before opening the flow
            tab.since_run = min(tab.since_run, run_id - 1)
        self._show_results(tab, None if newest else run_id)
        self._show_log(tab, run_id)

    def _show_results(self, tab: FlowTab, run_id: int | None = None):
        """On the canvas: each block's result from the latest run (since the flow was opened) it took part in,
        following the runs to come; or only run `run_id`'s results."""
        history = self._history()
        try:
            if run_id is None:
                results = history.latest_block_results(str(tab.doc.path), tab.since_run)
            else:
                results = {block: {**info, "run_id": run_id} for block, info in history.blocks(run_id).items()}
        finally:
            history.close()
        tab.following_latest = run_id is None
        tab.block_info = {}
        tab.scene.clear_statuses()
        for block, info in results.items():
            self._apply_block_result(tab, block, info)

    def _show_log(self, tab: FlowTab, run_id: int, keep_log=False):
        """A past run's messages in the Log (after the earlier runs' messages with keep_log, else on their own)."""
        history = self._history()
        run, logs = history.run(run_id), history.logs(run_id)
        history.close()
        self._mark_seen(tab, run_id)
        if run:
            logs = [_started_line(run_id, run["started"]),
                    *logs, _finished_line(run_id, run["status"], run["finished"] or run["started"])]
        tab.entries = (tab.entries[-MAX_LOG_ENTRIES:] if keep_log else []) + logs
        if tab is self.current:
            self.log.set_entries(list(tab.entries))
            self.bottom_tabs.setCurrentWidget(self.log)
            self.statusBar().showMessage(f"Showing {run_label(run_id, run['started'] if run else None)}", 5000)

    def closeEvent(self, event):
        running = [r for t in self.tabs_list() for r in t.runs if r.running]
        if running:
            answer = QMessageBox.question(self, "Runs in progress",
                                          f"{len(running)} run(s) are still going. Stop them and quit?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            for run in running:
                run.stop()
            for run in running:
                if not run.proc.waitForFinished(8000):
                    run.proc.kill()
        self._save_session()
        self._quitting = True  # closing the tabs below must not empty the remembered session
        if not all(self._may_close(tab) for tab in self.tabs_list()):  # cancelled: every flow stays open
            self._quitting = False
            event.ignore()
            return
        for tab in self.tabs_list():
            self._remove_tab(tab)
        self.safety.stop()
        event.accept()


def main(paths=()) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Taskloom")
    app.setOrganizationName("Taskloom")
    window = MainWindow()
    window.safety.install_crash_log(on_error=window.show_error)
    window.show()
    window.restore_session(paths)
    if not window.tabs_list():
        window.new_flow()
    if os.environ.get("TASKLOOM_EDITOR_SMOKE") and paths:
        _smoke_test(app, window, window.open_flow(Path(paths[0])))  # used by packaging/smoke.py
    return app.exec()


def _smoke_test(app, window: MainWindow, tab: FlowTab):
    """Run the flow from the editor (through the runner program) and quit with 0 if it succeeded."""
    run = window.start_run(tab, {})
    run.finished.connect(lambda status: app.exit(0 if status == "success" else 1))
    QTimer.singleShot(120_000, window, lambda: app.exit(2))
