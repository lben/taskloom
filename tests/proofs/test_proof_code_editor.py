"""Proof: the Python and SQL code boxes edit like a code editor: colours, line numbers, the indentation kept on Enter
(one level deeper after a Python ':'), spaces on Tab, and long lines unwrapped with scroll bars unless you tick Wrap
lines (remembered); the other code fields keep the plain box."""

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QStyle, QStyleOptionButton, QToolBar

from proofs.conftest import menu_item
from taskloom.editor.canvas import HEADER_H, NODE_W
from tests.test_code_editor import code_box, color, colors, line_numbers, settle, visual_lines, wrap_box, wrap_boxes

ASKED = ("Add code editing support on python and SQL blocks, it should feel like I am using JetBrains products on "
         "those fields, not just plain text areas but at least with formatting help (remember how many spaces/tabs "
         "deep I am when I hit enter), color coding, wrap/unwrap text (unwrap by default), and vertical/horizontal "
         "scroll bars")
COLUMNS = ["region", "store", "city", "country", "day", "week", "month", "orders", "amount", "discount", "returns",
           "net_amount", "currency", "channel", "manager", "target", "margin", "cost", "tax", "shipping", "items",
           "customers", "visits", "rating"]
FLOW = """\
name: code-boxes
blocks:
  start:
    type: trigger.manual
    ui: {x: 0, y: 0}
  average_sale:
    type: logic.python
    config:
      inputs: []
      code: |-
        sales = {"north": 1200, "south": 800}
        stores = {"north": 4, "south": 0}
        average = {r: sales[r] / stores[r] for r in sales}
        output = average
    ui: {x: 280, y: 0}
  above_target:
    type: logic.python
    config:
      inputs: []
      code: |-
        # Regions over the target
        import math

        TARGET = 1500  # in thousands
        def above(rows, label="north"):
            \"\"\"Rows over the target.\"\"\"
            return [math.ceil(r) for r in rows
                    if r > TARGET * 1.1]

        output = above([1200, 1650.5])
    ui: {x: 0, y: 170}
  area:
    type: logic.python
    config: {inputs: [], code: "import math"}
    ui: {x: 280, y: 170}
  columns:
    type: logic.python
    config:
      inputs: []
      code: |-
        # The columns to keep, and their names in the report
        KEEP
        names = {
NAMES
        }
        output = {"keep": keep, "names": names}
    ui: {x: 560, y: 170}
  regions:
    type: data.duckdb_sql
    config:
      inputs: []
      sql: |-
        -- Sales by region, this year only
        select region, sum(amount) as total, count(*) as orders, round(avg(amount), 2) as average_amount
        from read_csv('sales.csv')
        where year = 2026 and region <> 'test'
        group by region
        order by total desc
    ui: {x: 0, y: 340}
  check:
    type: logic.if
    config:
      condition: len(value) > 0 and value["north"] > 1000 and value["south"] > 1000 and is_business_day(today())
    ui: {x: 280, y: 340}
edges:
  - start.start -> average_sale.after
""".replace("KEEP", "keep = [" + ", ".join(f'"{c}"' for c in COLUMNS) + "]").replace(
    "NAMES", "\n".join(f'            "{c}": "{c.replace("_", " ").title()}",' for c in COLUMNS))
# The colours of keywords, strings, comments, numbers and the rest, in the light and the dark theme, by name.
COLOUR_NAMES = {"#0033b3": "dark blue", "#067d17": "green", "#808080": "grey", "#1750eb": "bright blue",
                "#000000": "black", "#cf8e6d": "orange", "#6aab73": "green", "#7a7e85": "grey", "#2aacb8": "teal"}


def click_block(tab, block: str):
    """Click a block's title bar, as you would."""
    at = tab.view.mapFromScene(tab.scene.nodes[block].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2)))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    settle()


def tick(box):
    """Click a tick box on its little square, as you would."""
    option = QStyleOptionButton()
    option.initFrom(box)
    option.text = box.text()
    QTest.mouseClick(box, Qt.LeftButton, Qt.NoModifier,
                     box.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, box).center())
    settle()


def type_in(box, *typed):
    """Type into the box, key by key: a str is typed as text, a Qt key is pressed."""
    for k in typed:
        if isinstance(k, str):
            QTest.keyClicks(box, k)
        else:
            QTest.keyClick(box, k)
    settle()


def before_cursor(box) -> str:
    """What the line the cursor is on holds before it."""
    cursor = box.textCursor()
    return cursor.block().text()[:cursor.positionInBlock()]


def place(box, line: int, column: int | None = None) -> QRect:
    """Where the cursor would be on `line` (from 1) at `column` (None: its end), in the box's text area."""
    block = box.document().findBlockByNumber(line - 1)
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + (len(block.text()) if column is None else column))
    return box.cursorRect(cursor)


def rows(win, box, first: int, last: int) -> QRect:
    """Lines first to last (from 1) across the box, with their numbers in the margin, in a screenshot of the window."""
    view = box.viewport()
    rect = QRect(QPoint(0, place(box, first, 0).top()), QPoint(view.width() - 1, place(box, last).bottom()))
    rect.translate(view.mapTo(win, QPoint(0, 0)))
    rect.setLeft(box.mapTo(win, box.contentsRect().topLeft()).x())
    return rect


def named(colour: str) -> str:
    return COLOUR_NAMES.get(colour, colour)


def dots(text: str) -> str:
    """The text with each space shown as · and each tab character as →, as code editors show them when asked."""
    return text.replace(" ", "·").replace("\t", "→")


def test_python_and_sql_code_boxes_edit_like_a_code_editor(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("The Python and SQL code boxes edit like a code editor", ASKED)
    p.idea("The Code box of Python Code, Polars Transform, DuckDB SQL and Run Query blocks (and the Python or SQL "
           "code of a block of your own) now works like a small code editor: it colours your code, numbers its lines, "
           "keeps your indentation as you type, and leaves long lines unwrapped unless you ask.")
    p.idea("Enter starts the new line as deep as the line you were on, with the same spaces or tabs. After a Python "
           "line that ends with “:” (as def, if, for and with lines do) it goes one level, 4 spaces, deeper. Tab puts "
           "in spaces up to the next multiple of 4, never a tab character; with several lines selected, Tab and "
           "Shift+Tab move them all in or out by 4 spaces. Backspace right after indentation goes back a whole level.")
    p.idea("Keywords, strings, comments and numbers each have their own colour. The colours are only on the screen: "
           "the flow file keeps your code as plain text, exactly as you typed it.")
    p.idea("Each line's number is in a margin on the left, counted from 1, as a Python Code block's error message "
           "counts them, e.g. “(line 3)”. A long line stays one line, with a horizontal scroll bar; long code gets a "
           "vertical one. Tick Wrap lines, under the box, to wrap long lines instead: the choice is for every Python "
           "and SQL box, and the editor remembers it, also when you start it again.")
    p.idea("The other code fields (If's condition, Inspect's message, HTML Template's template) keep the plain box.")
    p.why_simplest("It is the box the code was already in, made smarter. Qt, the toolkit the editor is built with, "
                   "already gives that box scroll bars, wrapping on or off, and a way to colour each line as it "
                   "changes. Taskloom adds only what Qt leaves out: what Enter, Tab, Shift+Tab and Backspace do with "
                   "indentation, a margin that shows the line numbers, and one short list per language of what a "
                   "keyword, a string, a comment and a number look like.")
    p.why_simplest("Everything the box did before works as before: your code is saved to the block when you leave the "
                   "box, as one step that Undo takes back, and Copy, Paste and Undo inside the box act on its text.")
    p.why_simplest("What it does not do: there is no auto-completion, no error checking while you type, and no "
                   "auto-closing of brackets and quotes. You did not ask for them, and each would need much more: "
                   "knowing every name your code can use, checking your code in the background as you type, and "
                   "rules for when a closing bracket is wanted and when it is in the way. When code fails, its error "
                   "names the line, and the margin's numbers take you to it.")
    p.alternative("A ready-made code editor: QScintilla, or the Monaco editor of VS Code shown in a web view",
                  "QScintilla is made for PyQt, not for PySide6, the Qt that Taskloom uses. Monaco runs only inside a "
                  "web browser engine: the editor would have to carry Qt WebEngine (well over a hundred megabytes in "
                  "the packaged app) and a bridge to pass your code back and forth. Either would be a large new part "
                  "to do what a few short pieces of code do here.")
    p.alternative("Colour the code with Pygments, which Taskloom already has (the library that lays out error "
                  "reports uses it)",
                  "Pygments knows hundreds of languages, but it reads a whole text from its start, while the box "
                  "recolours only the line you change (and the next ones, while a string or comment over several "
                  "lines goes on). Using it would mean recolouring all of the code on each key you press, or more "
                  "code to join the two ways, for the four kinds of words you asked to see in two languages.")
    p.alternative("Save Wrap lines in the flow file, for each block",
                  "Whether long lines wrap is how you like to read code, not part of the flow: in the flow file, it "
                  "would change the file just for how it looks to you. One choice for every box, kept with the "
                  "editor's preferences, is what code editors do.")
    p.alternative("Make every code field a code editor, also If's condition, Inspect's message and HTML Template's "
                  "template",
                  "Those are not Python or SQL: a one-line expression, a message with {placeholders}, an HTML page. "
                  "Each would need its own rules for colours and indentation, and you asked for Python and SQL.")
    p.fits("The margin counts lines as error messages do: a Python Code block that fails says “(line 3)”, and line "
           "3 is the one numbered 3 in the margin. This proof shows it.")
    p.fits("The colours follow the editor's theme, light or dark, read as the canvas reads it, in colours like those "
           "of JetBrains' own light and dark themes.")
    p.fits("Wrap lines is kept with the editor's other preferences, such as Run now's “Don't ask me again”, in your "
           "Taskloom folder: the same in every flow, and after you start the editor again. The flow file does not "
           "change: the same code, saved as before.")
    flow = tmp_path / "code-boxes.yaml"
    flow.write_text(FLOW, encoding="utf-8")

    # --- colours and line numbers ---------------------------------------------------------------------------------
    p.step("Open code-boxes.yaml (File > Open…) and click the Python Code block above_target.")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(flow), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    menu_item(file_menu, "Open…").trigger()
    tab = win.current
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    click_block(tab, "above_target")
    box = code_box(win)
    kinds = {"keyword": color(box, "def above", "def"), "string": color(box, '"north"'),
             "comment": color(box, "# in thousands"), "number": color(box, "1500"),
             "rest": color(box, "TARGET = 1500", "TARGET")}
    p.shot(win, f"① above_target's Code box. The margin on its left numbers each line, from 1. Keywords (import, "
                f"def, return, for, in, if) are {named(kinds['keyword'])}, strings (\"north\" and the description "
                f"under def) {named(kinds['string'])}, comments (from # on) {named(kinds['comment'])}, numbers (1500, "
                f"1.1, 1200, 1650.5) {named(kinds['number'])}, and the rest, such as TARGET and rows, "
                f"{named(kinds['rest'])}.",
           highlight=[box], badges=True, zoom=[box])
    p.check("① Keywords, strings, comments and numbers each have their own colour, and the rest another",
            len(set(kinds.values())) == 5)
    p.check("① Every keyword is in the keyword colour: import, def, return, for, in, if",
            all(color(box, context, word) == kinds["keyword"]
                for context, word in [("import math", "import"), ("    return", "return"), ("for r in", "for"),
                                      ("r in rows", "in"), ("if r >", "if")]))
    p.check("① Every string, comment and number is in its colour: the description under def, the first line's "
            "comment, 1.1, 1200 and 1650.5",
            color(box, '"""Rows over the target."""') == kinds["string"]
            and color(box, "# Regions over the target") == kinds["comment"]
            and {color(box, n) for n in ("1.1", "1200", "1650.5")} == {kinds["number"]})
    p.check("① The margin shows each line's number on its row: 1 beside the first comment, 5 beside def, 10 beside "
            "output",
            [list(line_numbers(box, start).values()) for start in ("# Regions", "def", "output")]
            == [[["1"]], [["5"]], [["10"]]])

    # --- typing ---------------------------------------------------------------------------------------------------
    p.step("Click the Python Code block area, click just after import math in its Code box, and type, key by key: "
           "Enter, def area(r):, Enter, if r < 0:, Enter, raise ValueError(r).")
    click_block(tab, "area")
    box = code_box(win)
    qtbot.waitExposed(win)
    win.activateWindow()
    QTest.mouseClick(box.viewport(), Qt.LeftButton, Qt.NoModifier, place(box, 1).center() + QPoint(30, 0))
    qtbot.waitUntil(box.hasFocus, timeout=5_000)
    after = {}  # what the line held before the cursor, after each key that changes the indentation
    type_in(box, Qt.Key_Return)
    after["Enter after import math"] = before_cursor(box)
    type_in(box, "def area(r):", Qt.Key_Return)
    after["Enter after def area(r):"] = before_cursor(box)
    type_in(box, "if r < 0:", Qt.Key_Return)
    after["Enter after if r < 0:"] = before_cursor(box)
    type_in(box, "raise ValueError(r)")
    p.shot(win, "You typed no spaces. ① Enter after def area(r): started the new line 4 spaces in, so if r < 0: is "
                "one level deeper. ② Enter after if r < 0: started the next one 8 spaces in, so raise ValueError(r) "
                "is one level deeper again.",
           highlight=[(box, "if r < 0:"), (box, "raise ValueError(r)")], badges=True, zoom=[rows(win, box, 1, 4)])
    p.check("Enter after import math (not indented) starts the new line with no spaces",
            after["Enter after import math"] == "")
    p.check("① Enter after def area(r): starts the new line 4 spaces in: one level deeper",
            after["Enter after def area(r):"] == " " * 4)
    p.check("② Enter after if r < 0: (4 spaces in) starts the new line 8 spaces in: one level deeper again",
            after["Enter after if r < 0:"] == " " * 8)

    p.step("Press Enter, then Backspace; type squared = r ** 2, press Enter; type return math.pi * squared, press "
           "Enter, then Backspace; type output = area(2), press Tab, and type # about 12.57.")
    type_in(box, Qt.Key_Return)
    after["Enter after raise ValueError(r)"] = before_cursor(box)
    type_in(box, Qt.Key_Backspace)
    after["Backspace"] = before_cursor(box)
    type_in(box, "squared = r ** 2", Qt.Key_Return)
    after["Enter after squared = r ** 2"] = before_cursor(box)
    type_in(box, "return math.pi * squared", Qt.Key_Return)
    after["Enter after return"] = before_cursor(box)
    type_in(box, Qt.Key_Backspace)
    after["Backspace again"] = before_cursor(box)
    type_in(box, "output = area(2)", Qt.Key_Tab)
    after["Tab after output = area(2)"] = before_cursor(box)[len("output = area(2)"):]
    type_in(box, "# about 12.57")
    typed = box.toPlainText()
    p.shot(win, "Enter after raise ValueError(r) kept its 8 spaces, and one Backspace took the new line back a level, "
                "to 4: squared = r ** 2 is in the function, not in the if. ① Enter after squared = r ** 2 kept its 4 "
                "spaces: return starts 4 spaces in, as deep as the line above it, and you pressed no key but Enter. "
                "② Enter after return kept 4 spaces too, and one Backspace took output back to the left edge; Tab "
                "after output = area(2) put in 4 spaces before the comment, up to column 20.",
           highlight=[(box, "return math.pi"), (box, "output = area(2)")], badges=True, zoom=[rows(win, box, 1, 7)])
    p.check("Enter after raise ValueError(r) (8 spaces in) keeps its 8 spaces",
            after["Enter after raise ValueError(r)"] == " " * 8)
    p.check("Backspace there goes back one level: 4 spaces", after["Backspace"] == " " * 4)
    p.check("① Enter after squared = r ** 2 (4 spaces in) keeps its 4 spaces: return starts there",
            after["Enter after squared = r ** 2"] == " " * 4)
    p.check("② Enter after return (4 spaces in) keeps its 4 spaces, and Backspace goes back to the left edge",
            after["Enter after return"] == " " * 4 and after["Backspace again"] == "")
    p.check("② Tab put in spaces, not a tab character, up to the next multiple of 4 (column 20)",
            after["Tab after output = area(2)"] == " " * 4 and "\t" not in typed)
    p.text("The code box's text, with each space shown as · (a tab character would show as →: there is none)",
           dots(typed))
    p.check("The code is exactly as shown: each line as deep as it should be",
            typed == "import math\n"
                     "def area(r):\n"
                     "    if r < 0:\n"
                     "        raise ValueError(r)\n"
                     "    squared = r ** 2\n"
                     "    return math.pi * squared\n"
                     "output = area(2)    # about 12.57")
    p.note("Typing ( or a quote adds nothing after it, and nothing pops up to complete a name or to point at a "
           "mistake as you type: Taskloom does not do these (see why at the start).")

    # --- long lines, scroll bars and Wrap lines -------------------------------------------------------------------
    lines = tab.doc.blocks["columns"]["config"]["code"].count("\n") + 1
    p.step(f"Click the Python Code block columns. Its line 2, keep = […], is long, and it has {lines} lines.")
    click_block(tab, "columns")
    p.check("Leaving area's box saved its code to the block, exactly as typed",
            tab.doc.blocks["area"]["config"]["code"] == typed)
    box = code_box(win)
    wrap = wrap_box(win)
    qtbot.waitUntil(lambda: box.horizontalScrollBar().isVisible(), timeout=5_000)
    p.shot(win, "Line 2 does not wrap: it goes on past the box's right edge, so ① a horizontal scroll bar shows. The "
                "code is longer than the box, so ② a vertical scroll bar shows too. ③ Wrap lines, under the box, is "
                "not ticked: every code box starts unwrapped.",
           highlight=[box.horizontalScrollBar(), box.verticalScrollBar(), wrap], badges=True, zoom=[box, wrap])
    p.check("Line 2 stays one line on the screen", visual_lines(box, 1) == 1)
    p.check("① A horizontal scroll bar shows, and ② a vertical one",
            box.horizontalScrollBar().isVisible() and box.verticalScrollBar().isVisible())
    p.check("③ Wrap lines is not ticked", not wrap.isChecked())

    p.step("Tick Wrap lines.")
    code = tab.doc.blocks["columns"]["config"]["code"]
    tick(wrap)
    qtbot.waitUntil(lambda: not box.horizontalScrollBar().isVisible(), timeout=5_000)
    p.shot(win, f"① Wrap lines is ticked. Line 2 now wraps onto {visual_lines(box, 1)} rows (the margin numbers it "
                f"once, on its first row), and the horizontal scroll bar is gone.",
           highlight=[wrap], badges=True, zoom=[box, wrap])
    p.check("① Wrap lines is ticked", wrap.isChecked())
    p.check("Line 2 wraps onto several rows, and the horizontal scroll bar is gone",
            visual_lines(box, 1) > 1 and not box.horizontalScrollBar().isVisible())
    p.check("The code itself did not change: wrapping is only on the screen",
            box.toPlainText() == code and tab.doc.blocks["columns"]["config"]["code"] == code)

    p.step("Click the DuckDB SQL block regions.")
    click_block(tab, "regions")
    box = code_box(win)
    wrap = wrap_box(win)
    sql = {"keyword": color(box, "select region", "select"), "string": color(box, "'test'"),
           "comment": color(box, "-- Sales by region, this year only"), "number": color(box, "2026"),
           "rest": color(box, "select region", "region")}
    p.shot(win, f"① Its SQL box is coloured too: SQL keywords (select, as, from, where, and, group by, order by, "
                f"desc) {named(sql['keyword'])}, strings ('sales.csv', 'test') {named(sql['string'])}, the -- comment "
                f"{named(sql['comment'])}, numbers (2, 2026) {named(sql['number'])}. ② Wrap lines is still ticked: "
                f"the choice is for every Python and SQL box, so its long line 2 wraps too.",
           highlight=[box, wrap], badges=True, zoom=[box, wrap])
    p.check("① SQL keywords, strings, comments and numbers each have their own colour, and the rest another",
            len(set(sql.values())) == 5)
    p.check("① Every SQL keyword is in the keyword colour: as, from, where, and, group by, order by, desc",
            {color(box, context, word) for context, word in
             [("as total", "as"), ("from read_csv", "from"), ("where year", "where"), ("2026 and", "and"),
              ("group by", "group"), ("group by", "by"), ("order by", "order"), ("desc", "desc")]}
            == {sql["keyword"]})
    p.check("② Wrap lines is still ticked here, and the long line 2 wraps", wrap.isChecked() and visual_lines(box, 1) > 1)
    p.note("That the editor still remembers Wrap lines after you start it again is not shown here: it keeps the choice "
           "with its preferences in your Taskloom folder, and Taskloom's own tests start the editor again to check "
           "it.")

    # --- an error names its line ----------------------------------------------------------------------------------
    p.step("Click the Python Code block average_sale, then press ▶ Run now in the toolbar. Its line 3 divides by the "
           "number of stores in the south: 0.")
    click_block(tab, "average_sale")
    QTest.mouseClick(win.findChild(QToolBar, "run-toolbar").widgetForAction(win.run_action), Qt.LeftButton)
    qtbot.waitUntil(lambda: len(tab.runs) == 1 and not tab.running and tab.runs[-1].status is not None,
                    timeout=120_000)
    error = "ZeroDivisionError: division by zero (line 3)"
    qtbot.waitUntil(lambda: error in win.log.text.toPlainText(), timeout=30_000)
    win.log.text.verticalScrollBar().setValue(0)
    said = (win.log.text, error)
    p.shot(win, "① average_sale failed. ② Its error, in the Log under the canvas, says where: line 3.",
           highlight=["average_sale", said], badges=True, zoom=[said])
    p.check("① The run failed at average_sale", tab.runs[-1].status == "failed"
            and tab.scene.nodes["average_sale"].status == "failed")
    p.check("② The Log says ZeroDivisionError: division by zero (line 3)", error in win.log.text.toPlainText())
    box = code_box(win)
    p.shot(win, "In Properties, the margin numbers the lines of average_sale's code: ① line 3 is the one numbered 3, "
                "the line that divides by the stores.",
           highlight=[(box, "average = ")], badges=True, zoom=[rows(win, box, 1, 4)])
    p.check("① The number 3 is in the margin beside the line that divides: average = …",
            list(line_numbers(box, "average").values()) == [["3"]])

    # --- the other code fields ------------------------------------------------------------------------------------
    p.step("Click the If block check.")
    click_block(tab, "check")
    box = code_box(win)
    condition = tab.doc.blocks["check"]["config"]["condition"]
    p.shot(win, "① Its Condition box is the plain box, as before: one colour, no margin of line numbers, and no Wrap "
                "lines under it. Its long condition wraps, as a plain box always has: Wrap lines is only for the Python "
                "and SQL boxes.",
           highlight=[box], badges=True, zoom=[box])
    p.check("① The Condition box shows the condition in one colour, with no line numbers",
            box.toPlainText() == condition and len(colors(box, condition)) == 1
            and all(not shown for shown in line_numbers(box, "len").values()))
    p.check("① It has no Wrap lines, and its long condition wraps", wrap_boxes(win) == [] and visual_lines(box) > 1)
