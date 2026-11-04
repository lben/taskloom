"""Proof: the Session, a box on the canvas whose API Request blocks share their cookies during a run, like a
requests.Session; the Log names the cookies, never their values; a re-run of a block inside starts with the cookies
the session held just before it. How proofs are written: README.md here."""

import html
import re

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication, QComboBox,
                               QFormLayout, QGroupBox, QLineEdit, QPlainTextEdit, QTextEdit, QToolTip, QWidget)

from proofs.conftest import choose, menu_item, right_click
from proofs.test_proof_block_icons import list_row
from proofs.test_proof_canvas import drag_block, open_flow
from proofs.test_proof_loop_runs import click_at, make_room, run_ended, top_of
from proofs.test_proof_python_reusable_block import item_rect, pan
from proofs.test_proof_result_time import hover as hover_result
from proofs.test_proof_run_output import setting, type_into
from proofs.test_proof_run_scope import press
from proofs.test_proof_scheduler_start import click_empty
from proofs.test_proof_tab_plus import set_theme
from taskloom.editor import theme
from taskloom.editor.canvas import HEADER_H, NODE_W, paint_icon
from taskloom.history import History
from tests.test_api_session import LoginSite, lists_cookies, says_from_run
from tests.test_block_icons import GROUP_COLORS, has_icon, ink, pixels, rows, unlike, unlike_listed
from tests.test_inspect_mermaid import contrast
from tests.test_loop_container import box_texts, drawn, render
from tests.test_paths_editor_editing import clean, drag_over_and_drop, dragged_from_the_tree, flow_file, settle
from tests.test_theme import TEXT, most_common

ASKED = ('"cookies kept between blocks." <-- probably this would be useful, reminds me of ussing Sessions with the '
         'requests package and that is needed sometimes. I think just like you create a darker shadow container for '
         'some groups already we may also create a container for a session? What do you think?')
SERVERS = GROUP_COLORS["Servers"]
SESSION, API = "servers.api_session", "servers.api_request"
NAME = "account"  # the Session, once named
INSIDE = ["login", "me"]
SHOWN = f"session {NAME}'s cookies: sessionid (127.0.0.1)"  # the Log line after each request inside it


class Shop(LoginSite):
    """The web server of this proof, on this computer: POST /login gives a new session (the cookie sessionid); GET /me
    answers 200 with a session it gave and still knows, 401 without one. `calls`: each request as [method, path, the
    session it carried (its number: 1 for the first login's), status, the session it gave]."""

    def __init__(self):
        self.calls = []
        super().__init__()

    def _serve(self, handler):
        self.calls.append([handler.command, handler.path, None, None, None])
        super()._serve(handler)

    def _answer(self, path, cookies):
        sent = cookies.get("sessionid")
        self.calls[-1][2] = self.issued.index(sent) + 1 if sent in self.issued else None
        status, headers, text = super()._answer(path, cookies)
        self.calls[-1][3] = status
        if path == "/login":
            self.calls[-1][4] = len(self.issued)
        return status, headers, text


def account_flow(folder, site):
    """start_by_hand -> login (POST /login) -> me (GET /me) -> me_outside (GET /me), apart, with room for the Session
    in the middle of the canvas."""
    def api(url, x, y, method=None):
        return {"type": API, "config": {"url": url, **({"method": method} if method else {})}, "ui": {"x": x, "y": y}}

    return flow_file(folder / "account.yaml", {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": -520, "y": 40}},
        "login": api(site.url("/login"), -260, -230, "POST"),
        "me": api(site.url("/me"), 120, -230),
        "me_outside": api(site.url("/me"), 360, 340),
    }, ["start_by_hand.start -> login.after", "login.status -> me.after", "me.status -> me_outside.after"])


def regions_flow(folder, site):
    """start_by_hand -> regions (three regions) -> each_region (a Loop); inside it the Session account, with login and
    me inside it: each item logs in, then calls /me, whose response is the item's result."""
    def api(url, x, y, method=None):
        return {"type": API, "config": {"url": url, **({"method": method} if method else {})}, "ui": {"x": x, "y": y},
                "inside": NAME}

    return flow_file(folder / "regions.yaml", {
        "start_by_hand": {"type": "trigger.manual", "ui": {"x": -740, "y": 40}},
        "regions": {"type": "logic.python", "config": {"inputs": [], "code": "output = ['north', 'south', 'east']"},
                    "ui": {"x": -500, "y": 40}},
        "each_region": {"type": "logic.loop", "config": {}, "ui": {"x": -220, "y": -110}},
        NAME: {"type": SESSION, "config": {}, "ui": {"x": -180, "y": -50}, "inside": "each_region"},
        "login": api(site.url("/login"), -120, 30, "POST"),
        "me": api(site.url("/me"), -120 + NODE_W + 80, 30),
    }, ["start_by_hand.start -> regions.after", "regions.output -> each_region.items", "each_region.item -> login.after",
        "login.status -> me.after", "me.response -> each_region.result"])


def cookie_drawing():
    """Tabler's cookie, the Session's icon, white on the Servers colour, as blocks paint their icons."""
    image = QImage(96, 96, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("white"))
    painter = QPainter(image)
    paint_icon(painter, QRectF(0, 0, 96, 96), ("cookie",), QColor(SERVERS))
    painter.end()
    return ink(pixels(image), SERVERS)


def name_and_icon(tab, session: str) -> tuple[bool, bool]:
    """Whether the box shows its name at its top; and whether the icon at its top left is the cookie (less than 0.2
    unlike it, as the Loop's proof tells its icon)."""
    settle()
    rect = tab.scene.nodes[session].drawn_rect()
    named = any(session in t["text"] and t["center"][1] < rect.top() + 40 for t in box_texts(tab, session))
    icon = ink(pixels(render(tab.scene, QRectF(rect.left() - 4, rect.top() - 4, 60, 52), 2.0)), SERVERS)
    return named, has_icon(icon, 2.0) and unlike(icon, cookie_drawing()) < 0.2


def lightness(image: QImage) -> np.ndarray:
    rgb = pixels(image)
    return (rgb.max(axis=2) + rgb.min(axis=2)) / 2


def the_look(tab, session: str) -> list[str]:
    """What does not read well on the Session's box, or is not as it should be: its grey hardly unlike the canvas or
    not grey; no shadow darker than both along its right and bottom edges; its name in a colour that does not stand
    out from what is behind it (4.5 to 1, WCAG's bar for text); a counter, or a progress bar under its top. Measured
    where the canvas draws the box (the tests of this change find the box by its colour, which the connections
    crossing it here would cut in parts); each shadow edge by the middle of its rows, so that a connection or a dot of
    the canvas crossing it does not count."""
    settle()
    r = tab.scene.nodes[session].drawn_rect()
    fill = most_common(render(tab.scene, QRectF(r.left() + 120, r.top() + 4, r.width() - 140, HEADER_H - 8)))
    canvas = most_common(render(tab.scene, QRectF(r.right() + 60, r.top(), 40, r.height())))
    problems = []
    rgb = fill.getRgb()[:3]
    if max(rgb) - min(rgb) > 20:
        problems.append(f"its background {fill.name()} is not grey")
    if abs(fill.lightness() - canvas.lightness()) < 8:
        problems.append(f"its background {fill.name()} hardly differs from the canvas {canvas.name()}")
    right = np.median(lightness(render(tab.scene, QRectF(r.right() + 2, r.top() + 20, 4, r.height() - 30)))
                      .min(axis=1))
    below = np.median(lightness(render(tab.scene, QRectF(r.left() + 20, r.bottom() + 2, r.width() - 30, 4)))
                      .min(axis=0))
    for edge, shade in (("right", right), ("bottom", below)):
        if shade > fill.lightness() - 8 or shade > canvas.lightness() - 4:
            problems.append(f"no darker shadow along its {edge} edge (lightness {shade:.0f}; its background "
                            f"{fill.lightness()}, the canvas {canvas.lightness()})")
    texts = box_texts(tab, session)
    names = [t for t in texts if session in t["text"]]
    if not names:
        problems.append(f"its name is not on it: {[t['text'] for t in texts]}")
    for t in names:
        s = max(t["size"], 6.0)
        cx, cy = t["center"]
        behind = most_common(render(tab.scene, QRectF(cx - 2 * s, cy - 2 * s, 4 * s, 4 * s), 2.0))
        ink_colour, alpha = QColor(t["fill"]), t["opacity"]
        shown = QColor(*(round(alpha * f + (1 - alpha) * b) for f, b in zip(ink_colour.getRgb()[:3],
                                                                           behind.getRgb()[:3])))
        if contrast(shown, behind) < TEXT:
            problems.append(f"{t['text']!r}: {shown.name()} on {behind.name()} ({contrast(shown, behind):.2f}:1)")
    counters = [t["text"] for t in texts if re.search(r"\d+\s*/\s*\d+", t["text"])]
    if counters:
        problems.append(f"it shows a counter: {counters}")
    bar = QRectF(r.left() + 12, r.top() + HEADER_H - 4, r.width() - 24, 14)
    off = np.abs(pixels(render(tab.scene, bar)) - rgb).max(axis=2) > 24
    if (off.sum(axis=1) >= 0.6 * bar.width()).any():
        problems.append("it shows a progress bar")
    return problems


def inside_of(tab, block: str):
    return tab.doc.blocks[block].get("inside")


def holds(tab, session: str, block: str) -> bool:
    return tab.scene.nodes[session].drawn_rect().contains(drawn(tab, block))


def kept(win, run_id: int) -> tuple[dict, list[dict]]:
    """What the run history keeps of a run: each block's result, and the Log lines."""
    history = History(win.home.history_db)
    try:
        return history.blocks(run_id), history.logs(run_id)
    finally:
        history.close()


def said_after(lines: list[dict], block: str) -> list[str]:
    return [line["message"] for line in lines if line["block"] == block]


def server_log(site, since: int, runs: dict) -> str:
    """What the web server received, request by request: runs is {run #: how many requests it sent}."""
    out, k = [], since
    for run_id, count in runs.items():
        out.append(f"run #{run_id}")
        for method, path, carried, status, gave in site.calls[k:k + count]:
            sent = f"sessionid of session {carried}" if carried else "no cookie"
            out.append(f"  {method:<4} {path:<7} {sent:<26} -> {status}" + (f", gives session {gave}" if gave else ""))
        k += count
    return "\n".join(out)


def shown_anywhere(win, tabs, run_ids, values, cards) -> list[str]:
    """Where a cookie's value shows: the Log, the result cards (`cards`: their text), the canvas's results, the run
    history's results and Log lines."""
    found = []
    if any(v in win.log.text.toPlainText() for v in values):
        found.append("the Log")
    if any(v in card for card in cards for v in values):
        found.append("a result card")
    for tab in tabs:
        for block, shown in tab.scene.values.items():
            if any(v in str(shown) for v in values):
                found.append(f"{block}'s result on the canvas")
    for run_id in run_ids:
        blocks, lines = kept(win, run_id)
        for block, row in blocks.items():
            if any(v in str(row) for v in values):
                found.append(f"{block}'s result in run #{run_id}")
        if any(v in str(line["message"]) for line in lines for v in values):
            found.append(f"run #{run_id}'s Log lines")
    return found


INPUTS = (QLineEdit, QComboBox, QAbstractButton, QAbstractSpinBox, QPlainTextEdit, QTextEdit, QAbstractItemView)


def properties_shown(win) -> tuple[list[str], list[str], list[QWidget]]:
    """What Properties shows for the selected block: its settings' names, its boxes' titles (as Retry and timeout),
    and the fields you can type in, tick or choose (one inside another, as a list's own text, counts as that one)."""
    body = win.properties.widget()
    names = [item.widget().text() for form in body.findChildren(QFormLayout) for row in range(form.rowCount())
             if (item := form.itemAt(row, QFormLayout.LabelRole)) and item.widget()]
    boxes = [box.title() for box in body.findChildren(QGroupBox)]
    inputs = [w for w in body.findChildren(QWidget) if isinstance(w, INPUTS) and w.isVisibleTo(body)
              and not isinstance(w.parentWidget(), INPUTS)]
    return names, boxes, inputs


def unhover(tab):
    """Move the mouse off the canvas's blocks: the card goes."""
    QTest.mouseMove(tab.view.viewport(), tab.view.viewport().rect().topLeft())
    QToolTip.hideText()
    QApplication.processEvents()


def test_a_session_shares_cookies_between_the_api_request_blocks_inside_it(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("Session: a box whose API Request blocks share their cookies during a run", ASKED)
    p.idea("A Session is a box on the canvas, drawn as the Loop's, around API Request blocks. Like a requests.Session, "
           "the API Request blocks inside it share one set of cookies during a run: the cookie a login's answer sets "
           "goes with the calls after it.")
    p.idea("Add it from the Blocks list, under Servers (its icon is a cookie), and drag blocks into its box, as for a "
           "Loop. Nothing is connected to the Session itself: the blocks inside are connected to each other and to "
           "blocks outside as anywhere, and other blocks may be inside too (they run as usual). An API Request block "
           "outside any Session keeps no cookies from one block to the next, as before.")
    p.idea("The cookies go where the rules of cookies send them, by their domain and path, as a browser does. After "
           "each request inside the box, the Log lists the cookies the session holds, by name and domain, as "
           "“sessionid (127.0.0.1)”, never their values. The Set-Cookie header of a block's headers output shows as "
           "*** in the Log and in the results, while the blocks after it get its value.")
    p.idea("Every run starts its session with no cookies; in a Loop, every item starts its own. Cookies are not kept "
           "from one run to the next, with one exception: to run a block of a run again. Taskloom keeps, with that "
           "run's other kept results, the cookies the session held just before each API Request inside it. Run only "
           "this block (or Run from this block on) on such a block starts with them, and the Log says which run they "
           "came from: a later call runs again without logging in again.")
    p.idea("If the server has ended that session since, the call fails, saying what the server answered (401 here), "
           "and nothing logs in again on its own: run the flow again from the login.")
    p.idea("A Session shares cookies only. The base URL and the authentication (a password, a token) belong to an API "
           "connection (Tools > Connections), which a block inside a Session uses as one outside does.")
    p.why_simplest("It is the Loop's box, reused as it is: the same dragging in and out, the same box that grows to "
                   "hold its blocks, the same one line in the flow file saying which box a block is inside. What is "
                   "new is small: during a run, the API Request blocks inside one Session are given the same cookie "
                   "jar, Python's own, which follows the domain and path rules of cookies, and send their requests "
                   "through it.")
    p.why_simplest("There is nothing to connect and nothing to set: which calls share cookies is what you see inside "
                   "the box.")
    p.why_simplest("Keeping the cookies to run a block again is done as for a block's outputs: they are kept with the "
                   "run, and carried over to the run that carries on from it.")
    p.alternative("A cookies output and input on each API Request, connected from one call to the next",
                  "Every call would need one more connection in and one out, and a call that sets a cookie of its own "
                  "would have to pass on the old ones with the new. A box says once which calls share, and the "
                  "cookies then follow the rules a browser follows, by themselves.")
    p.alternative("A session name typed in each API Request's settings",
                  "Which calls share cookies would be hidden in each block's Properties, and a typo would make a "
                  "second session without a word. A box shows it at a glance, as you suggested, like the Loop's.")
    p.alternative("Every API Request of the flow sharing one set of cookies",
                  "A call to another service could then send or get cookies it should not, and a flow could not keep "
                  "two logins apart (two users, two sites: two Sessions). Outside a Session a call keeps no cookies, "
                  "as a plain requests.get does.")
    p.alternative("Keeping the cookies from one run to the next",
                  "A session cookie lets whoever holds it act as you, and the next run, maybe days later, would send "
                  "one the server has long ended. Each run starts from nothing and logs in; only running a block of a "
                  "run again uses that run's own cookies.")
    p.alternative("Logging in again by itself when a re-run gets 401",
                  "Taskloom cannot know which block logs in, nor whether running it again is what you want. The call "
                  "fails, saying what the server answered, and you choose: run the flow again from the login.")
    p.alternative("The session kept in the API connection",
                  "A connection is shared by every flow and every run that uses it, while a session belongs to one "
                  "run. The connection keeps what is the same each time: the base URL, the auth, the headers.")
    p.fits("It is drawn and moved as the Loop's box, grey with a darker shadow, its icon and name at its top, in Light "
           "and in Dark. It has no ports, no counter and no progress bar (it does not run), and no Retry or Timeout "
           "(the blocks inside have their own).")
    p.fits("It works with the Loop and the Splitter: a Session inside a Loop gives each item a session of its own; "
           "the items of a Loop inside a Session, and a Splitter's lines inside it, share its cookies, also when they "
           "run at the same time.")
    p.fits("A cookie's value is hidden as a stored secret's is: the Log and the run history never show it. Run only "
           "this block and Run from this block on are the ones Taskloom has; on a block inside a Session they also "
           "start with the cookies of the run they carry on from.")
    p.fits("It is in the Blocks list, Help > Documentation, the hover help and the README, like any block.")

    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))  # (Save in Settings writes Windows' Startup script there)
    app = QApplication.instance()
    as_found = app.style().name(), QApplication.palette(), QToolTip.palette()  # given back at the end
    log = win.log.text
    site = Shop()
    try:
        # --- 1. The box --------------------------------------------------------------------------------------------
        p.step("Open the flow account.yaml (File > Open…). Its Manual Start, start_by_hand, runs login (POST /login), "
               "then me (GET /me), then me_outside (the same GET /me).")
        p.note("The web server they call runs on this computer, started by the proof (never the internet). POST /login "
               "gives a new session, the cookie sessionid; GET /me answers 200 with a session it gave and still knows, "
               "and 401 (Unauthorized) without one.")
        p.note("For this proof, the Blocks list and the panels on the right are made narrower and the Log taller (their "
               "edges dragged), so that the canvas is wider and the Log shows more.")
        make_room(win)
        tab = open_flow(win, monkeypatch, account_flow(tmp_path, site))
        settle()
        p.check("The flow opens", tab is not None and set(tab.doc.blocks) == {"start_by_hand", *INSIDE, "me_outside"})

        p.step("Look at the Blocks list, under Servers.")
        tree = win.palette_panel.tree
        servers = rows(win.palette_panel)["Servers"]
        tree.scrollToItem(servers[-1])
        tree.scrollToItem(servers[0].parent())
        QApplication.processEvents()
        session_row = next((item for item in servers if item.text(0) == "Session"), None)
        p.check("The Blocks list has Session under Servers", session_row is not None)
        p.shot(win, "Under Servers: ① Session, with its icon, a cookie. On the canvas, the flow as it opens: login, me "
                    "and me_outside are in no Session yet.", highlight=[list_row(win, session_row)], badges=True,
               zoom=[item_rect(win, servers[0].parent()), item_rect(win, servers[-1])])
        like = {item.text(0): unlike_listed(item, SERVERS, {"cookie": cookie_drawing()})["cookie"] for item in servers}
        p.check("① Of the Servers blocks' icons in the list, Session's is the one most like Tabler's cookie",
                min(like, key=like.get) == "Session")

        p.step("Drag Session from the Blocks list and drop it in the middle of the canvas, under login and me.")
        dropped = drag_over_and_drop(tab, dragged_from_the_tree(session_row), tab.view.mapFromScene(QPointF(0, 60)))
        sessions = [b for b, spec in tab.doc.blocks.items() if spec["type"] == SESSION]
        p.check("The canvas takes it: one Session, named api_session", dropped and sessions == ["api_session"])

        p.step(f"Click the box's top bar and type {NAME} in its Name, in Properties.")
        click_at(tab, top_of(tab, "api_session"))
        label, name = setting(win, "Name")
        type_into(name, NAME)
        settle()
        p.check(f"The Session is now named {NAME}", NAME in tab.doc.blocks and "api_session" not in tab.doc.blocks)
        p.shot(win, f"The new Session, {NAME} (selected, so outlined in blue): a grey box with a darker shadow, its "
                    "icon and its name at its top.", zoom=[NAME])
        named, its_icon = name_and_icon(tab, NAME)
        p.check("Its name is at its top, and at its top left is the cookie, as in the Blocks list", named and its_icon)
        label, name = setting(win, "Name")
        head = win.properties.widget().layout().itemAt(0).widget()  # the block's type, at the top
        p.shot(win.right_tabs, f"Its Properties: ① its type, Session, and its name, {NAME}, and nothing else. It has no "
                               "settings, and no Retry and timeout box, which other blocks have at the bottom (the "
                               "blocks inside have their own).", highlight=[[head, label, name]], badges=True)
        rows_shown, boxes, fields_shown = properties_shown(win)
        only_name = "<b>Session</b>" in head.text() and (rows_shown, boxes, fields_shown) == (["Name"], [], [name])
        p.check("① Properties shows its type, Session, and its name, and nothing else to set: no other setting, no "
                "Retry and timeout" + ("" if only_name else f" (it shows: {rows_shown}, {boxes}, "
                                                             f"{len(fields_shown)} fields)"), only_name)

        p.step("Drag login into the box, then me, beside it. Then click an empty part of the canvas.")
        r = tab.scene.nodes[NAME].drawn_rect()
        drag_block(tab, "login", QPointF(r.left() + 40, r.top() + 50))
        drag_block(tab, "me", QPointF(r.left() + 40 + NODE_W + 80, r.top() + 50))
        click_empty(tab)
        settle()
        p.shot(win, f"① login and me are inside {NAME}, and the box grew to hold them both. Their connections are "
                    "kept: start_by_hand, outside, to login; login to me; me to me_outside, outside.",
               highlight=[INSIDE], badges=True, zoom=[NAME])
        p.check(f"① login and me are inside {NAME}: the flow, saved, would say so",
                [inside_of(tab, b) for b in INSIDE] == [NAME, NAME])
        p.check("① The box, as the canvas paints it, holds both of them whole",
                all(holds(tab, NAME, b) for b in INSIDE))
        problems = the_look(tab, NAME)
        p.check("The box is grey, unlike the canvas, with a shadow darker than both along its right and bottom edges; "
                "its name reads well (a contrast of at least 4.5 to 1 with what is behind it); it shows no counter and "
                "no progress bar" + (f" (not: {'; '.join(problems)})" if problems else ""), problems == [])

        # --- 2. A run --------------------------------------------------------------------------------------------------
        p.step("Press ▶ Run now.")
        press(win, win.run_action)
        qtbot.waitUntil(lambda: len(tab.runs) == 1, timeout=10_000)
        first = run_ended(qtbot, tab)
        blocks, lines = kept(win, first)
        p.shot(win, f"Run #{first}: ① login and me, inside the Session, succeeded: me got 200, for the cookie login "
                    "got went with it. ② me_outside, the same call outside the Session, failed.",
               highlight=[INSIDE, "me_outside"], badges=True, zoom=[NAME, "me_outside"])
        p.check(f"① login and me show success, from run #{first}",
                all(tab.scene.nodes[b].status == "success" and tab.result_run(b) == first for b in INSIDE))
        p.check("The web server got login's POST /login with no cookie, then me's GET /me with the sessionid login got",
                [c[:4] for c in site.calls[:2]] == [["POST", "/login", None, 200], ["GET", "/me", 1, 200]])

        texts = [(log, f"[{b}] INFO {SHOWN}") for b in INSIDE]
        p.shot(win, "The Log of the run: after login's request and after me's, a line lists the cookies the session "
                    "holds, by name and domain: sessionid (127.0.0.1). No line shows a cookie's value.",
               highlight=texts, zoom=[(log, f"run #{first} · "), *texts])
        p.check("After login's request and after me's, the Log lists the session's cookies by name and domain: "
                f"“{SHOWN}”", all(lists_cookies(m, ("sessionid",), "127.0.0.1") and SHOWN in m
                                  for b in INSIDE for m in [next((m for m in said_after(lines, b) if "cookie" in m), "")]))
        p.check("me_outside, outside the Session, has no such line",
                not [m for m in said_after(lines, "me_outside") if "cookie" in m])

        # --- 3. What shows of the cookie -------------------------------------------------------------------------------
        p.step("Rest the mouse on the line under login: its result.")
        tip = hover_result(qtbot, tab, "login")
        cards = [tip.text()]
        p.shot(tip, "login's result: its response, its status 200 and its headers. The cookie its answer set is in "
                    "Set-Cookie, shown as ***: the blocks after it get its value, the screen never shows it.",
               over=win, zoom=[tip, "login"])
        p.check("login's headers show Set-Cookie as ***", re.search(r"Set-Cookie\W+\*\*\*", html.unescape(tip.text())))
        unhover(tab)

        # --- 4. Outside the Session ------------------------------------------------------------------------------------
        p.step("Drag the canvas with the middle mouse button to bring me_outside to its middle, and rest the mouse on "
               "the line under me_outside: its result.")
        p.note("The card opens to the right of the mouse: the canvas is dragged so that all of it fits in the window.")
        middle = tab.view.mapToScene(tab.view.viewport().rect().center())  # (to drag the canvas back to, after)
        pan(tab, tab.scene.nodes["me_outside"].sceneBoundingRect().center())
        error = (blocks.get("me_outside") or {}).get("error") or ""
        tip = hover_result(qtbot, tab, "me_outside")
        cards.append(tip.text())
        p.shot(tip, "me_outside sent the same GET /me after login, but it is outside the Session: it carried no "
                    "cookie, the server answered 401 Unauthorized, and the block failed, saying so.",
               over=win, zoom=[tip, "me_outside"])
        p.check("me_outside failed, saying the server answered 401",
                (blocks.get("me_outside") or {}).get("status") == "failed" and "401" in error and "401" in tip.text())
        p.check("The web server got its GET /me with no cookie, and answered 401",
                site.calls[2][:4] == ["GET", "/me", None, 401])
        p.check(f"So run #{first} failed, as a run with a failed block does; login and me succeeded in it",
                tab.runs[-1].status == "failed")
        unhover(tab)

        # --- 5. Run only me ----------------------------------------------------------------------------------------
        p.step(f"Drag the canvas back to the box, right-click me and choose “Run only this block, with the same inputs "
               f"(run #{first} …)”.")
        pan(tab, middle)

        def run_only_me(menu):
            p.shot(menu, "The menu of me, with the item to choose.", over=win,
                   highlight=[menu_item(menu, "Run only this block, with the same inputs")], zoom=[menu, "me"])
            choose(menu, "Run only this block, with the same inputs")

        p.while_open(lambda: right_click(tab, "me"), run_only_me)
        qtbot.waitUntil(lambda: len(tab.runs) == 2, timeout=10_000)
        second = run_ended(qtbot, tab)
        blocks, lines = kept(win, second)
        p.shot(win, f"Run #{second}: ① me ran again and succeeded; ② login did not run again: it still shows its "
                    f"result from run #{first}.", highlight=["me", "login"], badges=True, zoom=[NAME])
        p.check(f"① me shows success, from run #{second}",
                tab.scene.nodes["me"].status == "success" and tab.result_run("me") == second)
        p.check(f"② login was not run again: the web server got no /login, and login still shows run #{first}'s result",
                [c[1] for c in site.calls[3:]] == ["/me"] and tab.result_run("login") == first)
        p.check(f"The web server got me's GET /me with the sessionid login got in run #{first}, and answered 200",
                site.calls[3][:4] == ["GET", "/me", 1, 200])
        came = f"session {NAME}'s cookies come from run #{first}"
        p.shot(win, f"The Log of run #{second}: me ran on its own, with the inputs it got in run #{first}; ③ the "
                    f"session's cookies came from run #{first}, as they were just before me there: they were kept "
                    f"with that run's results, for running a block of it again.",
               highlight=[(log, came)], badges=[3],
               zoom=[(log, f"run #{second} · "), (log, came), (log, f"DONE run #{second}")])
        p.check(f"③ The Log says the cookies came from run #{first}",
                any(says_from_run(m, first) for m in said_after(lines, "me")))

        # --- 6. The server ended the session --------------------------------------------------------------------------
        p.step("After the web server has forgotten its sessions, right-click me and choose “Run only this block, with "
               "the same inputs” again.")
        p.note("The proof's web server forgets the sessions it gave, as a server does when a session expires.")
        site.valid.clear()
        p.while_open(lambda: right_click(tab, "me"),
                     lambda menu: choose(menu, "Run only this block, with the same inputs"))
        qtbot.waitUntil(lambda: len(tab.runs) == 3, timeout=10_000)
        third = run_ended(qtbot, tab)
        blocks, lines = kept(win, third)
        error = (blocks.get("me") or {}).get("error") or ""
        p.shot(win, f"Run #{third}: ① me failed; ② login did not run: nothing logged in again.",
               highlight=["me", "login"], badges=True, zoom=[NAME])
        p.check("① me failed, saying the server answered 401", (blocks.get("me") or {}).get("status") == "failed"
                and "401" in error and tab.runs[-1].status == "failed")
        p.check("② The web server got one GET /me, with the session it had ended, and no /login: nothing logged in "
                "again", [c[:4] for c in site.calls[4:]] == [["GET", "/me", 1, 401]] and len(site.issued) == 1)
        came = f"session {NAME}'s cookies come from run #{second}"
        failed = "[me] ERROR me (API Request) failed"
        p.shot(win, f"The Log of run #{third}: ③ me started with the session's cookies from run #{second} (which had "
                    f"them from run #{first}); ④ the server answered 401 Unauthorized, and me failed, saying so.",
               highlight=[(log, came), (log, failed)], badges=[3, 4],
               zoom=[(log, f"run #{third} · "), (log, came), (log, f"run #{third} failed")])
        p.text("What the web server received in these three runs, request by request (the session each carried, by "
               "the number of the login that got it)", server_log(site, 0, {first: 3, second: 1, third: 1}))
        p.check(f"③ The Log says the cookies came from run #{second}",
                any(says_from_run(m, second) for m in said_after(lines, "me")))
        p.check("④ The Log says me failed because the server answered 401", any("401" in m for m in said_after(lines, "me")
                                                                                if "ERROR" in m or "failed" in m))
        p.check("No cookie's value shows anywhere: not in the Log, not in a result card, not in a block's result on the "
                "canvas, not in the run history's results or Log lines",
                shown_anywhere(win, [tab], [first, second, third], site.issued, cards) == [])

        # --- 7. In a Loop ----------------------------------------------------------------------------------------------
        p.step("Open the flow regions.yaml (File > Open…) and press ▶ Run now. Its Loop, each_region, runs three "
               f"regions; inside it, the Session {NAME} holds login and me.")
        before = len(site.calls)
        loop_tab = open_flow(win, monkeypatch, regions_flow(tmp_path, site))
        settle()
        click_empty(loop_tab)
        press(win, win.run_action)
        qtbot.waitUntil(lambda: len(loop_tab.runs) == 1, timeout=10_000)
        fourth = run_ended(qtbot, loop_tab)
        blocks, lines = kept(win, fourth)
        p.shot(win, f"Run #{fourth}: the Loop ran its three items; ① login and me, inside {NAME} inside the Loop, each "
                    "say ✓ 3. The Loop's results are what each item's me got: 'your data', three times.",
               highlight=[INSIDE], badges=True, zoom=["each_region"])
        p.check(f"Run #{fourth} succeeded; ① login and me each show ✓ 3",
                loop_tab.runs[-1].status == "success"
                and [loop_tab.scene.nodes[b].status_text for b in INSIDE] == ["✓ 3", "✓ 3"])
        tagged = [(log, f"[item {k}/3] {SHOWN}") for k in (1, 2, 3)]
        p.shot(win, "The Log: for each item, after its login and after its me, the cookies the session holds, by name "
                    "and domain. Each item's login started a session of its own: what the server received, above, "
                    "shows it.", highlight=tagged, zoom=[(log, f"run #{fourth} · "), *tagged])
        p.text("What the web server received in that run, request by request", server_log(site, before, {fourth: 6}))
        p.check("For each of the three items, the Log lists the session's cookies after login and after me",
                all(sum(f"[item {k}/3]" in m and SHOWN in m for m in said_after(lines, b)) == 1
                    for k in (1, 2, 3) for b in INSIDE))
        calls = [c[:4] for c in site.calls[before:]]
        p.check("Each item's login carried no cookie, and its me carried the session that item's login got: sessions 2, "
                "3 and 4, one per item", calls == [step for k in (2, 3, 4)
                                                   for step in (["POST", "/login", None, 200], ["GET", "/me", k, 200])])

        # --- 8. Dark ---------------------------------------------------------------------------------------------------
        p.step("Click the tab of account.yaml, then choose Tools > Settings…, set Theme to Dark and press Save.")
        bar = win.tabs.tabBar()
        QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, bar.tabRect(win.tabs.indexOf(tab)).center())
        settle()
        set_theme(p, win, "Dark", shoot=True)
        QApplication.processEvents()
        p.check("The editor is dark", QApplication.palette().window().color().lightness() < 128)
        problems = the_look(tab, NAME)
        p.shot(win, "In Dark: the box is a lighter grey than the dark canvas, with a darker shadow, and its name reads "
                    "well.", zoom=[NAME])
        p.check("The box is grey, unlike the canvas, with a shadow darker than both along its right and bottom edges; "
                "its name reads well (a contrast of at least 4.5 to 1 with what is behind it); it shows no counter and "
                "no progress bar" + (f" (not: {'; '.join(problems)})" if problems else ""), problems == [])

        p.step("Set Theme back to System (Tools > Settings…) and press Save.")
        set_theme(p, win, "System", shoot=False)
        p.check("The Theme is System again, and the editor light",
                theme.chosen(win.settings) == "System" and QApplication.palette().window().color().lightness() >= 128)
    finally:  # the web server stopped; the Theme and the application's look as the proof found them; no run left going
        site.close()
        for run_ in [r for t in win.tabs_list() for r in t.runs if r.running]:  # (a proof that failed mid-run)
            run_.proc.kill()
            run_.proc.waitForFinished(5000)
        win.settings.setValue("theme", "System")
        theme.apply("System")
        if app.style().name() != as_found[0]:
            QApplication.setStyle(as_found[0])
        QApplication.setPalette(as_found[1])
        QToolTip.setPalette(as_found[2])
        QApplication.processEvents()
        clean(win)
