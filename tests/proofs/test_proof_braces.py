"""Proof: one brace rule, {{name}} (docs/promises/braces.yaml). {{name}} is filled in, in every setting that is
filled; single braces stay as typed; Validate warns about a single-brace {name} that names a parameter, and a {{name}}
that names nothing is an error. How proofs are written: README.md here."""

from pathlib import Path

import yaml
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QMessageBox, QPlainTextEdit

from proofs.test_proof_api_request import (click_bottom_tab, log_text, open_flow, preview_lines, preview_table,
                                           properties_row, scroll_to)
from proofs.test_proof_placeholders import run_now
from proofs.test_proof_run_output import click, type_into
from proofs.test_proof_run_scope import box_text, press
from tests.test_api_request import json_body, request, rows, serving
from tests.test_paths_editor_editing import field, flow_file

# Taken as this file is imported, before the win fixture answers message boxes itself: the box that says what is
# wrong is then shown as in the app.
REAL_WARNING = QMessageBox.warning
REPO = Path(__file__).resolve().parents[2]
PROMISES = yaml.safe_load((REPO / "docs" / "promises" / "braces.yaml").read_text(encoding="utf-8"))
ABOUT = ("the plan's one brace rule for the whole app, Inspect's message too",  # what each of `asked` answered
         "the rows of what must always be true, and the recommended answers",
         "how to write a literal {{")
ASKED = " ".join(f"({a['date']}, about {about}:) “{a['words']}”"
                 for a, about in zip(PROMISES["asked"], ABOUT, strict=True))
REGION = "North"  # the flow parameter region
BODY = '{"region": "{{region}}", "extra": { "a": 1 }}'  # send_order's JSON body, as typed
SENT_BODY = '{"region": "North", "extra": { "a": 1 }}'  # as it must be sent
SQL = "select '{{region}}' as region, 245 as total"
PATH = "sales_{{region}}.csv"
TEMPLATE = "<h2>Sales of {{region}}</h2>Total: 245"
SUBJECT = "Sales of {{region}}"
FIVE = ["sales", "save_sales", "page", "mail", "send_order"]  # badges ① to ⑤
SLIP = "Sales of {region}"  # single braces: a warning (BR-4)
TYPO = "Sales of {{regoin}}"  # names nothing: an error (BR-3)
WARNING = "mail: Subject: {region} is plain text: did you mean {{region}}?"
ERROR = "mail: Subject: {{regoin}} is neither a flow parameter nor an input of this block"


def what_it_got(got) -> tuple[int, dict, bytes]:
    """The web server's answer to a request: what it got, as it got it (its first line, the X-Region header, the
    Content-Type and the body), as text."""
    said = "\n".join([f"{got['method']} {got['path']}", f"X-Region: {got['headers'].get('x-region')}",
                      f"Content-Type: {got['headers'].get('content-type')}", "", got["body"].decode("utf-8")])
    return 200, {"Content-Type": "text/plain; charset=utf-8"}, said.encode("utf-8")


def report_flow(path, base: str):
    """start, then: sales (DuckDB SQL) -> save_sales (Write File); page (HTML Template) -> mail (Send Email);
    send_order (API Request, to the web server at `base`). {{region}} in a setting of each."""
    return flow_file(path, {
        "start": {"type": "trigger.manual", "ui": {"x": 0, "y": 130}},
        "sales": {"type": "data.duckdb_sql", "config": {"inputs": [], "sql": SQL}, "ui": {"x": 240, "y": 0}},
        "save_sales": {"type": "data.write_file", "config": {"path": PATH, "format": "csv"},
                       "ui": {"x": 480, "y": 0}},
        "page": {"type": "reports.html_template", "config": {"template": TEMPLATE}, "ui": {"x": 240, "y": 130}},
        "mail": {"type": "email.send", "config": {"connection": "mailer", "to": ["team@example.com"],
                                                  "subject": SUBJECT}, "ui": {"x": 480, "y": 130}},
        "send_order": request(f"{base}/orders", json_body(BODY), method="POST",
                              headers=rows(("X-Region", "{{region}}")), x=240, y=260),
    }, ["start.start -> sales.after", "sales.result -> save_sales.table", "start.start -> page.after",
        "page.html -> mail.body", "start.start -> send_order.after"],
        params={"region": {"type": "text", "value": REGION}})


def code_box(win, label: str) -> QPlainTextEdit:
    """The text box of a code setting of the selected block (SQL, Template, Content), which Properties shows under the
    setting's name."""
    names = [w for w in win.properties.widget().findChildren(QLabel) if w.text() == label and w.isVisible()]
    assert len(names) == 1, f"Properties shows {len(names)} settings named {label}"
    layout = names[0].parentWidget().layout()
    box = layout.itemAt(layout.indexOf(names[0]) + 1).widget()
    return box if isinstance(box, QPlainTextEdit) else box.findChild(QPlainTextEdit)


def boxed(p, win, monkeypatch, action, caption: str) -> list[str]:
    """Press a toolbar button (Validate, ▶ Run now) that shows the box saying what is wrong; shoot it with `caption`
    and press OK. The lines it says."""
    said = []

    def look(box):
        said.extend(box.text().splitlines())
        p.shot(box, caption, over=win, highlight=[box_text(box)], zoom=[box])
        box.accept()

    with monkeypatch.context() as real:  # the box as the app shows it
        real.setattr(QMessageBox, "warning", REAL_WARNING)
        p.while_open(lambda: press(win, action), look)
    return said


# `smtp` (and with it `env`) before `win`: its Taskloom folder is the editor's, and it is made first.
def test_double_braces_are_filled_in_every_setting_and_single_braces_stay_as_typed(proof, smtp, env, win, qtbot,
                                                                                  tmp_path, monkeypatch):
    p = proof("One brace rule: {{name}} is filled in, single braces stay as typed", ASKED)
    p.idea("Every setting that is filled in follows one rule: {{name}}, with two braces, is a placeholder. It gives "
           "the value of the flow parameter, or of the block's input, of that name; {{name:format}} formats it, such "
           "as {{day:%Y-%m-%d}}. Spaces inside do not matter: {{ name }} is {{name}}.")
    p.idea("Single braces are plain text, sent character for character: a JSON body such as { \"a\": 1 }, a CSS "
           "rule, ${HOME}, a regular expression \\d{4}. You type JSON as it is, with nothing to double.")
    p.idea("Validate (F7) catches the slips. A single-brace {region}, where region is a flow parameter or an input of "
           "the block, gives a warning, did you mean {{region}}?, and the run sends it as typed. A {{regoin}} that "
           "names nothing is an error, at Validate and at the run, naming the block, the setting and the name: it is "
           "never sent as typed, and never emptied.")
    p.idea("{{{{ writes a literal {{, so a Mermaid hexagon A{{text}} is written A{{{{text}}. A backslash is plain "
           "text, so a Windows path C:\\data\\{{day}}.csv works.")
    p.why_simplest("Every setting that is filled in goes through one reader of placeholders just before its block "
                   "runs, and Validate checks them with that same reader. So the rule is written once, and is the same "
                   "in a path, an SQL text, an HTML template, an API body or header and an email subject. A test walks "
                   "every such setting of every block, so a new one cannot be left out.")
    p.why_simplest("Only {{ is special. JSON, CSS and scripts are full of single braces and almost never hold {{, so "
                   "they need no change at all; when one does, {{{{ gives it.")
    p.alternative("Keep single braces, {name}, and double a brace that is meant as a brace",
                  "This is how Taskloom worked before. Every brace of a JSON body, a CSS rule or a regular expression "
                  "had to be doubled, and one forgotten brace broke the request or the email.")
    p.alternative("Fill in a {name} only when name is a parameter or an input",
                  "A text would then change as soon as a parameter of that name is added: a word of a JSON body or a "
                  "CSS rule could be filled in without a word. With {{name}}, what is filled in shows in the text "
                  "itself; a {name} only gets a warning, and the run leaves it as typed.")
    p.alternative("${name}, as shells write it",
                  "Shells and scripts name their own variables so, and those must stay as typed: ${HOME} in a command "
                  "would be filled in. {{name}} is also how Postman writes its variables, so a request copied from an "
                  "API's documentation reads the same here.")
    p.alternative("A backslash for a literal brace, \\{{",
                  "A Windows path such as C:\\data\\{{day}}.csv would then lose its placeholder. You chose {{{{ for a "
                  "literal {{.")
    p.fits("The help of each setting, its examples, the starter texts, the shipped example flows and Help > "
           "Documentation all write {{name}}, and pass Validate with no did you mean.")
    p.fits("Inspect's message is the one place where {{ }} holds a Python expression, such as {{len(value)}}. Import "
           "from cURL keeps a body as the command has it, writing {{{{ for a {{ in it, so the run sends what curl "
           "would.")
    p.fits("Validate's warnings go to the Log, and its errors to the same box that stops a run: both as for every "
           "other problem of a flow.")

    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)  # (the runs the editor starts inherit this process's)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    with serving() as api:
        api.answers["/orders"] = what_it_got
        flow = report_flow(tmp_path / "region_report.yaml", api.base)

        # --- 1. an API Request's JSON body ---------------------------------------------------------------------------
        p.step("Open region_report.yaml (File > Open…). Its flow parameter region is North, and each of five blocks "
               "has {{region}} in a setting of another kind.")
        p.note("The web server and the mail server of this proof are small servers it starts on this computer, at "
               "127.0.0.1: nothing goes to the internet. The web server answers each request with what it got, as it "
               "got it: its first line, its X-Region and Content-Type headers, and its body.")
        tab = open_flow(win, monkeypatch, flow)
        p.text("What is typed in each of the five blocks", "\n".join([
            f"① sales, a DuckDB SQL block, its SQL: {SQL}",
            f"② save_sales, a Write File block, its Path: {PATH}",
            f"③ page, an HTML Template block, its Template: {TEMPLATE}",
            f"④ mail, a Send Email block, its Subject: {SUBJECT}",
            "⑤ send_order, an API Request block, its header X-Region: {{region}}, and its JSON body: " + BODY]))
        p.shot(win, "The five blocks, ① to ⑤. Nothing has run yet.", highlight=FIVE, badges=True, zoom=FIVE)
        p.check("The flow opens, with the parameter region = North",
                tab.doc.path == flow.resolve() and tab.doc.data["params"]["region"]["value"] == REGION)

        p.step("Click send_order, and scroll Properties down to its Headers.")
        click(tab, "send_order")
        scroll_to(win, "Headers")
        content = code_box(win, "Content")
        typed = [properties_row(win, "Headers"), (content, BODY)]
        p.shot(win, "As typed: ① its Headers send X-Region: {{region}}; ② its Body is JSON, and its Content holds "
                    "{{region}} and, after it, { \"a\": 1 } with single braces.",
               highlight=typed, badges=True, zoom=typed)
        p.check("The Content box shows the JSON body as typed: " + BODY, content.toPlainText() == BODY)

        p.step("Press ▶ Run now, and Run in the window that asks for the flow's parameters.")

        def look_at_the_parameters(dialog):
            p.shot(dialog, "The window asks for region: North, as the flow sets it.",
                   highlight=[dialog.edits["region"]])

        first = run_now(p, win, qtbot, tab, look=look_at_the_parameters)
        p.check("The run ended with success, each of the five blocks too",
                tab.runs[-1].status == "success" and all(tab.scene.nodes[b].status == "success" for b in FIVE))
        got = api.got("/orders")
        p.check("The web server got one request: POST /orders", [r["method"] for r in got] == ["POST"])
        sent = got[0]["body"].decode("utf-8")

        p.step("Click send_order, then the Data preview tab under the canvas. It shows send_order's response: what "
               "the web server got.")
        click(tab, "send_order")
        click_bottom_tab(win, win.preview)
        shown = win.preview.text.toPlainText()
        p.shot(win, "① What was sent: the X-Region header is North, and the body has North in place of {{region}}, "
                    "and { \"a\": 1 } with its single braces and its spaces, as typed. ② Properties still shows the "
                    "body as typed, with {{region}}.",
               highlight=[preview_lines(win), (code_box(win, "Content"), BODY)], badges=True,
               zoom=[preview_lines(win)])
        p.text("The body the web server got", sent)
        p.check("The body sent keeps the single braces of { \"a\": 1 } exactly as typed, with their spaces (BR-2)",
                sent.endswith('"extra": { "a": 1 }}'))
        p.check(f"{{{{region}}}} in the body became North: the body sent is {SENT_BODY} (BR-1, BR-2)",
                sent == SENT_BODY)
        p.check("Data preview shows that very body, under the X-Region header North (BR-1)",
                win.preview.ports.currentText() == "response" and shown.endswith("\n\n" + SENT_BODY)
                and "X-Region: North\n" in shown and got[0]["headers"].get("x-region") == REGION)

        # --- 2. the same {{region}} in four more kinds of setting --------------------------------------------------
        p.step("Click sales: Data preview shows the table its SQL gave.")
        click(tab, "sales")
        model = win.preview.view.model()
        p.shot(win, "① The SQL, as typed: select '{{region}}' as region. ② The table it gave: region is North.",
               highlight=[(code_box(win, "SQL"), SQL), preview_table(win)], badges=True, zoom=[preview_table(win)])
        p.check("In an SQL text: the table's region is North (BR-1)",
                model.rowCount() == 1 and model.data(model.index(0, 0)) == REGION)

        p.step("Click save_sales, then the Log tab.")
        click(tab, "save_sales")
        click_bottom_tab(win, win.log)
        wrote = (win.log.text, "wrote 1 rows to sales_North.csv")
        p.shot(win, "① The Path, as typed: sales_{{region}}.csv. ② The Log: it wrote sales_North.csv.",
               highlight=[properties_row(win, "Path"), wrote], badges=True, zoom=[wrote])
        p.check("In a path: the file written is sales_North.csv, next to the flow, and the Log says so (BR-1)",
                (tmp_path / "sales_North.csv").is_file() and "wrote 1 rows to sales_North.csv" in log_text(win))

        p.step("Click page, then the Data preview tab.")
        click(tab, "page")
        click_bottom_tab(win, win.preview)
        html = win.preview.text.toPlainText()
        p.shot(win, "① The Template, as typed: <h2>Sales of {{region}}</h2>. ② The HTML it made: <h2>Sales of "
                    "North</h2>.",
               highlight=[(code_box(win, "Template"), TEMPLATE), preview_lines(win)], badges=True,
               zoom=[preview_lines(win)])
        p.check("In an HTML template: the HTML made has <h2>Sales of North</h2> (BR-1)",
                "<h2>Sales of North</h2>" in html and "{{" not in html)

        p.step("Click mail, then the Log tab.")
        click(tab, "mail")
        click_bottom_tab(win, win.log)
        mailed = (win.log.text, "sent 'Sales of North'")
        p.shot(win, "① The Subject, as typed: Sales of {{region}}. ② The Log: it sent 'Sales of North' to "
                    "team@example.com.",
               highlight=[properties_row(win, "Subject"), mailed], badges=True, zoom=[mailed])
        p.check("In an email subject: the mail server got one email, its subject Sales of North (BR-1)",
                [m["Subject"] for m in smtp.messages] == ["Sales of North"])
        p.check("So {{region}} gave North in all five: an SQL text, a path, an HTML template, an API header and body, "
                "and an email subject (BR-1)",
                (got[0]["headers"].get("x-region"), sent, model.data(model.index(0, 0)))
                == (REGION, SENT_BODY, REGION) and (tmp_path / "sales_North.csv").is_file()
                and "<h2>Sales of North</h2>" in html and smtp.messages[0]["Subject"] == "Sales of North")

        # --- 3. Validate: a single-brace {region}, then a {{regoin}} --------------------------------------------------
        p.step(f"With mail selected, type {SLIP} over its Subject, with single braces, and press Enter. Then press "
               "Validate in the toolbar.")
        type_into(field(win, "Subject"), SLIP)
        press(win, win.validate_action)
        qtbot.waitUntil(lambda: WARNING in log_text(win), timeout=10_000)
        QTest.qWait(200)  # (Properties is built again once the flow is saved)
        warned, found = (win.log.text, WARNING), win.statusBar().currentMessage()
        p.shot(win, f"① The Subject is now {SLIP}. ② No box: the Log warns, “{WARNING}” ③ At the foot of the "
                    f"window: “{found}”.",
               highlight=[properties_row(win, "Subject"), warned, win.statusBar()], badges=True, zoom=[warned])
        p.check(f"The flow saved holds the Subject {SLIP}",
                yaml.safe_load(flow.read_text(encoding="utf-8"))["blocks"]["mail"]["config"]["subject"] == SLIP)
        p.check(f"Validate found no problem (no box, and “{found}”), and the Log warns: “{WARNING}” (BR-4)",
                win.message_boxes == [] and found == "region_report.yaml: no problems found"
                and WARNING in log_text(win))

        p.step("Press ▶ Run now, and Run.")
        second = run_now(p, win, qtbot, tab)
        click(tab, "mail")
        click_bottom_tab(win, win.log)
        plain = (win.log.text, "sent 'Sales of {region}'")
        p.shot(win, "The Log warns again as the run starts, and ① the run sent the Subject as typed, as plain text: "
                    "sent 'Sales of {region}'.",
               highlight=[plain], badges=True, zoom=[(win.log.text, "INFO run #2 "), plain])
        p.check("The run ended with success", second > first and tab.runs[-1].status == "success")
        p.check("The mail server's second email has the subject Sales of {region}, as typed (BR-4, BR-2)",
                [m["Subject"] for m in smtp.messages] == ["Sales of North", SLIP])

        p.step(f"Type {TYPO} over the Subject, a slip of the fingers in region, and press Enter. Then press Validate.")
        type_into(field(win, "Subject"), TYPO)
        # As at your pace: the first Validate's “no problems found” has left the foot of the window (after 5 seconds).
        qtbot.waitUntil(lambda: not win.statusBar().currentMessage(), timeout=10_000)
        said = boxed(p, win, monkeypatch, win.validate_action,
                     f"Validate's box: the flow has a problem, “{ERROR}”: it names the block, the setting and "
                     "{{regoin}}.")
        p.check(f"Validate says: “{ERROR}” (BR-3)", ERROR in said)

        p.step("Press ▶ Run now.")
        before = len(tab.runs)
        said = boxed(p, win, monkeypatch, win.run_action,
                     "The run does not start: the same box, with the same problem.")
        p.check(f"The run is refused with “{ERROR}” (BR-3)", ERROR in said and len(tab.runs) == before)
        p.check("Nothing was sent: the mail server got no third email, and the web server no third request (BR-3)",
                len(smtp.messages) == 2 and len(api.got("/orders")) == 2)
    tab.doc.dirty = False
