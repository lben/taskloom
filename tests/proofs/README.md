# Writing a proof

Every feature or fix comes with a proof: a test that drives the real app the way you would, and records what it
showed. CI runs the proofs on Windows, and those Windows cannot show on Linux, and makes one PDF of them
(`packaging/proof_pdf.py`), attached to each release.
`test_proof_lines.py` is the example every proof follows.

## The shape: a short story

Each proof tells the same five things, in this order:

1. **What you asked**: the user's own words, quoted.
2. **The idea**: what the program now does, in a few plain sentences. No function names.
3. **Why this design**: why it is the simplest way to do it, the other ways it could have been done and why they
   lost, and how it fits the rest of Taskloom (what it reuses, what it is consistent with).
4. **See it working**: numbered steps, each with its screenshot and what to look at in it.
5. **Checked**: each check, ✓ or ✗, in plain words.

The idea, why it is the simplest, at least one other way and how it fits are required: a proof without them fails.

## The API

```python
def test_something(proof, win, qtbot):
    p = proof("Each start block runs its own line", "the user's words, as they said them")
    p.idea("...")                       # one call per short paragraph; the first is the summary on the cover
    p.why_simplest("...")
    p.alternative("Another way", "Why it lost.")
    p.fits("...")

    p.step("Press ▶ Run now in the toolbar. It starts line ①.")
    ...                                 # do it, as you would: click, type, choose
    p.shot(win, "What you see: ① ran, ② did not.", highlight=[LINE_1, LINE_2], badges=True, zoom=[LINE_1, LINE_2])
    p.check("① ran: its three blocks show success", ...)    # recorded, and asserted
    p.note("Why something is done differently here than you would.")
    p.text("The command printed", output)                   # text a screenshot cannot show
```

- `win` is the editor, 1600×1000. `p.shot(widget, caption)` shoots any widget; `over=win` draws a menu or a dialog
  over the window where it is on the screen.
- A menu or a modal dialog waits until it is closed: open it with `p.while_open(open_it, look)`, where `look(it)`
  shoots it and acts on it as you would (`choose(menu, "Results of this block…")`). `right_click(tab, block)` opens a
  block's menu; `menu_item(menu, text)` is one of its items.

## Highlights and close-ups

`highlight` outlines what to look at, in orange. Each entry is one of:

| Entry | Outlines |
|---|---|
| `"total_sales"` | that block on the canvas |
| a widget | that widget |
| a menu's `QAction` (`menu_item(menu, text)`) | that item of the open menu |
| `(table, row)` | that row |
| `table.item(row, column)` | that cell |
| `(text_box, "some text")` | the lines that show that text (e.g. `(win.log.text, "run #3 ")` in the Log) |
| a `QRect` | that rectangle, in the screenshot's pixels |
| a list of these | one outline around them all (a line of blocks, a column of cells) |

Menu items, table rows and cells, and lines of text touch the ones next to them: their outline hugs them, so it
covers neither a table's header nor the next row.

`badges=True` numbers the outlines 1, 2, 3…; `badges=[1, 2, 1]` gives each its number. Write ①, ②… in a caption,
a step or a check, and the PDF draws the same badge there.

The window is 1600 pixels wide: on the page its text is too small to read. When the detail matters, add a close-up
with `zoom=` (the same kinds of entries): the PDF shows that part bigger, beside a small picture of the whole window.
Keep a close-up small, about 750×400 pixels at most (a few blocks, a few rows), or it cannot be shown bigger.
A dialog shot on its own needs no close-up.

## Tone

- Write for the person who asked: "you", plain words, short sentences, short paragraphs.
- No internal names (no settings keys, functions, files, classes) unless you see them on the screen.
- Start each step with what you do, in one sentence: those sentences are the step list on the first page.
- A caption says what to look at and what it shows, not what the picture is ("① ran: its blocks are green", not
  "The editor after the run").
- Make the idea and the reasons true: read the code they describe. Name the alternatives that were really possible.
- Show the case the user worried about. For "when one line fails", make a line fail.

## A proof Windows cannot show

When a proof cannot run on Windows (it needs what only Linux has, as the keeper's does), mark it with why, and skip
it on Windows once its story is told:

```python
@pytest.mark.linux_proof("The keeper looks after Linux servers: ...")
def test_something(proof, win, qtbot):
    p = proof(...)
    ...                                 # the idea, why it is the simplest, the other ways, how it fits
    if sys.platform == "win32":
        pytest.skip("Not shown on Windows. ...")
```

CI's `proof-linux` job runs the marked proofs on Linux. The PDF has each change once, where it was shown, and says
on its pages which system that was; for one shown on Linux, why.

## Running it

```sh
PROOF_DIR=/tmp/proofs .venv/bin/python -m pytest tests/proofs -q     # without PROOF_DIR nothing is written
.venv/bin/python packaging/proof_pdf.py /tmp/proofs /tmp/proof.pdf
.venv/bin/python packaging/proof_pdf.py /tmp/proofs /tmp/proofs-linux /tmp/proof.pdf   # several runs: one PDF
```

The cover opens with what is new: each change whose scenario (`module.function`) was not in `tests/proofs` at the
last release (the nearest `v` tag before the commit built), each a link to its first page, and NEW on it and on its
pages. Under "New in <version>" when the commit built is that version's tag, else "New since <tag>". It is read with
git in the repository the script is in (`--repo` for another); without git, the history (a shallow checkout: CI
checks out with `fetch-depth: 0`) or a `v` tag, nothing is said to be new.

Then open the PDF and read it as the user would: every page.
