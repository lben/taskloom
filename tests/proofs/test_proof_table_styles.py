"""Proof: a template's own <style> rules win over a table's built-in look, and {name:html} puts text in as HTML. See
README.md here."""

from proofs.test_proof_failure_inputs import finished, open_flow, press_run_now
from proofs.test_proof_html_template_formatting import click_block, email_reader
from tests.test_code_editor import code_box
from tests.test_paths_reports_email import html_of
from tests.test_template_styles import (DARK, HEAD_LOOK, STYLED, background_shown, borders, document, effective,
                                        paddings, styled_table)

ASKED = ("The HTML Template seems to override styles from df.style.apply / set_table_styles … cell padding 15px, "
         "font 15px, 1px black borders")
RULE = "<style>td {{padding: 15px; border: 1px solid black; font-size: 15px}}</style>"
PRICES = 'table = pl.DataFrame({"item": ["pen", "ink", "paper"], "price": [1.2, 4.5, 3.75], "stock": [1200, 80, 15]})'
COLUMNS = ["item", "price", "stock"]
BLACK = [("1px", "solid", "#000000")] * 4


def python(code: str, x: int, y: int, output: str) -> dict:
    return {"type": "logic.python", "config": {"inputs": [], "outputs": [output], "code": code}, "ui": {"x": x, "y": y}}


def template(text: str, x: int, y: int, names: dict | None = None) -> dict:
    return {"type": "reports.html_template", "config": {"template": text, **({"names": names} if names else {})},
            "ui": {"x": x, "y": y}}


def send(subject: str, x: int, y: int) -> dict:
    return {"type": "email.send", "ui": {"x": x, "y": y},
            "config": {"connection": "mailer", "to": ["team@example.com"], "subject": subject}}


PRICES_FLOW = {
    "blocks": {"start": {"type": "trigger.manual", "ui": {"x": 0, "y": 90}},
               "prices": python(PRICES, 250, 90, "table"),
               "report": template(RULE + "\nPrices this week:<br>\n{my_table}", 520, 0, {"prices.table": "my_table"}),
               "send": send("Prices, with my styles", 790, 0),
               "report_plain": template("Prices this week:<br>\n{my_table}", 520, 190, {"prices.table": "my_table"}),
               "send_plain": send("Prices, built-in look", 790, 190)},
    "edges": ["start.start -> prices.after", "prices.table -> report.data", "report.html -> send.body",
              "prices.table -> report_plain.data", "report_plain.html -> send_plain.body"],
}
STYLED_FLOW = {
    "blocks": {"start": {"type": "trigger.manual", "ui": {"x": 0, "y": 0}},
               "styled": python(f"output = '''{STYLED}'''", 250, 0, "output"),
               "report": template("<b>With {{styled:html}}:</b><br>\n{styled:html}\n<br><b>With {{styled}}:</b><br>\n"
                                  "{styled}", 520, 0),
               "send": send("Scores", 790, 0)},
    "edges": ["start.start -> styled.after", "styled.output -> report.data", "report.html -> send.body"],
}


def prices_table(html: str):
    """The table the email shows for {my_table}."""
    [table] = [t for t in document(html).all("table")
               if not t.all("table") and [th.text for th in t.all("th")] == COLUMNS]
    return table


def cell_html(html: str, text: str) -> str:
    """The <td> of the sent HTML that shows `text`, as written."""
    end = html.index(f">{text}</td>")
    return html[html.rindex("<td", 0, end):end + len(text) + 6]


# `smtp` (and with it `env`) before `win`: its Taskloom folder is the editor's, and it is made first.
def test_a_td_rule_in_the_templates_style_changes_the_tables_cells(proof, smtp, env, win, qtbot, monkeypatch):
    p = proof("Your own <style> rules win over a table's built-in look", ASKED)
    p.idea("A rule you write in the template's own <style> now changes how a table looks in the email. With "
           "<style>td {padding: 15px; border: 1px solid black; font-size: 15px}</style> above {my_table}, every cell "
           "gets 15px of padding, a black border and 15px text. You do not need !important.")
    p.idea("What you do not set stays as Taskloom draws it: the header stays dark with white text, the rows stay "
           "striped, numbers stay on the right. Without a <style> of your own, the table looks as it always did.")
    p.idea("Before, your rule lost. Taskloom writes each cell's look into the cell itself, which is the only place "
           "Outlook reads it, and it wrote its own look there first: a rule from <style> could not replace it.")
    p.why_simplest("Only the order changed. Your rules are written into the cells first, then Taskloom's built-in "
                   "look fills in what your rules left open, property by property. The same step that already moved "
                   "styles into the cells does both.")
    p.alternative("Ask you to write !important in your rules",
                  "It worked before for some properties, but it is one more thing to know, and it is not how a "
                  "style sheet normally behaves: your own rule should simply win.")
    p.alternative("Settings on the block for padding, borders and text size",
                  "That is the Table Stylizer block planned for later. A <style> rule is what people who know HTML "
                  "write first, and it already had a place in the template.")
    p.fits("In a template braces are doubled, as everywhere in it: you type td {{ … }}, and the email gets td { … }.")
    p.fits("The email's own frame around your text takes none of your rules: a td rule changes the cells of your "
           "tables, and puts no border around the whole email.")

    assert win.home.root == env.home, "the editor and the test mail server's connection use the same Taskloom folder"
    p.step("Open prices.yaml (File > Open…) and click the HTML Template block report. Its Template starts with the "
           "<style> line, then {my_table}: the table that prices makes. report_plain, below it, has the same "
           "template without the <style> line, to compare.")
    tab = open_flow(win, monkeypatch, env.root / "prices.yaml", PRICES_FLOW)
    click_block(tab, "report")
    box = code_box(win)
    p.shot(win, "① report, the HTML Template block. ② Its Template: the <style> line with the td rule (braces "
                "doubled), then {my_table}.", highlight=["report", box], badges=True, zoom=[box])
    p.check("② The Template holds the td rule and {my_table}",
            box.toPlainText().startswith(RULE) and "{my_table}" in box.toPlainText())

    p.step("Press ▶ Run now. Both emails are sent to team@example.com, through a mail server that this proof runs on "
           "this computer.")
    press_run_now(win)
    finished(qtbot, tab, 1)
    p.check("The run succeeded and the mail server received two emails",
            tab.runs[0].status == "success" and len(smtp.messages) == 2)
    emails = {msg["Subject"]: msg for msg in smtp.messages}
    plain, styled = html_of(emails["Prices, built-in look"]), html_of(emails["Prices, with my styles"])

    p.step("Look at the email from report_plain, without the <style> line: the built-in look.")
    reader, _ = email_reader(qtbot, emails["Prices, built-in look"])
    p.shot(reader, "The table as Taskloom draws it: a dark header with white text, thin grey lines, striped rows, "
                   "numbers on the right, little room around the text.")
    reader.close()
    cells = prices_table(plain).all("td")
    p.check("Each cell has the built-in look: 5px and 10px of padding and a thin grey border",
            len(cells) == 9 and all(paddings(td.style) == ["5px", "10px", "5px", "10px"]
                                    and borders(td.style) == [("1px", "solid", "#d5dce4")] * 4 for td in cells))

    p.step("Look at the email from report, with the <style> line.")
    reader, _ = email_reader(qtbot, emails["Prices, with my styles"])
    p.shot(reader, "The same table with your rule: each cell has 15px of room around its text, a black border and "
                   "bigger text. The header is still dark with white text, the rows are still striped, the numbers "
                   "still on the right. There is no border around the email itself.")
    reader.close()
    p.note("This is not Outlook: the build machine has none. Qt's HTML view draws the email close to how a mail "
           "program shows it. What is checked is the HTML that was sent: each cell has its look in its own style "
           "attribute, where Outlook reads it.")
    p.text("The cell “ink” in the sent HTML: your padding, border and text size, then what is left of the built-in "
           "look", cell_html(styled, "ink"))
    table = prices_table(styled)
    cells = table.all("td")
    p.check("Every cell has 15px of padding on its four sides", len(cells) == 9
            and all(paddings(td.style) == ["15px"] * 4 for td in cells))
    p.check("Every cell has a 1px solid black border on its four sides", all(borders(td.style) == BLACK for td in cells))
    p.check("Every cell has 15px text", all(td.style.get("font-size") == "15px" for td in cells))
    p.check("No !important was needed, and none is in the email", "!important" not in styled.lower())
    p.check("The header is as before: dark with white bold text",
            [(th.text, th.style) for th in table.all("th")] == [(column, effective(HEAD_LOOK)) for column in COLUMNS]
            and effective(HEAD_LOOK)["background-color"] == DARK)
    p.check("The rows are still striped: white, light grey, white",
            [background_shown(row.all("td")[0]) for row in table.all("tr")[1:]] == ["#ffffff", "#f3f6f9", "#ffffff"])
    p.check("Numbers are still on the right, text on the left",
            [td.style.get("text-align") for td in cells] == ["left", "right", "right"] * 3)
    frame = [td for td in document(styled).all("td") if td.all("table")]  # the cell that holds your text
    p.check("The email's own frame got none of the rule: no black border, no 15px padding",
            frame and not any(borders(td.style) == BLACK or paddings(td.style) == ["15px"] * 4 for td in frame))
    for each in win.tabs_list():  # nothing to save when the window closes
        each.doc.dirty = False


def test_name_html_puts_html_text_in_as_it_is(proof, smtp, env, win, qtbot, monkeypatch):
    p = proof("{name:html} puts text in as the HTML it holds", ASKED)
    p.idea("Write {styled:html} in a template, and the text that styled gives is put into the email as HTML, as it "
           "is. So a table you coloured yourself in a Python Code block, with pandas' "
           "df.style.apply(colour, axis=1).to_html(), shows in the email with its colours.")
    p.idea("Plain {styled} still shows the text as text, tags and all: text from a query or a file must never turn "
           "into HTML by accident. Adding :html is how you say this text is HTML.")
    p.idea("pandas puts its colours in a <style> block at the top of that HTML. Outlook ignores <style> blocks, so "
           "Taskloom moves those rules into each cell, as it does with your own.")
    p.why_simplest("A placeholder could already take a format after a colon, such as {total:.1f} or {day:%Y-%m-%d}. "
                   "html is one more word in that place, and the step that moves styles into the cells was already "
                   "there for the template's own <style>.")
    p.alternative("Treat any text that looks like HTML as HTML",
                  "A customer's name or a query's text that happens to hold < and > would change the email. You "
                  "decide, in the template, which text is HTML.")
    p.alternative("A separate block that turns text into HTML",
                  "One more block and one more connection for every such text. The format after the colon is "
                  "already where you say how a value is shown.")
    p.fits("Used on something that is not text (a table, a number, a list, a picture), :html stops the run and says "
           "“html applies only to text”. HTML that comes from another HTML Template was already passed as it is.")

    assert win.home.root == env.home, "the editor and the test mail server's connection use the same Taskloom folder"
    p.step("Open scores.yaml and click the HTML Template block report. styled, a Python Code block, gives the HTML "
           "that pandas writes for a table whose first row is painted green. The Template has {styled:html}, then "
           "{styled}.")
    tab = open_flow(win, monkeypatch, env.root / "scores.yaml", STYLED_FLOW)
    click_block(tab, "report")
    box = code_box(win)
    p.shot(win, "① styled gives the HTML as text. ② The Template of report: {styled:html}, then {styled}.",
           highlight=["styled", box], badges=True, zoom=[box])
    p.note("This computer has no pandas, so styled holds the HTML itself, in the shape pandas writes it: a <style> "
           "block with one rule for the green cells, then the table.")
    p.check("② The Template holds {styled:html} and {styled}",
            "\n{styled:html}\n" in box.toPlainText() and box.toPlainText().endswith("\n{styled}"))

    p.step("Press ▶ Run now, and look at the email the mail server received.")
    press_run_now(win)
    finished(qtbot, tab, 1)
    p.check("The run succeeded and the mail server received one email",
            tab.runs[0].status == "success" and len(smtp.messages) == 1)
    html = html_of(smtp.messages[0])
    reader, _ = email_reader(qtbot, smtp.messages[0])
    p.shot(reader, "Under “With {styled:html}”: the table, its first row green. Under “With {styled}”: the same "
                   "text shown as text, with its tags.")
    reader.close()
    table = styled_table(html)
    p.check("{styled:html}: the email holds the table itself, with its cells",
            [td.text for td in table.all("td")] == ["EU", "10", "US", "20"])
    p.check("Its first row is green, the second is not: the colour is in each cell's own style, for Outlook",
            {td.text: td.style.get("background-color") for td in table.all("td")}
            == {"EU": "#008000", "10": "#008000", "US": None, "20": None})
    p.check("No <style> block is left in the email", "<style" not in html.lower())
    p.check("{styled}: the email also shows the tags as text", "&lt;table" in html and "&lt;/table&gt;" in html)
    for each in win.tabs_list():
        each.doc.dirty = False
