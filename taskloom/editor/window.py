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
from ..engine import block_inputs, blocks_from, input_ports
from ..flow import FlowError, load_flow, run_clock, schedules_of, validate
from ..schedule import Trigger
from ..history import History, run_label
from ..launch import frozen, runner_command
from ..scheduler import (acquire_lock, running_scheduler_version, set_start_at_login, startup_file,
                         startup_script_outdated, version_tuple)
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
        return "paused", "This schedule is paused: tick Active to use it."
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
    if not scheduler_on:
        return f"next {upcoming[0]:%a %H:%M} · scheduler off", tip + "<br><br>Turn the scheduler on (top right)."
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
        self.entries: list[dict] = []  # log of the run shown on the canvas
        self.block_info: dict[str, dict] = {}  # block -> status, tables, … of the run shown
        self.shown_run: RunProcess | None = None
        self.shown_run_id: int | None = None  # the run whose results are on the canvas
        self.following_latest = True  # show each new run (incl. scheduled ones) unless an older one was opened
        self.seen_up_to: int | None = None  # the newest run of this flow you have seen (or skipped) here
        self.last: dict | None = None  # last finished run of this flow, from the history
        self.scheduled = False  # listed for the scheduler
        self.next_run: dt.datetime | None = None  # when the scheduler runs it next

    @property
    def running(self) -> bool:
        return any(r.running for r in self.runs)

    def list_status(self) -> tuple[str | None, str]:
        if self.running:
            done = sum(1 for i in self.block_info.values() if i.get("status") not in (None, "running"))
            return "running", f"running · {done} of {len(self.doc.blocks)} blocks done"
        if self.last:
            when = (self.last.get("finished") or self.last["started"] or "").replace("T", " ")[:19]  # date and seconds
            if self.last["status"] == "failed":
                return "failed", f"failed {when}"
            return self.last["status"], f"{'ok' if self.last['status'] == 'success' else self.last['status']} {when}"
        return None, "not run yet" if self.doc.path else "unsaved"


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
        self._scheduler_on = False
        # Bottom left: the flows running now (from here, on schedule or from a terminal); open one from it.
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
        act(m, "Settings…", lambda: SettingsDialog(self.home, self).exec())
        act(m, "Python environment…", lambda: PythonEnvDialog(self.home, self).exec())
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
        return self.add_tab(FlowDocument())

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
        if path.resolve() != tab.doc.path:
            tab.seen_up_to = None  # another file: which of its runs you saw is read again
        tab.doc.save(path.resolve())
        self.safety.drop_copy(tab.tab_id)
        self._load_last_run(tab)
        self._sync_schedule(tab)
        self._save_session()
        return True

    def edit_connections(self):
        ConnectionsDialog(self.home, self).exec()
        self.properties.refresh()  # new or renamed connections show up in the block's lists

    def close_tab(self, tab: FlowTab | None) -> bool:
        if tab is None:
            return False
        self._finish_editing(tab)  # what is being typed is an unsaved change too
        if tab.doc.dirty:
            answer = QMessageBox.question(self, "Unsaved changes", f"Save changes to {tab.doc.title}?",
                                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Cancel or answer == QMessageBox.Save and not self.save(tab):
                return False
        tab.autosave.stop()
        self.safety.drop_copy(tab.tab_id)
        self.tabs.removeTab(self.tabs.indexOf(tab))
        self._save_session()
        return True

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
        if not starts:
            return "This flow has no start blocks: every block in it runs."
        if manual:
            which = f"its Manual Start block {manual[0]}" if len(manual) == 1 else \
                f"its Manual Start blocks ({', '.join(manual)})"
            return (f"It runs from {which}: the blocks connected after it run; blocks connected to no start "
                    "block do not. To run from one start block only, right-click it.")
        return (f"It has no Manual Start block, so it runs from all its start blocks ({', '.join(starts)}), as "
                "if each one's time had come. To run from one of them only, right-click it.")

    def start_run(self, tab: FlowTab, overrides: dict, extra_args=(), refresh: set | None = None) -> RunProcess:
        """Start a run of the flow. `refresh`: the blocks it runs, when it does not run them all (one block,
        or a block and what follows it): the rest of the canvas keeps the run it showed."""
        run = RunProcess(str(tab.doc.path), overrides, self.home, self, extra_args)
        run.event.connect(lambda e, t=tab, r=run: self._on_event(t, r, e))
        run.output.connect(lambda line, t=tab, r=run: self._on_output(t, r, line))
        run.finished.connect(lambda status, t=tab, r=run: self._on_finished(t, r, status))
        tab.runs.append(run)
        if refresh is None:
            self._show_run(tab, run, keep_log=True)
        else:
            tab.shown_run = run
            for block in refresh:
                tab.block_info.pop(block, None)
                tab.scene.set_status(block, None)
                tab.scene.set_values(block, {})
        run.start()
        self.flow_status.update_tab(tab)
        return run

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
        shown = f" ({self._run_label(tab.shown_run_id)})" if tab.shown_run_id else ""
        rerun = menu.addAction(f"Run only this block, with the same inputs{shown}")
        rerun.setEnabled(tab.shown_run_id is not None and not tab.running)
        rerun.triggered.connect(lambda: self.rerun_block(tab, block_id))
        choose = menu.addAction("Run only this block, with inputs I choose…")
        choose.setEnabled(not tab.running)
        choose.triggered.connect(lambda: self.run_block_with_inputs(tab, block_id))
        onwards = menu.addAction(f"Run from this block on, with the same inputs{shown}")
        onwards.setToolTip("This block and every block after it; the blocks before it are not run again")
        onwards.setEnabled(tab.shown_run_id is not None and not tab.running)
        onwards.triggered.connect(lambda: self.rerun_block(tab, block_id, on=True))
        return menu

    def _show_run(self, tab: FlowTab, run: RunProcess | None, keep_log=False):
        """Put a run on the canvas. Its Log follows the earlier runs' messages (keep_log), or replaces them."""
        tab.shown_run = run
        tab.entries, tab.block_info = (tab.entries[-MAX_LOG_ENTRIES:] if keep_log else []), {}
        tab.scene.clear_statuses()
        if tab is self.current:
            self.log.set_entries(list(tab.entries))
            self.bottom_tabs.setCurrentWidget(self.log)

    def rerun_block(self, tab: FlowTab, block_id: str, on: bool = False):
        """Run one block again (`on`: and every block after it), with the inputs it got in the run on the
        canvas (e.g. after fixing it); the blocks before it are not run again."""
        if tab.shown_run_id is None:
            QMessageBox.information(self, "Run the flow first", "There is no run on the canvas to take the inputs from.")
            return
        if tab.running:
            QMessageBox.information(self, "A run is going", "Wait for it to finish, or stop it, first.")
            return
        flow = self._ready_to_run(tab)  # saves your fix and checks the flow
        if flow is None:
            return
        blocks = blocks_from(flow, self.registry, block_id) if on else {block_id}
        self.start_run(tab, {}, ["--from" if on else "--only", block_id, "--inputs-from", str(tab.shown_run_id)],
                       refresh=blocks)

    def run_block_with_inputs(self, tab: FlowTab, block_id: str):
        """Run one block with input values you type (filled in with what reached it in the run on the canvas)."""
        if tab.running:
            QMessageBox.information(self, "A run is going", "Wait for it to finish, or stop it, first.")
            return
        flow = self._ready_to_run(tab)
        if flow is None:
            return
        names = [name for name in input_ports(flow, self.registry, block_id) if name != AFTER]
        run_id, values, missing, typed = tab.shown_run_id, {}, {}, {}
        if run_id is not None:
            try:
                values, missing, typed = block_inputs(flow, self.registry, self.home, block_id, run_id)
            except Exception:  # e.g. that run's files were cleaned up: start from nothing
                run_id = None
        dialog = dialogs.ChooseInputsDialog(block_id, names, values, missing,
                                            self._run_label(run_id) if run_id is not None else None, self, typed)
        if not dialog.exec():
            return
        args = ["--only", block_id]
        # Start from the run on the canvas (the rest of the flow's results carry over), unless that run has
        # nothing for a block without inputs: running it as it was then would be refused.
        if run_id is not None and (names or not missing):
            args += ["--inputs-from", str(run_id)]
        for name, text in dialog.given().items():
            args += ["--input", f"{name}={text}"]
        self.start_run(tab, {}, args, refresh={block_id})

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
            tab.shown_run_id, tab.following_latest = e["run_id"], True
            self._mark_seen(tab, e["run_id"])
            params = " ".join(f"{k}={v}" for k, v in e.get("params", {}).items())
            self._log(tab, _started_line(e["run_id"], e["ts"], params))
        elif kind == "block_started":
            tab.block_info[block] = {"status": "running"}
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
        status = info.get("status")
        cls = self.registry.get((tab.doc.blocks.get(block) or {}).get("type"))
        if status == "skipped" and cls is not None and cls.is_trigger:
            # A start block that did not start this run (e.g. a Schedule block when you press Run): it
            # skipped nothing, so it looks as usual (a Schedule block shows when it starts the flow next).
            tab.block_info.pop(block, None)
            tab.scene.set_status(block, None)
            tab.scene.set_values(block, {})
            return
        tab.block_info[block] = {"status": status, "tables": info.get("tables") or {},
                                 "previews": info.get("previews") or {}, "error": info.get("error")}
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
                    self._apply_block_result(tab, block, {"status": "failed", "error": "the run process stopped unexpectedly"})
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
        runs = []
        if self.home.history_db.exists():
            history = self._history()
            runs = [r for r in history.runs(str(tab.doc.path), limit=20) if r["status"] != "running"]
            history.close()
        tab.last = runs[0] if runs else None
        self.flow_status.update_tab(tab)
        if tab.seen_up_to is None:
            # Just opened: runs you saw before start nothing (a flow opens clean); none yet: every run to
            # come is new.
            stored = self.settings.value(_seen_key(tab.doc.path))
            tab.seen_up_to = int(stored) if stored is not None else runs[0]["id"] if runs else 0
            self._mark_seen(tab, tab.seen_up_to)
        if tab.following_latest and not tab.running and runs:
            # Each run you have not seen yet (e.g. started by the scheduler, also while this flow was closed),
            # oldest first, its messages after the ones already shown.
            for r in reversed([r for r in runs if r["id"] > tab.seen_up_to]):
                self.show_history_run(r["id"], tab, keep_log=True)

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
        for run in running:
            started = run["started"].replace("T", " ")[:19]
            since = started[11:] if started.startswith(today) else started
            menu.addAction(f"{run['flow']}  ·  running since {since} ({run_label(run['id'], None)})",
                           lambda path=run["flow_path"]: self.open_flow(Path(path)))

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
            was = tab.doc.path in self.home.scheduled_flows()
        except ValueError:
            was = False
        if scheduled != was:
            self.home.set_scheduled(tab.doc.path, scheduled)
        self._refresh_schedule(tab)
        if scheduled and not self.scheduler_running():
            self.statusBar().showMessage("This flow has a schedule. Turn the scheduler on (top right) so it runs.", 10000)

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
                                                                      self._scheduler_on, tab.doc.dirty))

    def _show_scheduler_state(self, running: bool):
        self._scheduler_on = running
        self.scheduler_action.setIcon(scheduler_icon(running))
        self.scheduler_action.setText("Scheduler on" if running else "Scheduler off")
        version = running_scheduler_version(self.home) if running else None
        older = running and version_tuple(version) < version_tuple(__version__)
        self.scheduler_action.setToolTip(
            (f"The scheduler is from an older Taskloom ({version or 'before 0.14.1'}); this is {__version__}. "
             "Stop it and start it again here to run this version." if older else
             "The scheduler is running scheduled flows. Click to stop it.") if running else
            "Scheduled flows only run while the scheduler is on. Click to start it.")
        if older and not self._offered_restart:
            self._offered_restart = True
            answer = QMessageBox.question(
                self, "Restart the scheduler?",
                f"The scheduler that is running is from an older Taskloom ({version or 'before 0.14.1'}); this is "
                f"Taskloom {__version__}. It may not read flows saved by this version (it says they cannot be "
                "read). Restart it with this version?")
            if answer == QMessageBox.Yes:
                self.restart_scheduler()

    def restart_scheduler(self):
        """Stop the running scheduler, then start this version's once it has stopped."""
        try:
            os.kill(int((self.home.root / "scheduler.pid").read_text()), signal.SIGTERM)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Cannot stop the scheduler", f"{e}\n\nStop it from its tray icon, then start "
                                                                  "it here with the Scheduler button.")
            return
        deadline = time.monotonic() + 15

        def when_stopped():
            if not self.scheduler_running():
                self.start_scheduler()
            elif time.monotonic() < deadline:
                QTimer.singleShot(300, self, when_stopped)
            else:
                QMessageBox.warning(self, "Cannot restart the scheduler", "It did not stop. Stop it from its tray "
                                    "icon, then start it here with the Scheduler button.")

        QTimer.singleShot(300, self, when_stopped)

    def _update_start_at_login(self):
        """If you start the scheduler at login, it starts this version from now on (not an older one)."""
        try:
            path = startup_file()
            if path.exists() and startup_script_outdated(path.read_text(encoding="utf-8"), self.home):
                set_start_at_login(self.home, True)
                self.statusBar().showMessage(f"The scheduler now starts at login with Taskloom {__version__}", 10000)
        except (OSError, RuntimeError) as e:
            self.statusBar().showMessage(f"Could not update starting the scheduler at login: {e}", 10000)

    def toggle_scheduler(self):
        if not self.scheduler_running():
            self.start_scheduler()
            return
        answer = QMessageBox.question(self, "Stop the scheduler?",
                                      "Scheduled flows will not run until the scheduler is started again. "
                                      "Runs already going continue.")
        if answer == QMessageBox.Yes:
            self.stop_scheduler()

    def stop_scheduler(self):
        try:
            os.kill(int((self.home.root / "scheduler.pid").read_text()), signal.SIGTERM)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Cannot stop the scheduler", str(e))
            return
        QTimer.singleShot(1500, self, lambda: self._show_scheduler_state(self.scheduler_running()))
        self.statusBar().showMessage("Scheduler stopped", 5000)

    def start_scheduler(self):
        if self.scheduler_running():
            self.statusBar().showMessage("The scheduler is already running", 5000)
            return
        self._offered_restart = True  # this version's: nothing to offer
        subprocess.Popen(
            runner_command() + ["scheduler"], env={**os.environ, "TASKLOOM_HOME": str(self.home.root)},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=sys.platform != "win32",
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.statusBar().showMessage("Scheduler started", 5000)
        QTimer.singleShot(2000, self, lambda: self._show_scheduler_state(self.scheduler_running()))

    def _refresh_history(self, tab: FlowTab):
        if tab.doc.path is None or not self.home.history_db.exists():
            self.history_panel.show_runs([])
            return
        history = self._history()
        self.history_panel.show_runs(history.runs(str(tab.doc.path)))
        history.close()

    def show_history_run(self, run_id: int, tab: FlowTab | None = None, keep_log=False):
        """Show a past run: its results on the canvas, its messages in the Log (after the earlier
        runs' messages with keep_log, else on their own)."""
        tab = tab or self.current
        if tab is None:
            return
        history = self._history()
        run, blocks, logs = history.run(run_id), history.blocks(run_id), history.logs(run_id)
        history.close()
        self._show_run(tab, None, keep_log)
        tab.shown_run_id = run_id
        self._mark_seen(tab, run_id)
        tab.following_latest = tab.last is None or run_id >= tab.last["id"]
        for block, info in blocks.items():
            self._apply_block_result(tab, block, info)
        if run:
            logs = [_started_line(run_id, run["started"]),
                    *logs, _finished_line(run_id, run["status"], run["finished"] or run["started"])]
        tab.entries += logs
        if tab is self.current:
            self.log.set_entries(list(tab.entries))
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
        for tab in self.tabs_list():
            if not self.close_tab(tab):
                self._quitting = False
                event.ignore()
                return
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
