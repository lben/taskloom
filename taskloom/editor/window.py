"""The editor's main window."""

from __future__ import annotations

import datetime as dt
import os
import signal
import subprocess
import sys
from pathlib import Path

import yaml
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QMainWindow, QMessageBox, QSizePolicy, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import params as P
from ..config import Home
from ..flow import FlowError, load_flow, run_clock, validate
from ..history import History
from ..launch import runner_command
from ..schedule import Trigger
from ..scheduler import acquire_lock
from ..registry import discover
from .canvas import FlowScene, FlowView
from .dialogs import AskDialog, ConnectionsDialog, OverlapDialog, RunDialog, SettingsDialog
from .document import FlowDocument
from .panels import (FlowSettingsPanel, FlowsPanel, HistoryPanel, LogPanel, Palette, ParametersPanel,
                     PreviewPanel, PropertiesPanel, scheduler_icon)
from .runs import RunProcess

CLIPBOARD_KEY = "taskloom_blocks"
FLOW_FILTER = "Taskloom flows (*.yaml *.yml)"


def _short(text: str, n: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


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
        self.entries: list[dict] = []  # log of the run shown on the canvas
        self.block_info: dict[str, dict] = {}  # block -> status, tables, … of the run shown
        self.shown_run: RunProcess | None = None
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
            when = (self.last.get("finished") or self.last["started"] or "")[11:16]
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
        self.resize(1440, 900)

        self.tabs = QTabWidget(tabsClosable=True, movable=True, documentMode=True)
        self.setCentralWidget(self.tabs)

        self.palette_panel = Palette(self.registry)
        self.flows_panel = FlowsPanel()
        left = QSplitter(Qt.Vertical)
        left.addWidget(self.palette_panel)
        left.addWidget(self.flows_panel)
        left.setSizes([520, 300])
        self._dock("Blocks and flows", left, Qt.LeftDockWidgetArea)

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
        self.flows_panel.activated.connect(lambda tab: self.tabs.setCurrentWidget(tab))
        self.flows_panel.run_requested.connect(self.run_flow)
        self.flows_panel.last_run_requested.connect(self._open_last_run)
        self.flows_panel.schedule_toggled.connect(self.set_scheduled)
        self.flow_settings.schedule_toggled.connect(lambda on: self.current and self.set_scheduled(self.current, on))
        self.history_panel.open_run.connect(self.show_history_run)
        self.log.filter_cleared.connect(lambda: self.current and self.current.scene.clearSelection())
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
            QTimer.singleShot(0, lambda: self.current and self.current.view.center_on_flow())

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
        act(m, "Copy", self.copy_blocks, QKeySequence.Copy)
        act(m, "Paste", self.paste_blocks, QKeySequence.Paste)
        delete = act(m, "Delete", lambda: self.current and self.current.scene.delete_selected())
        delete.setShortcuts([QKeySequence.Delete, QKeySequence(Qt.Key_Backspace)])
        delete.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.tabs.addAction(delete)
        m = bar.addMenu("&Run")
        self.run_action = act(m, "▶ Run", lambda: self.run_flow(self.current), "F5")
        self.stop_action = act(m, "Stop", lambda: self.stop(self.current), "Shift+F5")
        self.validate_action = act(m, "Validate", lambda: self.validate(self.current), "F7")
        m = bar.addMenu("&Tools")
        act(m, "Connections…", lambda: ConnectionsDialog(self.home, self).exec())
        act(m, "Settings…", lambda: SettingsDialog(self.home, self).exec())
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
        doc.changed.connect(lambda t=tab: self._on_doc_changed(t))
        self.tabs.addTab(tab, doc.title)
        self._refresh_schedule(tab)
        self.flows_panel.add(tab)
        self._load_last_run(tab)
        self.tabs.setCurrentWidget(tab)
        QTimer.singleShot(0, tab.view.center_on_flow)
        return tab

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
        path = tab.doc.path
        if ask or path is None:
            suggested = str(path or Path.cwd() / f"{tab.doc.data.get('name') or 'flow'}.yaml")
            chosen, _ = QFileDialog.getSaveFileName(self, "Save flow", suggested, FLOW_FILTER)
            if not chosen:
                return False
            path = Path(chosen)
        tab.doc.save(path.resolve())
        self._load_last_run(tab)
        return True

    def close_tab(self, tab: FlowTab | None) -> bool:
        if tab is None:
            return False
        if tab.doc.dirty:
            answer = QMessageBox.question(self, "Unsaved changes", f"Save changes to {tab.doc.title}?",
                                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Cancel or answer == QMessageBox.Save and not self.save(tab):
                return False
        self.flows_panel.remove(tab)
        self.tabs.removeTab(self.tabs.indexOf(tab))
        return True

    def _on_tab_changed(self, index):
        tab = self.current
        if tab is None:
            self.properties.show_block(None, self.registry, None)
            return
        self.flows_panel.select(tab)
        self.parameters.show_doc(tab.doc)
        self.flow_settings.show_doc(tab.doc)
        self._on_block_selected(tab, (tab.scene.selected_block_ids() or [None])[0])
        self.log.set_entries(list(tab.entries))
        self._refresh_history(tab)
        self._update_title()

    def _on_doc_changed(self, tab: FlowTab):
        self.tabs.setTabText(self.tabs.indexOf(tab), tab.doc.title + (" *" if tab.doc.dirty else ""))
        self._refresh_schedule(tab)
        self.flows_panel.update_tab(tab)
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
            self.preview.show_tables(block_id, tab.block_info.get(block_id, {}).get("tables"))

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

    def _ready_to_run(self, tab: FlowTab):
        """Save and validate; returns the loaded flow or None."""
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

    def run_flow(self, tab: FlowTab | None):
        if tab is None:
            return
        flow = self._ready_to_run(tab)
        if flow is None:
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
                tab.queued.append(overrides)
                self.statusBar().showMessage("Queued: it starts when the current run finishes", 5000)
                return
        self.start_run(tab, overrides)

    def start_run(self, tab: FlowTab, overrides: dict) -> RunProcess:
        run = RunProcess(str(tab.doc.path), overrides, self.home, self)
        run.event.connect(lambda e, t=tab, r=run: self._on_event(t, r, e))
        run.output.connect(lambda line, t=tab, r=run: self._on_output(t, r, line))
        run.finished.connect(lambda status, t=tab, r=run: self._on_finished(t, r, status))
        tab.runs.append(run)
        self._show_run(tab, run)
        run.start()
        self.flows_panel.update_tab(tab)
        return run

    def _show_run(self, tab: FlowTab, run: RunProcess | None):
        tab.shown_run = run
        tab.entries, tab.block_info = [], {}
        tab.scene.clear_statuses()
        if tab is self.current:
            self.log.set_entries([])
            self.bottom_tabs.setCurrentWidget(self.log)

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
            params = " ".join(f"{k}={v}" for k, v in e.get("params", {}).items())
            self._log(tab, {"ts": e["ts"], "level": "INFO", "message": f"run #{e['run_id']} started {params}"})
        elif kind == "block_started":
            tab.block_info[block] = {"status": "running"}
            text = "running" if e["attempt"] == 1 else f"running · attempt {e['attempt']}"
            tab.scene.set_status(block, "running", text)
        elif kind == "log":
            self._log(tab, e)
        elif kind == "block_finished":
            self._apply_block_result(tab, block, e)
        elif kind == "run_finished":
            self._log(tab, {"ts": e["ts"], "level": "DONE" if e["status"] == "success" else "ERROR",
                            "message": f"run #{e['run_id']} {e['status']}"})
        self.flows_panel.update_tab(tab)

    def _apply_block_result(self, tab: FlowTab, block: str, info: dict):
        status = info.get("status")
        tab.block_info[block] = {"status": status, "tables": info.get("tables") or {}}
        attempts = info.get("attempts") or 0
        if status == "success":
            text = "✓ " + _short(info.get("summary") or "done", 48) + (f" · retried {attempts - 1}×" if attempts > 1 else "")
        elif status == "failed":
            text = "✕ " + _short(info.get("error") or "failed", 48)
        else:
            text = status
        tab.scene.set_status(block, status, text)
        if tab is self.current and block in tab.scene.selected_block_ids():
            self.preview.show_tables(block, tab.block_info[block]["tables"])

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
            self.start_run(tab, tab.queued.pop(0))
        self.flows_panel.update_tab(tab)

    # --- history --------------------------------------------------------------------

    def _history(self) -> History:
        return History(self.home.history_db)

    def _load_last_run(self, tab: FlowTab):
        if tab.doc.path is None or not self.home.history_db.exists():
            return
        history = self._history()
        runs = [r for r in history.runs(str(tab.doc.path), limit=5) if r["status"] != "running"]
        history.close()
        tab.last = runs[0] if runs else None
        self.flows_panel.update_tab(tab)

    def _refresh_statuses(self):
        for tab in self.tabs_list():
            self._refresh_schedule(tab)
            if not tab.running:
                self._load_last_run(tab)
        self._show_scheduler_state(self.scheduler_running())

    # --- scheduling -----------------------------------------------------------------

    def _refresh_schedule(self, tab: FlowTab):
        try:
            tab.scheduled = tab.doc.path is not None and tab.doc.path in self.home.scheduled_flows()
        except ValueError:
            tab.scheduled = False
        times = []
        if tab.scheduled:
            now = dt.datetime.now()
            for data in tab.doc.data.get("triggers") or []:
                try:
                    times.append(Trigger(data).next_after(now))
                except ValueError:
                    pass
        tab.next_run = min((t for t in times if t), default=None)
        self.flows_panel.update_tab(tab)

    def set_scheduled(self, tab: FlowTab, enabled: bool):
        if enabled and (tab.doc.dirty or tab.doc.path is None) and not self.save(tab):
            return
        if enabled and not tab.doc.data.get("triggers"):
            QMessageBox.information(self, "No schedule yet", "Add a schedule in the Flow tab first.")
            self.flow_settings.refresh()
            return
        self.home.set_scheduled(tab.doc.path, enabled)
        self._refresh_schedule(tab)
        if tab is self.current:
            self.flow_settings.refresh()
        if enabled and not self.scheduler_running():
            self.statusBar().showMessage("Scheduled. Start the scheduler (button at the top right) so it runs.", 8000)

    def scheduler_running(self) -> bool:
        """The scheduler holds its lock file while it runs."""
        try:
            acquire_lock(self.home).close()  # we got it, so nobody holds it
            return False
        except RuntimeError:
            return True

    def _show_scheduler_state(self, running: bool):
        self.scheduler_action.setIcon(scheduler_icon(running))
        self.scheduler_action.setText("Scheduler on" if running else "Scheduler off")
        self.scheduler_action.setToolTip(
            "The scheduler is running scheduled flows. Click to stop it." if running else
            "Scheduled flows only run while the scheduler is on. Click to start it.")

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
        QTimer.singleShot(1500, lambda: self._show_scheduler_state(self.scheduler_running()))
        self.statusBar().showMessage("Scheduler stopped", 5000)

    def start_scheduler(self):
        if self.scheduler_running():
            self.statusBar().showMessage("The scheduler is already running", 5000)
            return
        subprocess.Popen(
            runner_command() + ["scheduler"], env={**os.environ, "TASKLOOM_HOME": str(self.home.root)},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=sys.platform != "win32",
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.statusBar().showMessage("Scheduler started", 5000)
        QTimer.singleShot(2000, lambda: self._show_scheduler_state(self.scheduler_running()))

    def _refresh_history(self, tab: FlowTab):
        if tab.doc.path is None or not self.home.history_db.exists():
            self.history_panel.show_runs([])
            return
        history = self._history()
        self.history_panel.show_runs(history.runs(str(tab.doc.path)))
        history.close()

    def _open_last_run(self, tab: FlowTab):
        self.tabs.setCurrentWidget(tab)
        if tab.last:
            self.show_history_run(tab.last["id"])

    def show_history_run(self, run_id: int):
        tab = self.current
        if tab is None:
            return
        history = self._history()
        blocks, logs = history.blocks(run_id), history.logs(run_id)
        history.close()
        self._show_run(tab, None)
        for block, info in blocks.items():
            self._apply_block_result(tab, block, info)
        tab.entries = logs
        self.log.set_entries(list(logs))
        self.statusBar().showMessage(f"Showing run #{run_id}", 5000)

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
        for tab in self.tabs_list():
            if not self.close_tab(tab):
                event.ignore()
                return
        event.accept()


def main(paths=()) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Taskloom")
    app.setOrganizationName("Taskloom")
    window = MainWindow()
    opened = [window.open_flow(Path(p)) for p in paths]
    if not any(opened):
        window.new_flow()
    window.show()
    if os.environ.get("TASKLOOM_EDITOR_SMOKE") and opened and opened[0]:
        _smoke_test(app, window, opened[0])  # used by packaging/smoke.py to check a packaged build
    return app.exec()


def _smoke_test(app, window: MainWindow, tab: FlowTab):
    """Run the flow from the editor (through the runner program) and quit with 0 if it succeeded."""
    run = window.start_run(tab, {})
    run.finished.connect(lambda status: app.exit(0 if status == "success" else 1))
    QTimer.singleShot(120_000, lambda: app.exit(2))
