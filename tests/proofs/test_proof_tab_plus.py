"""Proof: the + that opens a new flow tab is larger, and easy to hit. How proofs are written: README.md here."""

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QFontMetricsF, QHelpEvent, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QToolButton, QToolTip

from tests.test_editor import write_flow
from tests.test_inspect_mermaid import contrast, near
from tests.test_paths_editor_editing import labelled, press
from tests.test_tab_plus import new_flow_button, the_plus

ASKED = "Editor: The plus sign to create a new tab needs to be larger"
FLOW = """
    blocks:
      start_by_hand:
        type: trigger.manual
        ui: {{x: 0, y: 0}}
      {name}:
        type: logic.python
        config: {{inputs: [], code: "output = 1"}}
        ui: {{x: 260, y: 0}}
    edges:
      - start_by_hand.start -> {name}.after
"""
TOOLTIP = "New flow (File > New flow)"
CLOSE_UP = 640  # how much of the tab row a close-up of its right end shows, in pixels


def in_window(win, widget, rect: QRect | None = None) -> QRect:
    """Where `widget` (or `rect` of it) is in a screenshot of the window."""
    return (rect if rect is not None else widget.rect()).translated(widget.mapTo(win, QPoint(0, 0)))


def flow_tab(win, index: int) -> QRect:
    """The flow tab `index` (from 0), above the canvas, in the window's pixels."""
    bar = win.tabs.tabBar()
    return in_window(win, bar, bar.tabRect(index))


def row_end(win) -> QRect:
    """The right end of the row of tabs, the + button included: for a close-up."""
    bar = win.tabs.tabBar()
    end = (in_window(win, bar, QRect(0, 0, bar.tabRect(bar.count() - 1).right() + 1, bar.height()))
           | in_window(win, new_flow_button(win)))
    end.setLeft(max(end.left(), end.right() - CLOSE_UP))
    return end


def measure(button) -> dict:
    """The + as `button` draws it, measured on its pixels (tests/test_tab_plus.py): its height and width, the
    thickness of its strokes, its colour (ink) and the colour behind it, in the editor's pixels."""
    picture = button.grab()
    return the_plus(picture.toImage(), picture.devicePixelRatio())


def menu_action(win, menu: str, item: str):
    return next(a for a in next(a.menu() for a in win.menuBar().actions() if a.text() == menu).actions()
                if a.text() == item)


def tabs_scroll(bar) -> bool:
    return bar.tabRect(0).left() < 0 or bar.tabRect(bar.count() - 1).right() >= bar.width()


def scroll_arrows(bar) -> list:
    """The arrows that scroll the tabs, as they show in the row."""
    return [b for b in bar.findChildren(QToolButton) if b.isVisible() and b.parent() is bar]


def what_the_plus_is_over(win) -> list[str]:
    """Of the tabs (each with its ×) and the arrows that scroll them, as far as they show in the row, those the +
    button is over; and what is under its middle, if not the + button. Nothing, when it is clear of them all."""
    bar, button = win.tabs.tabBar(), new_flow_button(win)
    place, row = in_window(win, button), in_window(win, bar)
    over = [f"tab {i + 1}" for i in range(bar.count()) if place.intersects(flow_tab(win, i) & row)]
    over += ["an arrow" for arrow in scroll_arrows(bar) if place.intersects(in_window(win, arrow) & row)]
    hit = win.childAt(place.center())
    if not (hit is button or button.isAncestorOf(hit)):
        over.append(f"under its middle, {hit!r}")
    return over


def click(button, at: QPoint | None = None):
    """Move the mouse onto `button` (at its middle, or `at`) and click it."""
    at = at if at is not None else button.rect().center()
    QTest.mouseMove(button, at)
    QTest.mouseClick(button, Qt.LeftButton, Qt.NoModifier, at)
    QApplication.processEvents()


def mouse_away(win):
    """Move the mouse from the + button, where it is now, onto the canvas: the + is no longer hovered (no frame
    around it)."""
    button, viewport = new_flow_button(win), win.current.view.viewport()
    QTest.mouseMove(button, button.rect().center())
    QTest.mouseMove(viewport, viewport.rect().center())
    QApplication.processEvents()
    assert not button.underMouse(), "the mouse is still on the + button"


def set_theme(p, win, theme: str, shoot: bool):
    """Tools > Settings…: Theme `theme`, then Save; the dialog is shot first if `shoot`."""
    def choose_and_save(dialog):
        row = labelled(dialog, "Theme")
        row.setCurrentIndex(row.findText(theme))
        if shoot:
            p.shot(dialog, f"① Theme: {theme}, about to be saved.", highlight=[row], badges=True)
        press(dialog, "Save")

    p.while_open(menu_action(win, "&Tools", "Settings…").trigger, choose_and_save)
    QApplication.processEvents()


def test_the_plus_for_a_new_flow_tab_is_larger_and_easy_to_hit(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("The + that opens a new flow tab is larger, and easy to hit", ASKED)
    p.idea("The + right of the last flow tab is now a large, bold +: twice as tall as the capital letters of the "
           "tabs' names, with strokes 2 pixels thick. It was the + of the text, thin, and smaller than those "
           "capitals.")
    p.idea("Its button is as tall as the row of tabs, and at least 24 pixels wide: a click anywhere on it opens a new "
           "flow, as File > New flow does, and its tooltip says so. It stays where it was: just right of the last "
           "tab, or at the right end of the row when there are more tabs than fit.")
    p.idea("It is drawn in the colour of the tabs' text, read each time it is drawn: dark on a light row, light on a "
           "dark one. So it follows the Theme setting (Tools > Settings).")
    p.why_simplest("Taskloom draws the + itself: two filled bars that cross in the middle. Its size comes from one "
                   "measure, the capital letters of the text, so it grows with the text. The bars are laid on whole "
                   "pixels of the screen, so the + stays sharp at 150% and 200%.")
    p.why_simplest("Nothing else of the tab row changed: it is the same button, in the same place, with the same "
                   "click and the same tooltip as before.")
    p.alternative("A bigger font for the + of the text",
                  "A font's + is smaller than its capital letters, and how big and how thick it is differs from font "
                  "to font (Windows, macOS and Linux each have their own). For a + this tall, the button's text "
                  "would be more than twice the size of the tabs' names, and a button sized for such text is taller "
                  "than the row of tabs.")
    p.alternative("A picture of a +, from a file, like the blocks' icons",
                  "A picture keeps the colours it was made in. The blocks' icons are white drawings on their coloured "
                  "squares, which show on any background; a + stands straight on the tab row, so it would need a "
                  "dark picture and a light one, swapped each time the colours change. Drawn, the + simply takes "
                  "the colour of the moment.")
    p.alternative("A last tab marked +, that opens a new flow when clicked",
                  "Every tab of that row is an open flow: you can move it and close it, and it shows a * for unsaved "
                  "changes and its last run's result. A tab that is not a flow would be an exception to each of "
                  "those.")
    p.fits("It is drawn as the magnifier of the canvas's zoom bar is: in the colour of the text, drawn again when "
           "the colours change. The tab row is made as tall as the + needs, and room is kept at its right end for "
           "the +, as before, so it never covers a tab.")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # Settings' Save writes a script there on Windows
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    flows = [write_flow(tmp_path / f"{name}.yaml", FLOW.format(name=name)) for name in ("load_sales", "ask_region")]
    bar = win.tabs.tabBar()
    try:
        p.step("Open the flows load_sales.yaml and ask_region.yaml (File > Open…): two flow tabs, above the canvas.")
        for flow in flows:
            monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, _f=flow, **k: (str(_f), ""))
            menu_action(win, "&File", "Open…").trigger()
        QApplication.processEvents()
        p.check("The two flows open, each in its tab",
                [t.doc.path for t in win.tabs_list()] == [f.resolve() for f in flows])
        button = new_flow_button(win)
        first, text = measure(button), bar.palette().color(QPalette.WindowText)
        cap = QFontMetricsF(bar.font()).capHeight()  # the tabs' names' capital letters
        before = QFontMetricsF(button.font()).tightBoundingRect("+").height()  # the text + it showed before
        last, place = flow_tab(win, 1), in_window(win, button)
        thick = min(first["upright"], first["crossbar"])
        p.shot(win, "① The + right of the last tab, ask_region.yaml: a large, bold +, on a button as tall as the tabs.",
               highlight=[button], badges=True, zoom=row_end(win))
        p.note("Only this + changed. The toolbar's buttons, and the + and − of the canvas's zoom bar (at the top "
               "right of the canvas, for zooming in and out), are as they were.")
        p.text("The + and the tabs, measured in pixels",
               f"The new +           {first['height']:.0f} tall, {first['width']:.0f} wide, strokes {thick:.0f} thick\n"
               f"The + before        {before:.1f} tall: the + of the text, in the same font, as the font measures it\n"
               f"The tabs' capitals  {cap:.1f} tall\n"
               f"The new +'s button  {button.width()} wide, {button.height()} tall\n"
               f"A tab               {last.height()} tall")
        p.check(f"① The + is just right of the last tab, ask_region.yaml ({place.left() - last.right() - 1} pixels "
                "after it)",
                0 <= place.left() - last.right() - 1 <= 30 and last.top() <= place.center().y() <= last.bottom())
        p.check(f"① Its + is {first['height']:.0f} pixels tall: about twice the tabs' capital letters ({cap:.1f} "
                "pixels), at least 1.9 times", first["height"] >= 1.9 * cap)
        p.check(f"It is {first['height'] / before:.1f} times as tall as the + it replaces, the + of the text "
                f"({before:.1f} pixels): at least twice", first["height"] >= 2 * before)
        p.check(f"It is bold: its strokes are {thick:.0f} pixels thick", thick >= 1.9)
        p.check(f"Its button is as tall as a tab ({button.height()} pixels, a tab {last.height()}), and the + fits "
                "whole in it", button.height() >= last.height() and first["margin"] >= 1)
        p.check(f"Its button is {button.width()} pixels wide: at least about 24", button.width() >= 24)
        p.check(f"It is drawn in the colour of the tabs' text ({text.name()}), on the row's background "
                f"({first['background'].name()})", near(first["ink"], text, 24))

        p.step("Click the + near its lower right corner, away from the + sign itself.")
        corner = QPoint(button.width() - 3, button.height() - 3)  # 2 pixels in from its right and bottom edges
        under = win.childAt(button.mapTo(win, corner))
        click(button, corner)
        tab = win.current
        p.shot(win, "① A new flow opened, in a new tab: untitled, unsaved, with a Manual Start. ② The + moved right "
                    "of it.", highlight=[flow_tab(win, 2), new_flow_button(win)], badges=True, zoom=row_end(win))
        p.check("Under that corner, 2 pixels in from the button's edges, is the + button",
                under is button or button.isAncestorOf(under))
        p.check("① A new flow opened, in a third tab, and it is the one shown",
                win.tabs.count() == 3 and win.tabs.currentIndex() == 2)
        p.check("It is a new flow, as File > New flow makes one: unsaved, with only a Manual Start",
                tab.doc.path is None and [s["type"] for s in tab.doc.blocks.values()] == ["trigger.manual"])
        p.check("② The + is now just right of it",
                0 <= in_window(win, new_flow_button(win)).left() - flow_tab(win, 2).right() - 1 <= 30)

        p.step("Rest the mouse on the +.")
        button = new_flow_button(win)
        middle = button.rect().center()
        QTest.mouseMove(button, middle)
        QApplication.sendEvent(button, QHelpEvent(QEvent.ToolTip, middle, button.mapToGlobal(middle)))
        tips = []

        def tip_shown():
            tips[:] = [w for w in QApplication.topLevelWidgets() if w.inherits("QTipLabel") and w.isVisible()]
            return bool(tips)

        qtbot.waitUntil(tip_shown, timeout=5_000)
        tip = tips[0]
        p.shot(tip, f"① Its tooltip says what it does: “{tip.text()}”.", over=win, highlight=[tip], badges=True,
               zoom=[tip, button])
        p.check(f"① Its tooltip says it opens a new flow, as File > New flow does: “{TOOLTIP}”", tip.text() == TOOLTIP)
        QToolTip.hideText()

        p.step("Click the + until the tabs no longer all fit in their row.")
        clicks = 0
        while not tabs_scroll(bar) and clicks < 40:
            click(new_flow_button(win))
            clicks += 1
        mouse_away(win)
        button = new_flow_button(win)
        p.shot(win, f"After {clicks} more {'click' if clicks == 1 else 'clicks'}, {win.tabs.count()} flows are open, "
                    "more than fit in the row: the first tab is scrolled out of sight on the left, and the small "
                    "arrows just left of the + scroll the tabs. ① The + is at the right end of the row, clear of the "
                    "tabs and of the arrows.", highlight=[button], badges=True, zoom=row_end(win))
        p.check(f"The tabs no longer all fit in their row: {win.tabs.count()} flows are open, and the first tab is "
                "scrolled out of sight on the left, at least in part", tabs_scroll(bar) and bar.tabRect(0).left() < 0)
        p.check("① The + is at the right end of the row, after where the tabs end",
                in_window(win, button).left() > in_window(win, bar).right())
        over, arrows = what_the_plus_is_over(win), scroll_arrows(bar)
        p.check(f"① It is over no tab, nor over a tab's ×, nor over the {len(arrows)} arrows"
                + (f": it is over {over}" if over else ""), arrows and not over)

        p.step("Click the + once more, there.")
        count = win.tabs.count()
        click(button)
        mouse_away(win)
        button = new_flow_button(win)
        p.shot(win, "① The new flow's tab is the last one, and the one shown. ② The + is still at the right end of the "
                    "row.", highlight=[flow_tab(win, count), button], badges=True, zoom=row_end(win))
        p.check("① A new flow opened, in a new tab at the end, the one shown",
                win.tabs.count() == count + 1 and win.tabs.currentIndex() == count and win.current.doc.path is None)
        over = what_the_plus_is_over(win)
        p.check("② The + is still at the right end of the row, over no tab nor arrow"
                + (f": it is over {over}" if over else ""),
                in_window(win, button).left() > in_window(win, bar).right() and not over)

        p.step("Choose Settings… in the Tools menu, set Theme to Dark, and press Save.")
        set_theme(p, win, "Dark", shoot=True)
        mouse_away(win)
        button = new_flow_button(win)
        dark, text = measure(button), bar.palette().color(QPalette.WindowText)
        p.shot(win, "The editor is dark at once. ① The + is light, the colour of the tabs' names, on the dark row.",
               highlight=[button], badges=True, zoom=row_end(win))
        p.check(f"The tab row is dark: the + is drawn on {dark['background'].name()}",
                dark["background"].lightness() < 128)
        p.check(f"① The + is light ({dark['ink'].name()}): the colour of the tabs' text in Dark ({text.name()}), "
                f"where it was {first['ink'].name()} before",
                near(dark["ink"], text, 24) and dark["ink"].lightness() >= 128)
        readable = contrast(dark["ink"], dark["background"])
        p.check(f"① It is easy to read: a contrast of {readable:.1f} to 1 with the row (text needs 4.5 to 1)",
                readable >= 4.5)
        p.check(f"It is as large as before: {dark['height']:.0f} pixels tall, strokes "
                f"{min(dark['upright'], dark['crossbar']):.0f} thick",
                abs(dark["height"] - first["height"]) <= 1 and min(dark["upright"], dark["crossbar"]) >= 1.9)

        p.step("Set Theme back to System (Tools > Settings…), and press Save.")
        set_theme(p, win, "System", shoot=False)
        mouse_away(win)
        button = new_flow_button(win)
        again, text = measure(button), bar.palette().color(QPalette.WindowText)
        p.shot(win, "The editor has its first colours again, and ① the + its first colour: the tabs' text colour.",
               highlight=[button], badges=True, zoom=row_end(win))
        p.check(f"① The + is {again['ink'].name()} on {again['background'].name()} again, as at first: the colour of "
                f"the tabs' text ({text.name()})",
                near(again["ink"], first["ink"], 24) and near(again["background"], first["background"], 24)
                and near(again["ink"], text, 24))
    finally:
        if app.style().name() != as_found[0]:
            QApplication.setStyle(as_found[0])
        QApplication.setPalette(as_found[1])
        QToolTip.setPalette(as_found[2])
        QApplication.processEvents()
