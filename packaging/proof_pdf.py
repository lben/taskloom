"""Make one PDF of the proofs that tests/proofs recorded (run with PROOF_DIR set; see tests/proofs/README.md):

    python packaging/proof_pdf.py <proof_dir>... <out.pdf> [--ci-url URL] [--title TEXT] [--repo FOLDER]

A cover (Taskloom version, date, platform, the CI run, the changes new in this version, each a link to its pages,
and each change with its result and summary), then each change as a short story: what was asked and the idea; why
this design; each step with its screenshot; what was checked. Each change says which system it was shown on.
Several proof folders (CI shows the proofs on Windows, and those Windows cannot show on Linux) make one PDF: each
folder's changes in the order they ran, the folders in the order given; a change skipped in one folder and shown in
another is in it once, as shown.

A change is new when its scenario (module.function in tests/proofs, the name of its proof folder) was not there at
the base tag: the nearest v tag from the parent of the commit built (for a release's build, the previous release),
read with the git program in --repo (by default the repository this script is in). Without git, the history (a
shallow checkout) or a base tag, nothing is said to be new.

    python packaging/proof_pdf.py <proof_dir>... <out.pdf> --evidence proof/<release> --feature docs/promises/X.yaml...

With features, the PDF is the evidence CI gathered in proof/<release>/ for each (the ledger, the checkers' output,
the mutants, the leak check, a question that blocks it), never written by hand. It opens with the
page to read if nothing else (each feature's verdict, the release-wide numbers, how to read the colours) and the C4
view; then each feature in the order given: what you asked, what must always be true, the design, the evidence, then
its changes (the proofs whose module starts with one of its `proofs`); then the other changes.
"""

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # it shows no window
if sys.platform == "win32":  # Qt's offscreen platform finds no fonts there unless pointed at them
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QPageLayout, QPageSize, QPainter,  # noqa: E402
                           QPdfWriter, QPen, QTextDocument, QTextFormat, QTextTable)

TITLE = "Proof that it works"
DPI = 96  # pages are laid out in screen pixels, what the screenshots are in
INK, MUTED, FAINT, RULE = "#1f2328", "#59636e", "#8c959f", "#d0d7de"
ACCENT = "#e8590c"  # as the highlights on the screenshots (tests/proofs/conftest.py)
OK, FAILED = "#1a7f37", "#c62828"
ASKED_TINT, IDEA_TINT, IDEA_BAR, CARD_TINT = "#fff4e6", "#edf3fc", "#3b6fb6", "#f6f8fa"
FONTS = ["Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"]
SYMBOLS = "font-family: 'Segoe UI Symbol', 'DejaVu Sans', 'Apple Symbols'"
MONO_FONT = "font-family: Consolas, Menlo, 'DejaVu Sans Mono', 'Courier New'"
MONO = f"{MONO_FONT}; font-size: 8pt; white-space: pre-wrap"
FOOTER = 28  # under each page's text: the footer line
MAX_ZOOM = 1.5  # a close-up is shown at most this much bigger than on the screen
MIN_SCALE = 0.5  # a page is shrunk at most this much to fit on fewer pages
GAP = 16
BLANK = 1000  # pixels left blank under each page as it is laid out (see build()): whole ones, or Qt breaks pages anew


def esc(text: str) -> str:
    """Text as HTML, with ①, ②… drawn as the badges on the screenshots."""
    return re.sub("[①-⑳]", lambda m: f"<img src=\"badge{ord(m.group()) - 0x245F}\" width=\"17\" height=\"17\" "
                                      "style=\"vertical-align: middle\">", html.escape(text))


def mark(ok: bool, text: str, size: str = "") -> str:
    style = f"color: {OK if ok else FAILED}" + (f"; font-size: {size}" if size else "")
    return (f"<span style=\"{style}\"><span style=\"{SYMBOLS}; font-weight: bold\">{'✓' if ok else '✗'}</span>"
            f"&nbsp;{esc(text)}</span>")


def outcome(record: dict) -> str:
    if record["status"] == "passed":
        return mark(True, "passed")
    return mark(False, "not shown (skipped)" if record["status"] == "skipped" else record["status"])


def label(text: str, color: str = MUTED, space: int = 0, anchor: str = "") -> str:
    """A small capitals line over what it names (`space`: above it; `anchor`: the name a link to it goes to)."""
    return (f"<p style=\"color: {color}; font-size: 8.5pt; font-weight: 600; text-transform: uppercase; "
            f"letter-spacing: 1.5px; margin-top: {space}px; margin-bottom: 6px\">"
            + (f"<a name=\"{anchor}\"></a>" if anchor else "") + f"{esc(text)}</p>")


def box(content: str, tint: str, bar: str) -> str:
    """Content in a tinted box with a coloured bar on its left."""
    return (f"<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr><td width=\"5\" bgcolor=\"{bar}\"></td>"
            f"<td bgcolor=\"{tint}\" style=\"padding: 14px 18px\">{content}</td></tr></table>")


def paragraphs(texts: list, size: str = "11pt") -> str:
    """The paragraphs; none: that the proof does not say what it must (it failed)."""
    if not texts:
        return f"<p>{mark(False, 'Not said: a proof must say this.')}</p>"
    return "".join(f"<p style=\"font-size: {size}; line-height: 135%; margin-bottom: 10px\">{esc(t)}</p>"
                   for t in texts)


def columns(left: str, right: str, split: int = 48) -> str:
    return (f"<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top: 22px\"><tr>"
            f"<td width=\"{split}%\" valign=\"top\">"
            f"{left}</td><td width=\"4%\"></td><td valign=\"top\">{right}</td></tr></table>")


def heading(over: str, title: str, over_color: str = ACCENT, anchor: str = "") -> str:
    return (label(over, over_color, anchor=anchor) +
            f"<p style=\"font-size: 24pt; font-weight: 600; color: {INK}; margin-top: 0\">"
            f"{esc(title)}</p>")


def when(record: dict) -> str:
    started = dt.datetime.fromisoformat(record["started"])
    zone = started.tzname() or ""
    return f"{started.day} {started:%B %Y}, {started:%H:%M}" + (f" ({zone})" if zone else "")


def news(repo: Path, version: str) -> tuple[str, str, set] | None:
    """What is new is told against the base tag, the nearest v tag from the parent of the commit built (for a
    release's build, the previous release): the heading ("New in <version>" when that commit is tagged v<version>,
    else "New since <base tag>"), the base tag, and the proof scenarios (module.function) there. None without the git
    program, the history (a shallow checkout) or a base tag."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, encoding="utf-8",
                              errors="replace", check=True).stdout

    try:
        base = git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD^").strip()
        modules = [path for path in git("ls-tree", "--full-tree", "--name-only", base, "tests/proofs/").splitlines()
                   if re.fullmatch(r"tests/proofs/test_\w+\.py", path)]
        before = {f"{Path(module).stem}.{function}" for module in modules
                  for function in re.findall(r"^def (test_\w+)", git("show", f"{base}:{module}"), re.M)}
        tagged = f"v{version}" in git("tag", "--points-at", "HEAD").split()
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"New in {version}" if tagged else f"New since {base}", base, before


def problems(records: list, sessions: list) -> list:
    """What went wrong with the proofs on screen, in words: none when every change passed its proof."""
    found = []
    for proof_dir, session in sessions:
        if session is None:
            found.append(f"The proof run of {proof_dir.name} did not finish: proofs may be missing.")
        elif session["exitstatus"] != 0:
            found.append(f"The proof run on {session['system']} failed (pytest exit status "
                         f"{session['exitstatus']}): see the CI run.")
    if not records:
        found.append("No proof was recorded.")
    if failed := sum(r["status"] != "passed" for r in records):
        found.append(f"{failed} of {len(records)} changes did not pass their proof.")
    return found


def all_passed(records: list) -> str:
    return "The change passed its proof." if len(records) == 1 else f"All {len(records)} changes passed their proofs."


def facts(records: list, ci_url: str, commit: str = "") -> str:
    """The version, the commit built (when known), the date, the platform and the CI run, as a small table."""
    about = [("Version", ", ".join(sorted({r["version"] for r in records})) or "unknown")]
    about += [("Commit", commit)] if commit else []
    about += [("Date", when(min(records, key=lambda r: r["started"])) if records else "unknown"),
              ("Platform", "; ".join(sorted({r["platform"] for r in records})) or "unknown")]
    if ci_url:
        about.append(("CI run", f"<a href=\"{html.escape(ci_url)}\" style=\"color: {IDEA_BAR}\">"
                                f"{html.escape(ci_url)}</a>"))
    return "".join(f"<tr><td style=\"color: {MUTED}; padding: 3px 18px 3px 0\">{name}</td>"
                   f"<td style=\"padding: 3px 0\">{value if name == 'CI run' else esc(value)}</td></tr>"
                   for name, value in about)


def cover(title: str, records: list, sessions: list, ci_url: str, pages: list, since: tuple | None,
          new: list) -> str:
    """`since`: what news() says; `new`: whether each change is new."""
    verdict = "<br>".join(mark(False, p, "15pt") for p in problems(records, sessions)) or mark(
        True, all_passed(records), "15pt")
    return (label("Taskloom", ACCENT) +
            f"<p style=\"font-size: 34pt; font-weight: 600; margin-top: 0; margin-bottom: 8px\">{esc(title)}</p>"
            f"<p style=\"font-size: 13pt; color: {MUTED}; line-height: 140%; margin-bottom: 22px\">Each change in this "
            "version, shown working in the running app: what was asked, the idea behind it and why it is built that "
            "way, then the app doing it, step by step, with what was checked.</p>"
            f"<table cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top: 18px\">{facts(records, ci_url)}</table>"
            f"<table cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top: 26px\"><tr><td>{verdict}</td></tr></table>"
            f"{lists(records, pages, since, new)}")


def lists(records: list, pages: list, since: tuple | None, new: list) -> str:
    """The cover's lists of the changes: what is new (when news() says), then every change; each row with its number,
    its title (a link to its first page), its result, the system it was shown on and its page (`pages`)."""
    cell = f"valign=\"top\" style=\"padding: 12px 0; border-top: 1px solid {RULE}"

    def row(i: int, about: str, anchor: str = "") -> str:
        """Change i's line: its number, `about` it (its title...), its result, the system it was shown on, its page
        (`anchor`: the name a link to it goes to)."""
        r = records[i - 1]
        named = f"<a name=\"{anchor}\"></a>" if anchor else ""  # in the number's cell: next to a link, it is lost
        return (f"<tr><td width=\"5%\" {cell}; font-size: 18pt; font-weight: 600; color: {ACCENT}\">{named}{i}</td>"
                f"<td {cell}\">{about}</td><td width=\"14%\" {cell}; padding-left: 14px\">{outcome(r)}<br>"
                f"<span style=\"color: {MUTED}\">on {esc(r['system'])}</span></td>"
                f"<td width=\"8%\" align=\"right\" {cell}; color: {MUTED}\">page {pages[i - 1]}</td></tr>")

    def listing(name: str, rows: list) -> str:
        """The rows under their name, each row a table of its own, so that a page's end cuts none (see whole());
        the name in the first row's table, on that row's page."""
        tables = [f"<tr><td colspan=\"4\">{label(name)}</td></tr>{rows[0] if rows else ''}", *rows[1:]]
        return "".join(f"<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top: "
                       f"{0 if i else 30}px\">{table}</table>" for i, table in enumerate(tables))

    big = "font-size: 13pt; font-weight: 600"
    marked = (f"&nbsp;&nbsp;<span style=\"color: white; background-color: {ACCENT}; font-size: 8pt; "
              "font-weight: 600\">&nbsp;NEW&nbsp;</span>")

    def title_link(i: int) -> str:
        """Change i's title, a link to its first page."""
        return f"<a href=\"#change-{i}\" style=\"{big}; color: {IDEA_BAR}\">{esc(records[i - 1]['title'])}</a>"

    listed = [
        row(i, f"{title_link(i)}{marked if new[i - 1] else ''}<br>"
               f"<span style=\"font-size: 11pt; color: {MUTED}\">{esc(r['idea'][0] if r['idea'] else '')}</span>",
            anchor=f"row-{i}")  # where the go-back link on each of its pages goes
        for i, r in enumerate(records, 1)]
    news_list = ""
    if since:
        heading, base, _ = since
        linked = [row(i, title_link(i)) for i in range(1, len(records) + 1) if new[i - 1]]
        news_list = listing(heading if linked else f"Nothing new since {base}", linked)
    return f"{news_list}{listing('In this proof', listed)}"


def story(number: int, over: str, record: dict) -> list:
    """The two pages that tell the change: what was asked and the idea; why this design. The first is where the
    cover's link to the change goes."""
    asked = box(f"<p style=\"font-size: 18pt; font-style: italic; line-height: 135%; color: {INK}\">"
                f"“{esc(record['asked'])}”</p>", ASKED_TINT, ACCENT)
    idea = box(paragraphs(record["idea"], "13pt"), IDEA_TINT, IDEA_BAR)
    # What each step does (its first sentence), as a map of what is to come.
    steps = [re.split(r"(?<=[.!?])\s", e["text"], maxsplit=1)[0] for e in record["entries"] if e["kind"] == "step"]
    shown = "".join(f"<tr><td width=\"26\" style=\"color: {ACCENT}; font-weight: 600; padding: 3px 0\">{i}</td>"
                    f"<td style=\"padding: 3px 0\">{esc(text)}</td></tr>" for i, text in enumerate(steps, 1))
    roadmap = (f"<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top: 28px; "
               f"font-size: 11pt\"><tr><td colspan=\"2\">{label('How it is shown, step by step')}</td></tr>"
               f"{shown}</table>") if steps else ""
    result = (f"<p style=\"font-size: 11pt\">{outcome(record)} <span style=\"color: {MUTED}\">on "
              f"{esc(record['system'])}</span></p>")
    if record["why_not_windows"] and record["system"] != "Windows":
        result += (f"<p style=\"color: {MUTED}; line-height: 135%\">Shown on {esc(record['system'])}, not Windows. "
                   f"{esc(record['why_not_windows'])}</p>")
    first = (heading(over, record["title"], anchor=f"change-{number}") + result
             + columns(label("What you asked") + asked + roadmap, label("The idea") + idea, 44))
    cards = "".join(
        f"<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-bottom: 10px\"><tr>"
        f"<td bgcolor=\"{CARD_TINT}\" style=\"padding: 12px 16px\"><p style=\"font-size: 11pt; font-weight: 600; "
        f"margin-bottom: 4px\">{esc(a['option'])}</p><p style=\"color: {MUTED}; line-height: 140%\">"
        f"{esc(a['why_not'])}</p></td></tr></table>" for a in record["alternatives"])
    why = (heading(over, "Why this design")
           + columns(label("Why it is the simplest way") + paragraphs(record["why_simplest"])
                     + label("How it fits the rest of Taskloom", space=16) + paragraphs(record["fits"]),
                     label("Other ways it could have been done, and why not") + (cards or paragraphs([])), 50))
    return [first, why]


def groups(entries: list) -> list:
    """The entries, a page each: a screenshot with the steps, notes and checks around it."""
    pages = [[]]
    for entry in entries:
        if entry["kind"] in ("step", "shot") and any(e["kind"] == "shot" for e in pages[-1]):
            pages.append([])
        pages[-1].append(entry)
    return [page for page in pages if page]


def step_page(over: str, page: list, step: int, steps: int, images: dict, body: QSizeF, measure) -> tuple[str, int]:
    """One step's page: what you do, the screenshot (with its close-up) as big as fits, what to see, the checks;
    and the number of the last step on it."""
    top = []
    for entry in page:
        if entry["kind"] == "step":
            step += 1
            top.append(f"<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-bottom: 8px\"><tr>"
                       f"<td width=\"46\" valign=\"top\" style=\"font-size: 26pt; font-weight: 600; color: {ACCENT}\">"
                       f"{step}</td><td valign=\"top\">{label(f'{over} · See it working · Step {step} of {steps}')}"
                       f"<p style=\"font-size: 13pt; line-height: 135%\">{esc(entry['text'])}</p></td></tr></table>")
        elif entry["kind"] == "note":
            top.append(f"<p style=\"color: {MUTED}; font-style: italic; margin-left: 46px\">{esc(entry['text'])}</p>")
        elif entry["kind"] == "text":
            top.append(f"<p style=\"margin-left: 46px\">{esc(entry['caption'])}</p>"
                       f"<p style=\"{MONO}; background-color: {CARD_TINT}; margin-left: 46px; margin-bottom: 12px\">"
                       f"{html.escape(entry['text'])}</p>")
    if not any(e["kind"] == "step" for e in page):  # a second screenshot of a step
        top.insert(0, label(f"{over} · See it working · Step {step}, continued"))
    shot = next((e for e in page if e["kind"] == "shot"), None)
    checks = "".join(f"<p style=\"margin-bottom: 5px\">{mark(e['ok'], e['text'])}</p>"
                     for e in page if e["kind"] == "check")
    bottom = ""
    if shot or checks:
        see = label("What you see") + paragraphs([shot["caption"]], "12pt") if shot else ""
        bottom = (f"<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top: 10px\"><tr>"
                  f"<td valign=\"top\" style=\"padding-right: 24px\">{see}</td>"
                  + (f"<td width=\"38%\" valign=\"top\">{label('Checked')}{checks}</td>" if checks else "")
                  + "</tr></table>")

    def html_with(pictures: str) -> str:
        return "".join(top) + pictures + bottom

    if shot is None:
        return html_with(""), step
    room = QSizeF(body.width() - 12, body.height() - measure(html_with("")) - 24)
    return html_with(pictures(shot, images, room)), step


def pictures(shot: dict, images: dict, room: QSizeF) -> str:
    """The screenshot, as big as fits `room` (not bigger than on the screen). With a close-up: the close-up as big
    as fits (up to MAX_ZOOM) beside it, or under it when that shows it half as big again, and the window in the room
    left."""
    name, full = images[shot["file"]]
    caption = f"<br><span style=\"color: {FAINT}; font-size: 8pt\">{{}}</span>"
    if "zoom" not in shot:
        scale = min(1.0, room.width() / full.width(), room.height() / full.height())
        return f"<p align=\"center\"><img src=\"{name}\" width=\"{full.width() * scale:.0f}\" " \
               f"height=\"{full.height() * scale:.0f}\"></p>"
    zoom_name, zoom = images[shot["zoom"]]
    room = QSizeF(room.width(), room.height() - 20)  # for the line under each picture
    beside = min(MAX_ZOOM, room.height() / zoom.height(), (room.width() * 0.62 - GAP) / zoom.width())
    under = min(MAX_ZOOM, room.width() / zoom.width(), (room.height() * 0.7 - GAP - 20) / zoom.height())
    side = beside >= under / 1.5  # beside reads better: the window, then what to look at in it
    if side:
        z = beside
        s = min(1.0, (room.width() - GAP - zoom.width() * z) / full.width(), room.height() / full.height())
    else:
        z = under
        s = min(1.0, room.width() / full.width(), (room.height() - GAP - 20 - zoom.height() * z) / full.height())
    window = (f"<img src=\"{name}\" width=\"{full.width() * s:.0f}\" height=\"{full.height() * s:.0f}\">"
              + caption.format("The whole window: the dashed frame is the close-up"))
    close_up = (f"<img src=\"{zoom_name}\" width=\"{zoom.width() * z:.0f}\" height=\"{zoom.height() * z:.0f}\">"
                + caption.format("Close-up"))
    if side:
        return (f"<table align=\"center\" cellspacing=\"0\" cellpadding=\"0\"><tr><td valign=\"middle\">{window}</td>"
                f"<td width=\"{GAP}\"></td><td valign=\"middle\">{close_up}</td></tr></table>")
    return f"<p align=\"center\">{window}</p><p align=\"center\" style=\"margin-top: {GAP}px\">{close_up}</p>"


def checked(over: str, record: dict) -> str:
    checks = [e for e in record["entries"] if e["kind"] == "check"]
    passed = sum(e["ok"] for e in checks)
    if record["status"] == "passed":
        verdict = mark(True, f"All {passed} checks passed." if passed != 1 else "The check passed.", "15pt")
    else:
        verdict = mark(False, "The proof did not pass" + (" (skipped)." if record["status"] == "skipped" else "."),
                       "15pt")
    listed = "".join(f"<p style=\"font-size: 11.5pt; margin-bottom: 8px\">{mark(e['ok'], e['text'])}</p>"
                     for e in checks) or f"<p>{mark(False, 'Nothing was checked.')}</p>"
    wrong = ""
    if record["error"]:
        wrong = (label("What went wrong", FAILED, space=18) +
                 box(f"<p style=\"{MONO}\">{html.escape(record['error'])}</p>", "#fdecea", FAILED))
    return (heading(over, "What was checked") + f"<p style=\"margin-top: 10px; margin-bottom: 18px\">{verdict}</p>"
            + listed + wrong)


# --- The features (--feature, --evidence): what must always be true of each, and the evidence it is -----------------

def read(path: Path):
    """What the JSON or YAML file holds; None when there is no such file."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


def load_feature(promises: Path, evidence: Path) -> dict:
    """The feature of the agreed list `promises` (docs/promises/<stem>.yaml), its design choices next to it, and what
    CI found of it in <evidence>/<stem>/ (a file that is not there: None)."""
    agreed = read(promises)
    folder = evidence / promises.stem
    found = {name: read(folder / f"{name}.json") for name in ("ledger", "checkers", "mutants", "leak", "blocked")}
    return {**found, "name": agreed["feature"], "agreed": agreed["agreed"], "asked": agreed.get("asked") or [],
            "proofs": tuple(agreed.get("proofs") or ()), "folder": folder,
            "design": read(promises.with_name(promises.stem + ".design.yaml")),
            "found": read(promises.with_name(promises.stem + ".found.yaml"))}


def by_feature(proofs: list, features: list) -> list:
    """[(the feature's index, its proofs on screen)] in the features' order, then (None, the other proofs): a proof is
    the first feature's whose `proofs` its module (tests/proofs/<module>.py) starts with."""
    sections, rest = [], proofs
    for index, feature in enumerate(features):
        mine = [p for p in rest if feature["proofs"] and p[0].name.split(".")[0].startswith(feature["proofs"])]
        rest = [p for p in rest if all(p is not m for m in mine)]
        sections.append((index, mine))
    return [*sections, (None, rest)]


def mermaid(path: Path) -> QImage | None:
    """The drawing of the Mermaid file, made twice as big as it is shown (see drawing()), sharp; None when there is
    no such file."""
    if not path.is_file():
        return None
    import mmdr  # only the features' pages draw diagrams

    diagram = mmdr.render(path.read_text(encoding="utf-8"))
    size = QImage.fromData(diagram.png(background="#ffffff")).width()
    return QImage.fromData(diagram.png(width=2 * size, background="#ffffff"))


def drawing(name: str, image: QImage | None, room: QSizeF, path: Path) -> str:
    """The diagram (resource `name`) at its size, or smaller to fit `room`; or that its file `path` is missing."""
    if image is None:
        return f"<p>{mark(False, f'Not found: {path.as_posix()}')}</p>"
    scale = min(0.5, room.width() / image.width(), room.height() / image.height())
    return (f"<p align=\"center\"><img src=\"{name}\" width=\"{image.width() * scale:.0f}\" "
            f"height=\"{image.height() * scale:.0f}\"></p>")


AMBER = "#b35c00"
STATUS = {"GREEN": (OK, "#e6f4ea"), "AMBER": (AMBER, "#fff1d6"), "RED": (FAILED, "#fdecea")}  # colour, tint
MEANING = {"GREEN": "Checked for every input or state: a model checker explored every state, or a test tried every "
                    "case of a stated range.",
           "AMBER": "Checked by tests only. They all pass, but a bug could still hide in a case they did not try; "
                    "each amber row says why it is not green.",
           "RED": "Not verified, or a check failed. Each red row says what is missing or what failed."}
# The checkers, in the order their output is shown, in plain words. A trace validation comes before the model check:
# it shows that the model, which the model check explores, behaves as the code does.
KINDS = {"trace": ("Trace validation", "real runs replayed through the model, to show it behaves as the code does"),
         "model": ("Model check", "every state the model can reach, explored"),
         "tests": ("Tests", ""),
         "mutation": ("Mutation testing", "small bugs planted in the code on purpose, to see the tests catch them"),
         "leak": ("Memory and leak check", ""),
         "charter": ("Architecture rules", "the charter's rules on how the code is laid out")}
TERMINAL, TERMINAL_TOP, TERMINAL_INK, TERMINAL_DIM = "#0d1117", "#262c36", "#e6edf3", "#8b949e"
MUTATION_BAR = 90  # percent of the planted bugs the tests must catch


def day(value) -> str:
    """A date (a YAML date or ISO text) in words: 7 November 2026."""
    date = value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
    return f"{date.day} {date:%B %Y}"


def section(text: str, note: str = "", space: int = 26) -> str:
    """A section's heading, with a quiet note after it."""
    quiet = f"&nbsp;&nbsp;<span style=\"font-size: 10pt; font-weight: normal; color: {MUTED}\">{esc(note)}</span>"
    return (f"<p style=\"font-size: 15pt; font-weight: 600; margin-top: {space}px; margin-bottom: 8px\">{esc(text)}"
            f"{quiet if note else ''}</p>")


def kept(text: str, items: list, note: str = "") -> str:
    """The section `text`, its items each a table whole on one page (see whole()); its heading in the first item's
    table, so that it is on that item's page."""
    first = items[0] if items else ""
    return "".join([f"<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top: 26px\"><tr><td>"
                    f"{section(text, note, 0)}{first}</td></tr></table>", *items[1:]])


def verdict(feature: dict) -> tuple[str, str]:
    """The feature's verdict, one phrase, and its colour."""
    if feature["blocked"] is not None:
        return AMBER, "BLOCKED: needs your decision"
    if feature["ledger"] is None:
        return FAILED, "Not certified: no ledger"
    missing = [what for name, what in (("mutants", "mutation testing"), ("leak", "leak check")) if feature[name] is None]
    if missing:
        return FAILED, f"Not certified: no {' or '.join(missing)} result"
    red = sum(row["status"] == "RED" for row in feature["ledger"]["rows"])
    if red:
        return FAILED, f"Not certified: {red} red"
    failed = [c["name"] for c in feature["checkers"] or [] if c["exit"] != 0]
    return (FAILED, f"Not certified: {', '.join(failed)} failed") if failed else (OK, "Certified")


def tally(feature: dict) -> list:
    """How many rows the feature's ledger has, then how many are green, amber and red."""
    rows = (feature["ledger"] or {}).get("rows", [])
    return [len(rows), *(sum(row["status"] == status for row in rows) for status in STATUS)]


def same_commit(release: dict, features: list) -> tuple:
    """Whether every checker and test result is of the commit built: (what, passed, in words)."""
    commit = str(release["commit"])[:7]
    others = sorted({f"{c['name']} ran on {str(c['commit'])[:7]}" for f in features for c in f["checkers"] or []
                     if str(c["commit"])[:7] != commit} |
                    {f"the tests of {f['name']} ran on {str(x)[:7]}" for f in features
                     for x in (f["ledger"] or {}).get("commits", []) if str(x)[:7] != commit})
    return ("Commit", not others, f"Not all of this commit ({commit}): " + "; ".join(others) if others else
            f"Every checker and test result here is of commit {commit}, the one built.")


def release_lines(release: dict, records: list, sessions: list, features: list) -> list:
    """The release-wide numbers: (what, passed or None when it is neither, in words)."""
    found = problems(records, sessions)
    lines = [("Proofs on screen", not found, "; ".join(found) or all_passed(records))]
    if release is None:
        return lines + [("Release", False, "No release-wide numbers: release.json was not found.")]
    mutation, tests, checkers = release["mutation"], release["tests"], release["checkers"]
    each = "".join(f" · {name}: {n['passed']:,} passed, {n['failed']} failed, {n['skipped']} skipped"
                   for name, n in tests["platforms"].items())
    health = release["design_health"]
    kept_ones = " · ".join(f"{name}: {reason}" for name, reason in (health.get("kept") or {}).items())
    return lines + [
        same_commit(release, features),
        ("Mutation testing", mutation["score"] >= MUTATION_BAR,
         f"{round(mutation['score'], 1):g}%: the tests caught {mutation['killed']:,} of the {mutation['total']:,} small "
         f"bugs planted in the code on purpose. The bar is {MUTATION_BAR}%."),
        ("Tests", tests["failed"] == 0, f"{tests['passed']:,} passed, {tests['failed']} failed, {tests['skipped']} "
                                        f"skipped{each}"),
        ("Checkers", checkers["passed"] == checkers["run"], f"{checkers['passed']} of the {checkers['run']} run "
                                                            "passed."),
        ("Memory and leaks", release["leak"]["passed"], release["leak"]["summary"]),
        ("Architecture rules", release["charter"]["violations"] == 0,
         f"{release['charter']['rules_checked']} rules of the charter checked, {release['charter']['violations']} "
         "broken."),
        ("Design health", None, f"Functions over 25 decision points (the ifs, loops, ands and ors in one "
                                f"function): {health['over_points']}. Over 80 lines: {health['over_lines']}."
                                + (f" Kept as they are, and why: {kept_ones}." if kept_ones else "")),
    ]


def summary(title: str, features: list, release: dict | None, records: list, sessions: list, ci_url: str) -> str:
    """The first page, to read if nothing else: each feature's verdict, the release-wide numbers, how to read the
    colours."""
    cell = f"valign=\"top\" style=\"padding: 8px 12px; border-top: 1px solid {RULE}\""
    names = ("", "Feature", "Invariants", "Green", "Amber", "Red", "Verdict")
    head = "".join(f"<td bgcolor=\"{CARD_TINT}\" {cell}><b>{name}</b></td>" for name in names)
    rows = ""
    for i, feature in enumerate(features, 1):
        colour, phrase = verdict(feature)
        tint = next(t for c, t in STATUS.values() if c == colour)
        numbers = "".join(f"<td width=\"8%\" {cell}>{n}</td>" for n in tally(feature))
        rows += (f"<tr><td width=\"3%\" {cell}><a name=\"feature-row-{i}\"></a>{i}</td>"
                 f"<td {cell}><a href=\"#feature-{i}\" style=\"color: {IDEA_BAR}; font-weight: 600\">"
                 f"{esc(feature['name'])}</a></td>{numbers}<td width=\"26%\" bgcolor=\"{tint}\" {cell}>"
                 f"<span style=\"color: {colour}; font-weight: 600\">{esc(phrase)}</span></td></tr>")
    numbers = "".join(f"<tr><td width=\"16%\" valign=\"top\" style=\"color: {MUTED}; padding: 4px 18px 4px 0\">{what}"
                      f"</td><td style=\"padding: 4px 0\">{esc(words) if ok is None else mark(ok, words)}</td></tr>"
                      for what, ok, words in release_lines(release, records, sessions, features))
    key = "".join(f"<tr><td width=\"10%\" bgcolor=\"{STATUS[s][1]}\" style=\"padding: 7px 12px; color: {STATUS[s][0]}; "
                  f"font-weight: bold\">{s}</td><td bgcolor=\"{STATUS[s][1]}\" style=\"padding: 7px 12px\">"
                  f"{esc(MEANING[s])}</td></tr>" for s in STATUS)
    table = "<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\""
    commit = str((release or {}).get("commit", ""))[:7]
    return (label("Taskloom", ACCENT) +
            f"<p style=\"font-size: 30pt; font-weight: 600; margin-top: 0; margin-bottom: 8px\">{esc(title)}</p>"
            f"<table cellspacing=\"0\" cellpadding=\"0\">{facts(records, ci_url, commit)}</table>"
            + section("Read this page if you read nothing else")
            + f"{table}><tr>{head}</tr>{rows}</table>"
            + section("Release-wide numbers") + f"{table}>{numbers}</table>"
            + section("How to read the colors") + f"{table}>{key}</table>"
            + f"<p style=\"color: {MUTED}; margin-top: 10px\">Each checker's output shows the log it is from, the "
              "commit it ran on and its exit code (0: it passed). The diagrams are generated from the code, never drawn "
              "by hand.</p>")


def c4_page(images: dict, room: QSizeF, evidence: Path) -> str:
    """The C4 view (packaging/c4.py): who and what Taskloom talks to; the programs it is made of."""
    maps = [("context", "Who and what Taskloom talks to."),
            ("containers", "The programs Taskloom is made of, and where each keeps its state.")]
    shown = "".join(f"<p style=\"font-size: 12pt; font-weight: 600; margin-top: 18px\">{esc(caption)}</p>"
                    + drawing(f"c4/{name}", images[f"c4/{name}"], room, evidence / "c4" / f"{name}.mmd")
                    for name, caption in maps)
    return (heading("The C4 view", "How Taskloom is built")
            + f"<p style=\"font-size: 11pt; color: {MUTED}; line-height: 135%\">Two maps of Taskloom, the first two "
              "of the C4 model's zoom levels (context, containers, components, code). They are generated from the "
              "code, never drawn by hand.</p>" + shown)


def blocked_banner(blocked: dict) -> str:
    options = "".join(f"<li style=\"font-size: 12pt; margin-bottom: 4px\">{esc(o)}</li>" for o in blocked["options"])
    return box(f"<p style=\"font-size: 17pt; font-weight: 600; color: {AMBER}\">BLOCKED: needs your decision</p>"
               f"<p style=\"font-size: 12pt; margin-top: 4px\">I can't certify this until you choose.</p>"
               f"<p style=\"font-size: 12pt; font-weight: 600; margin-top: 10px\">{esc(blocked['question'])}</p>"
               f"<ol type=\"A\">{options}</ol>", STATUS["AMBER"][1], AMBER)


# The words this proof uses, in plain words with an example each, so that the reader and the proof mean the same; a
# checker's only when a feature ran one. The texts are HTML.
CONCEPTS = [
    (None, "A test", "A small program that runs Taskloom's real code in one situation and checks the result. For "
     "example: a flow scheduled for 9:00, a fake clock moved to 9:00, then a check that the flow ran once. It proves "
     "that one situation works. A situation no test tries is not checked."),
    (None, "A proof on screen", "The real Taskloom app on Windows, clicked through step by step as you would, with a "
     "screenshot of each step. The orange box on a screenshot shows what to look at."),
    (None, "A promise", "A rule that must always hold, agreed with you in chat before the code was written. For "
     "example: “If you turn the service off, it stays off.” Each promise is one row of its feature's evidence ledger, "
     "with a colour: <b>green</b>, checked in every case; <b>amber</b>, checked by tests that all pass, but a case "
     "they don't try could still hide a bug; <b>red</b>, not checked, or a check failed."),
    ("mutation", "Mutation testing", "It checks that the tests are good. A tool makes many copies of the code, each "
     "with one small change planted on purpose, for example “late by more than 2 minutes” turned into “late by 2 "
     "minutes or more”. Then it runs all the tests on every copy. <b>Caught</b>: some test failed, so the tests "
     "noticed the change. <b>Survived</b>: every test still passed. The score is the share caught."),
    ("mutation", "A surviving mutant", "A planted change that no test noticed. Your code does not contain that "
     "change, so a survivor is never a bug in your code. It is one of two things:<br>1. A change that does nothing. "
     f"For example, the scheduler asks the database <span style=\"{MONO_FONT}\">SELECT id FROM service_on</span>, "
     f"and one copy asks <span style=\"{MONO_FONT}\">select id from service_on</span> instead. The database reads "
     "both the same way, so that copy works exactly like the real code and no test could ever fail on it. The tool "
     "can't tell a harmless change from a harmful one, so it still counts it as missed: that is why 100% is out of "
     "reach.<br>2. A gap in the tests: the "
     "change does matter, and no test checks it. These are fixed by adding a test.<br>So a feature is certified only "
     "when every survivor left has a one-line note saying why it changes nothing you could see."),
    ("model", "A model check", "A small, simplified copy of the design, written for a checking tool; not the code "
     "itself. The tool tries every order in which things can happen, such as the service turned off while a run is "
     "starting, but only with a few of each (for example 2 flows). It proves the design has no bad order within "
     "those limits."),
    ("trace", "Trace validation", "The real code runs while every step it takes is recorded, and each recording is "
     "checked against the model: every step must be one the model allows. It shows the code behaves like the checked "
     "design, for the runs recorded, not for every possible one."),
    ("leak", "A leak check", "The service runs for many minutes on a fake clock while its memory, threads and open "
     "files are measured. They must level off, not keep growing: something that keeps growing would slow down or "
     "crash a service left on for weeks."),
    ("charter", "Design health", "Counts the functions that are very long or have very many branches, the kind of "
     "code where bugs hide. The count must not grow from one release to the next."),
]


def concepts_page(features: list) -> str:
    kinds = {c["kind"] for f in features for c in f["checkers"] or []} | ({"mutation"} if any(f["mutants"] for f in
                                                                                            features) else set())
    rows = "".join(f"<tr><td width=\"20%\" valign=\"top\" style=\"padding: 8px 18px 8px 0; font-weight: 600\">"
                   f"{esc(term)}</td><td style=\"padding: 8px 0; line-height: 135%\">{text}</td></tr>"
                   for kind, term, text in CONCEPTS if kind is None or kind in kinds)
    return (heading("Before you read", "The words this proof uses")
            + f"<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"font-size: 11pt\">{rows}</table>")


def shows(feature: dict) -> str:
    """What the feature's proof shows, in a few lines, highlighted before its details: what its mutation score means,
    the bugs proving it found (and what found them), and where it is checked more lightly."""
    lines = []
    m = feature["mutants"]
    if m:
        missed, unexplained = m["total"] - m["killed"], len(m.get("unexplained") or [])
        line = (f"The tests caught {m['killed']:,} of the {m['total']:,} bugs planted in the code on purpose "
                f"({round(m['score'], 1):g}%)")
        if m["score"] >= m["threshold"]:
            line += ", so almost every line's behaviour is checked by some test"
        line += (f". The {missed:,} they missed each change nothing anyone could see; each says why, under Surviving "
                 "mutants." if not unexplained else f". {unexplained:,} of the {missed:,} they missed {'has' if unexplained == 1 else 'have'} no reason yet.")
        lines.append(f"<p style=\"font-size: 11pt; line-height: 135%; margin-bottom: 8px\">{esc(line)}</p>")
    found = (feature["found"] or {}).get("found") or []
    if found:
        by = {}
        for bug in found:
            by[bug["found_by"]] = by.get(bug["found_by"], 0) + 1
        many = len(found) != 1
        line = (f"{len(found)} real bug{'s' * many} {'were' if many else 'was'} found while proving it, and fixed: "
                + ", ".join(f"{n} by {who}" for who, n in by.items())
                + ". Each now has a test that guards it; they are listed under Bugs found while proving it.")
        lines.append(f"<p style=\"font-size: 11pt; line-height: 135%; margin-bottom: 8px\">{esc(line)}</p>")
    lighter = (feature["found"] or {}).get("lighter") or []
    if lighter:
        lines.append("<p style=\"font-size: 11pt; margin-bottom: 4px\">Checked more lightly, so a bug is likeliest to "
                     "remain here:</p><ul>" + "".join(f"<li style=\"font-size: 11pt; line-height: 135%\">{esc(x)}</li>"
                                                     for x in lighter) + "</ul>")
    return kept("What this proof shows", [box("".join(lines), ASKED_TINT, ACCENT)]) if lines else ""


def bugs_found(feature: dict) -> list:
    """Each bug proving the feature found: what it did, what found it, the commit that fixed it, the test that guards
    it now."""
    return [f"<p style=\"font-size: 11pt; line-height: 135%; margin-bottom: 6px\"><b>{esc(b['what'])}</b> Found by "
            f"{esc(b['found_by'])}; fixed in {esc(str(b['fixed_in']))}; guarded by {esc(b['guarded_by'])}.</p>"
            for b in (feature["found"] or {}).get("found") or []]


def asked_and_agreed(feature: dict) -> str:
    """You asked (the quotes, with their dates); what must always be true; the design choices."""
    quotes = "".join(f"<p style=\"font-size: 13pt; font-style: italic; line-height: 135%; margin-bottom: 8px\">"
                     f"“{esc(a['words'])}”&nbsp;&nbsp;<span style=\"font-size: 10pt; font-style: normal; color: "
                     f"{MUTED}\">{day(a['date']).replace(' ', '&nbsp;')}</span></p>" for a in feature["asked"])
    rows = (feature["ledger"] or {}).get("rows", [])
    promised = "".join(f"<li style=\"font-size: 11pt; line-height: 135%; margin-bottom: 6px\">{esc(r['text'])}</li>"
                       for r in rows)
    design = "".join(f"<p style=\"font-size: 11pt; line-height: 135%; margin-bottom: 6px\"><b>Chosen:</b> "
                     f"{esc(str(d['chosen']).rstrip('.'))}. <b>Revisit if:</b> {esc(str(d['revisit_if']).rstrip('.'))}."
                     f"</p>" for d in feature["design"] or [])
    return (kept("You asked", [box(quotes, ASKED_TINT, ACCENT) if quotes else paragraphs([])])
            + section("What must always be true", f"(agreed with you in chat on {day(feature['agreed'])})")
            + (f"<ol>{promised}</ol>" if promised else paragraphs([]))
            + kept("Design", [design or paragraphs([])]))


def ledger_rows(rows: list) -> list:
    """The evidence ledger: its header, then each row (a table of its own, whole on a page); a row's status in a
    coloured cell, and under an amber or red row why it is not green."""
    widths = (4, 30, 24, 26, 16)
    cell = f"valign=\"top\" style=\"padding: 8px 10px; border-top: 1px solid {RULE}\""
    table = "<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\">"
    head = table + "<tr>" + "".join(
        f"<td width=\"{w}%\" bgcolor=\"{CARD_TINT}\" {cell}><b>{name}</b></td>" for w, name in
        zip(widths, ("#", "Must always be true", "How it is enforced", "Checker result", "Status"))) + "</tr></table>"
    tables = []
    for n, row in enumerate(rows, 1):
        colour, tint = STATUS[row["status"]]
        short = re.split(r"(?<=[.!?])\s", row["text"], maxsplit=1)[0]
        texts = [str(n), esc(short), esc(row["enforced"]) or mark(False, "Not said"), esc(row["checked"])]
        green = (f"<br><span style=\"font-size: 8.5pt\">{esc(row['why'])}</span>"
                 if row["status"] == "GREEN" and row["why"] else "")
        why = (f"<tr><td></td><td colspan=\"4\" style=\"padding: 0 10px 10px; line-height: 130%\"><span style=\"color: "
               f"{colour}; font-weight: 600\">Why {row['status'].lower()}:</span> {esc(row['why'])}</td></tr>"
               if row["status"] != "GREEN" else "")
        tables.append(f"{table}<tr>" + "".join(f"<td width=\"{w}%\" {cell}>{t}</td>" for w, t in zip(widths, texts))
                      + f"<td width=\"{widths[-1]}%\" bgcolor=\"{tint}\" {cell}><span style=\"color: {colour}; "
                        f"font-weight: bold\">{row['status']}</span>{green}</td></tr>{why}</table>")
    return [head + (tables[0] if tables else paragraphs([])), *tables[1:]]


def terminal(checker: dict, evidence: Path) -> str:
    """A checker's output as on its terminal: its log, commit and exit code; the command; its final lines. Over it,
    what it checks, in words, and what it found."""
    ok = checker["exit"] == 0
    top = (f"{html.escape((evidence / checker['log']).as_posix())}&nbsp;&nbsp;&nbsp;commit "
           f"{html.escape(str(checker['commit'])[:7])}&nbsp;&nbsp;&nbsp;<span style=\"color: "
           f"{'#3fb950' if ok else '#ff7b72'}\">exit {checker['exit']}</span>")
    # Its final lines, each run of lines its key matches (what to look at) in an orange box, as on the screenshots.
    key, shown = checker.get("key"), checker["tail"][-12:]
    runs: list[tuple[bool, list[str]]] = []
    for line in shown:
        boxed = bool(key and re.search(key, line))
        if runs and runs[-1][0] == boxed:
            runs[-1][1].append(line)
        else:
            runs.append((boxed, [line]))
    text = f"<p style=\"{MONO}; color: {TERMINAL_INK}\">"
    lines = (f"<p style=\"{MONO}; color: {TERMINAL_DIM}\">$ {html.escape(checker['command'])}</p>"
             + "".join(f"<table width=\"100%\" border=\"1.5\" cellspacing=\"0\" cellpadding=\"2\" style=\"border-color: "
                       f"{ACCENT}; border-style: solid; border-collapse: collapse\"><tr><td>{text}"
                       f"{html.escape(chr(10).join(group))}</p></td></tr></table>" if boxed
                       else f"{text}{html.escape(chr(10).join(group))}</p>" for boxed, group in runs))
    if key and not any(boxed for boxed, _ in runs):
        lines += f"<p style=\"font-size: 9pt\">{mark(False, f'None of its final lines shows what to look for ({key})')}</p>"
    name, gloss = KINDS.get(checker["kind"], (checker["kind"], ""))
    about = name + (f" ({gloss})" if gloss else "") + f": {checker['summary']}"
    return (f"<table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top: 14px\">"
            f"<tr><td style=\"padding-bottom: 6px; font-size: 11pt\">{mark(ok, about)}</td></tr>"
            f"<tr><td bgcolor=\"{TERMINAL_TOP}\" style=\"padding: 6px 12px; {MONO}; color: {TERMINAL_DIM}\">{top}</td>"
            f"</tr><tr><td bgcolor=\"{TERMINAL}\" style=\"padding: 8px 12px 10px\">{lines}</td></tr></table>")


def survivors(mutants: dict | None, path: Path) -> str:
    """The mutation score against its bar, then each mutant the tests did not catch and why it does not matter."""
    if mutants is None:
        return f"<p>{mark(False, f'Not found: {path.as_posix()}')}</p>"
    score = (f"Mutation score {round(mutants['score'], 1):g}%: the tests caught {mutants['killed']:,} of the "
             f"{mutants['total']:,} small bugs planted in the code on purpose. The bar is {mutants['threshold']}%.")
    lines = "".join(f"<p style=\"font-size: 11pt; line-height: 135%; margin-bottom: 6px\"><b>{esc(s['where'])}</b>"
                    f"&nbsp;&nbsp;{esc(s['change'])}. {esc(s['why_equivalent'])}</p>" for s in mutants["survivors"])
    return (f"<p style=\"font-size: 11pt; margin-bottom: 8px\">{mark(mutants['score'] >= mutants['threshold'], score)}"
            "</p>" + (lines or "<p style=\"font-size: 11pt\">None: the tests caught every one.</p>"))


def not_mutation_tested(mutants: dict | None) -> list:
    """Each function or mutant that mutation testing leaves out (its mutants are not in the score): how many mutants,
    and why, with the tests and proofs that check it instead. One paragraph each."""
    return [f"<p style=\"font-size: 11pt; line-height: 135%; margin-bottom: 6px\"><b>{esc(n['function'])}</b>"
            f"&nbsp;&nbsp;{n['mutants']:,} mutant{'s' * (n['mutants'] != 1)}. {esc(n['reason'])}</p>"
            for n in (mutants or {}).get("not_tested") or []]


def leaks(leak: dict | None, path: Path) -> str:
    if leak is None:
        return f"<p>{mark(False, f'Not found: {path.as_posix()}')}</p>"
    numbers = "".join(f"<tr><td style=\"color: {MUTED}; padding: 3px 18px 3px 0\">{esc(name)}</td>"
                      f"<td style=\"padding: 3px 0\">{esc(str(value))}</td></tr>"
                      for name, value in (leak.get("numbers") or {}).items())
    return (f"<p style=\"font-size: 11pt; margin-bottom: 8px\">{mark(leak['passed'], leak['summary'])}</p>"
            f"<table cellspacing=\"0\" cellpadding=\"0\">{numbers}</table>")


def on_screen(changes: list) -> str:
    """The changes that show the feature in the running app (their pages follow), each a link to its first page."""
    if not changes:
        return f"<p style=\"font-size: 11pt\">{mark(False, 'No proof on screen was recorded for this feature.')}</p>"
    return ("<p style=\"font-size: 11pt; margin-bottom: 8px\">The pages after this one show it in the running app, "
            "step by step:</p>" + "".join(
                f"<p style=\"font-size: 11pt; margin-bottom: 6px\"><span style=\"color: {ACCENT}; font-weight: 600\">"
                f"Change {n}</span>&nbsp;&nbsp;<a href=\"#change-{n}\" style=\"color: {IDEA_BAR}; font-weight: 600\">"
                f"{esc(r['title'])}</a>&nbsp;&nbsp;{outcome(r)} <span style=\"color: {MUTED}\">on "
                f"{esc(r['system'])}</span></p>" for n, r in changes))


def feature_page(index: int, total: int, feature: dict, changes: list, evidence: Path) -> str:
    """The feature's pages, before its changes' (`changes`: (number, record) each)."""
    folder = feature["folder"]
    order = list(KINDS)
    checkers = sorted(feature["checkers"] or [], key=lambda c: order.index(c["kind"]) if c["kind"] in order else
                      len(order))
    boxes = [terminal(c, evidence) for c in checkers] or [f"<p>{mark(False, 'No checker ran for this feature.')}</p>"]
    colour, phrase = verdict(feature)
    # A blocked feature opens with its question; any other with its verdict.
    opening = (blocked_banner(feature["blocked"]) if feature["blocked"] is not None else
               f"<p style=\"font-size: 13pt; font-weight: 600; color: {colour}\">{esc(phrase)}</p>")
    rest = (section("Memory and leaks") + leaks(feature["leak"], folder / "leak.json")
            + section("It works on screen") + on_screen(changes))
    left_out = not_mutation_tested(feature["mutants"])
    found = bugs_found(feature)
    return "".join([heading(f"Feature {index} of {total}", feature["name"], anchor=f"feature-{index}"), opening,
                    shows(feature), asked_and_agreed(feature),
                    kept("Evidence ledger", ledger_rows((feature["ledger"] or {}).get("rows", []))),
                    kept("Bugs found while proving it", found, "(and fixed)") if found else "",
                    kept("Checker output", boxes, "(final lines, this commit)"),
                    kept("Surviving mutants, one line each", [survivors(feature["mutants"], folder / "mutants.json")]),
                    kept("Not mutation-tested, and what checks it instead", left_out, "(not in the score)")
                    if left_out else "",
                    rest])


def cuts_a_box(doc: QTextDocument) -> bool:
    """Whether a page's end cuts a tinted box: a table with a coloured cell, or a paragraph with a background."""
    layout, height = doc.documentLayout(), doc.pageSize().height()
    boxes, frames = [], [doc.rootFrame()]
    while frames:
        frame = frames.pop()
        frames += frame.childFrames()
        if isinstance(frame, QTextTable) and any(frame.cellAt(row, column).format().background().style() != Qt.NoBrush
                                                 for row in range(frame.rows()) for column in range(frame.columns())):
            boxes.append(layout.frameBoundingRect(frame))
    block = doc.begin()
    while block.isValid():
        if block.blockFormat().background().style() != Qt.NoBrush:
            boxes.append(layout.blockBoundingRect(block))
        block = block.next()
    return any(box.top() // height != (box.bottom() - 1) // height for box in boxes)


def whole(doc: QTextDocument):
    """No table is cut by a page's end: one that does not fit at the bottom of a page starts the next (Qt would
    split its rows between the pages, line by line)."""
    layout, height = doc.documentLayout(), doc.pageSize().height()
    for frame in doc.rootFrame().childFrames():
        box = layout.frameBoundingRect(frame)  # where it is now that the tables before it have moved
        if box.top() // height != (box.bottom() - 1) // height:
            fmt = frame.frameFormat()
            fmt.setPageBreakPolicy(QTextFormat.PageBreak_AlwaysBefore)
            frame.setFrameFormat(fmt)


def framed(image: QImage) -> QImage:
    """The screenshot with a thin grey frame, so it stands out from the white paper."""
    image = image.convertToFormat(QImage.Format_RGB32)
    painter = QPainter(image)
    painter.setPen(QPen(QColor("#9e9e9e"), 2))
    painter.drawRect(1, 1, image.width() - 2, image.height() - 2)
    painter.end()
    return image


def badge(number: int) -> QImage:
    """A numbered badge, as tests/proofs/conftest.py draws it on the screenshots (drawn big: it is shown small)."""
    image = QImage(96, 96, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("white"), 8))
    painter.setBrush(QColor(ACCENT))
    painter.drawEllipse(QRectF(4, 4, 88, 88))
    font = QFont()
    font.setFamilies(FONTS)
    font.setPixelSize(52)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(QRectF(0, 0, 96, 96), Qt.AlignCenter, str(number))
    painter.end()
    return image


def read_proofs(proof_dirs: list) -> tuple[list, list]:
    """The proofs the folders hold, (folder, record) each, in the order they ran, the folders in the order given; and
    each folder's session (None: its run did not finish)."""
    proofs, sessions = [], []
    for proof_dir in proof_dirs:
        proofs += sorted(((folder, json.loads((folder / "proof.json").read_text(encoding="utf-8")))
                          for folder in (proof_dir.iterdir() if proof_dir.is_dir() else [])
                          if (folder / "proof.json").is_file()), key=lambda p: p[1]["started"])
        session_file = proof_dir / "session.json"
        sessions.append((proof_dir, json.loads(session_file.read_text()) if session_file.is_file() else None))
    # A change skipped on one system, where it cannot be shown, and shown on another: once, as shown. (A proof's
    # folder is named after its scenario.)
    shown = {folder.name for folder, record in proofs if record["status"] != "skipped"}
    return [(folder, record) for folder, record in proofs
            if record["status"] != "skipped" or folder.name not in shown], sessions


def change_pages(number: int, total: int, folder: Path, record: dict, marked: str, resources: dict, body: QSizeF,
                 measure) -> list:
    """Change `number`'s pages, (html, footer, go-back link, whether it is a feature's) each: its story, its
    steps, what was checked. Its screenshots are added to `resources`; `marked`: its NEW mark."""
    images = {}
    for entry in record["entries"]:
        for file in (entry.get("file"), entry.get("zoom")):
            if entry["kind"] == "shot" and file:
                name = f"proof{number}/{file}"
                resources[name] = framed(QImage(str(folder / file)))
                images[file] = (name, resources[name].size())
    over = f"Change {number}{marked}"
    footer = f"Change {number}: {record['title']} · on {record['system']}"
    back = (f"row-{number}", "Back to the list")
    pages = [(text, footer, back, False) for text in story(number, f"Change {number} of {total}{marked}", record)]
    step, steps = 0, sum(e["kind"] == "step" for e in record["entries"])
    for page in groups(record["entries"]):
        text, step = step_page(over, page, step, steps, images, body, measure)
        pages.append((text, footer, back, False))
    pages.append((checked(over, record), footer, back, False))
    return pages


def build(proof_dirs: list, out: Path, ci_url: str = "", title: str = TITLE, repo: Path = Path(__file__).parent,
          features: list = (), evidence: Path | None = None):
    """`features`: the agreed lists (docs/promises/<name>.yaml) of the features whose evidence CI laid out in
    `evidence` (proof/<release>/): the PDF opens with their verdicts and tells each before its changes."""
    QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
    proofs, sessions = read_proofs(proof_dirs)
    loaded = [load_feature(promises, evidence) for promises in features]
    sections = by_feature(proofs, loaded)
    proofs = [proof for _, part in sections for proof in part]
    records = [record for _, record in proofs]
    release = read(evidence / "release.json") if loaded else None
    version = ", ".join(sorted({r["version"] for r in records})) or str((release or {}).get("version", ""))
    since = news(repo, version)
    new = [since is not None and folder.name not in since[2] for folder, _ in proofs]

    writer = QPdfWriter(str(out))
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Landscape, QMarginsF(12, 12, 12, 12),
                                     QPageLayout.Millimeter))
    writer.setTitle(f"Taskloom: {title}")
    writer.setCreator("Taskloom packaging/proof_pdf.py")
    paper = writer.pageLayout().paintRect(QPageLayout.Inch)
    body = QSizeF(paper.width() * DPI, paper.height() * DPI - FOOTER)  # each page's text, above its footer
    font = QFont()
    font.setFamilies(FONTS)
    font.setPointSize(10)
    resources = {f"badge{n}": badge(n) for n in range(1, 21)}

    def document(text: str, paged: bool = True, scale: float = 1.0, rows: bool = False) -> QTextDocument:
        """`scale`: how much its pages are shrunk on paper (they are laid out that much bigger); `rows`: its tables
        are rows, each kept whole on one page (the cover's)."""
        doc = QTextDocument()
        doc.setDefaultFont(font)
        doc.setDocumentMargin(8)
        doc.setDefaultStyleSheet(f"body {{ color: {INK} }} p {{ margin-top: 0; margin-bottom: 4px }}")
        for name, image in resources.items():
            doc.addResource(QTextDocument.ImageResource, QUrl(name), image)
        if paged:
            doc.setPageSize(QSizeF(body.width() / scale, body.height() / scale + BLANK))
        else:
            doc.setTextWidth(body.width())
        doc.setHtml(f"<html><body>{text}</body></html>")
        if paged:  # the blank under each page: its bottom margin, which Qt leaves on every page
            frame = doc.rootFrame().frameFormat()
            frame.setBottomMargin(frame.bottomMargin() + BLANK)
            doc.rootFrame().setFrameFormat(frame)
        if rows:
            whole(doc)
        return doc

    def measure(text: str) -> float:
        return document(text, paged=False).size().height()

    def fitted(text: str, rows: bool = False) -> QTextDocument:
        """The page, shrunk as little as it takes to fit on one page fewer than its content fills, else on as many,
        with no tinted box cut by a page's end, when that is not too much: what runs on to a next page by a little
        would leave a few lines alone there, and a cut box is drawn with stray bits of its tint. (Its content is
        measured unpaged: where a page's end cuts a box, Qt adds space, so it can take a page more.) `rows`: as
        document()'s."""
        doc = document(text, rows=rows)
        fills = math.ceil(measure(text) / body.height())
        for pages in range(max(1, fills - 1), doc.pageCount() + 1):
            scale, smaller = 1.0, doc
            while scale >= MIN_SCALE:
                if smaller.pageCount() <= pages and not cuts_a_box(smaller):
                    return smaller
                scale *= 0.98
                smaller = document(text, scale=scale, rows=rows)
        return doc

    # The features' diagrams, drawn once.
    drawings = {f"c4/{name}": mermaid(evidence / "c4" / f"{name}.mmd")
                for name in ("context", "containers")} if loaded else {}
    resources.update({name: image for name, image in drawings.items() if image is not None})
    room = QSizeF(body.width() - 24, body.height() * 0.8)

    pages = []  # (html, footer, the go-back link at its top: (anchor, words), whether it is a feature's)
    starts = []  # the page each change starts on
    number = 0
    for index, part in sections:  # each feature's page, then its changes'; then the other changes'
        if index is not None:
            changes = [(number + k, record) for k, (_, record) in enumerate(part, 1)]
            pages.append((feature_page(index + 1, len(loaded), loaded[index], changes, evidence),
                          f"Feature {index + 1}: {loaded[index]['name']}",
                          (f"feature-row-{index + 1}", "Back to the summary"), True))
        for folder, record in part:
            number += 1
            starts.append(len(pages))
            pages += change_pages(number, len(proofs), folder, record, f" · {since[0]}" if new[number - 1] else "",
                                  resources, body, measure)

    # A feature's pages run on as long as they take, at their size, each table whole on a page; a change's are
    # fitted to as few pages as reads well.
    docs = [document(text, rows=True) if feature else fitted(text) for text, _, _, feature in pages]
    counts = [doc.pageCount() for doc in docs]
    first = [sum(counts[:i]) for i in range(len(counts))]
    # With features, the summary and the C4 view come first, then the list of the changes; else the cover.
    front = [fitted(text, rows=True) for text in (summary(title, loaded, release, records, sessions, ci_url),
                                                   concepts_page(loaded), c4_page(drawings, room, evidence))
             ] if loaded else []

    def listed(pages: list) -> str:
        if not loaded:
            return cover(title, records, sessions, ci_url, pages, since, new)
        return heading("It works on screen", "Every change, shown in the running app") + lists(records, pages, since,
                                                                                               new)

    cover_doc = fitted(listed([0] * len(records)), rows=True)  # its page count
    shift = sum(doc.pageCount() for doc in front) + cover_doc.pageCount()
    cover_doc = fitted(listed([shift + first[i] + 1 for i in starts]), rows=True)
    docs = [*front, cover_doc, *docs]
    footers = [""] * (len(front) + 1) + [footer for _, footer, _, _ in pages]
    backs = [None] * (len(front) + 1) + [back for _, _, back, _ in pages]

    def back_link(to: tuple | None) -> QTextDocument | None:
        """On each page of a change (of a feature), at its top: a link to its row on the cover (the summary)."""
        return document(f"<p align=\"right\" style=\"font-size: 8pt\"><a href=\"#{to[0]}\" style=\"color: "
                        f"{IDEA_BAR}\"><span style=\"{SYMBOLS}\">↑</span> {to[1]}</a></p>",
                        paged=False) if to else None

    paint(writer, list(zip(docs, footers, map(back_link, backs))), body, font, f"Taskloom {version} · {title}")


def paint(writer: QPdfWriter, docs: list, body: QSizeF, font: QFont, left: str):
    """The documents' pages, in order; on each page but the cover's, its go-back link at its top and its footer:
    `left`, its own words and its number. `docs`: (document, its footer's words, its go-back link) each."""
    total = sum(doc.pageCount() for doc, _, _ in docs)
    painter = QPainter(writer)
    painter.setRenderHint(QPainter.LosslessImageRendering)  # screenshots as sharp as on the screen, not as JPEG
    painter.scale(writer.resolution() / DPI, writer.resolution() / DPI)
    small = QFont(font)
    small.setPixelSize(11)  # in the page's pixels (a point size would count the PDF's resolution too)
    number = 0
    for doc, footer, back in docs:
        for i in range(doc.pageCount()):
            if number:
                writer.newPage()
            number += 1
            # Of a paragraph that a page starts in the middle of, Qt also draws the line before, clipped out of
            # sight: with the blank under each page, above the paper, not in its top margin under the go-back link.
            pitch = doc.pageSize().height()  # a page and the blank under it
            page = QSizeF(doc.pageSize().width(), pitch - BLANK)
            painter.save()
            painter.scale(body.width() / page.width(), body.height() / page.height())
            painter.translate(0, -i * pitch)
            doc.drawContents(painter, QRectF(0, i * pitch, page.width(), page.height()))
            painter.restore()
            if not footer:  # the cover
                continue
            painter.save()
            painter.translate(0, -back.size().height())  # its line just above the page's text, in the page's margin
            back.drawContents(painter)
            painter.restore()
            painter.setFont(small)
            painter.setPen(QColor(FAINT))
            line = QRectF(8, body.height() + 6, body.width() - 16, FOOTER - 6)
            painter.drawText(line, Qt.AlignLeft | Qt.AlignVCenter, left)
            painter.drawText(line, Qt.AlignHCenter | Qt.AlignVCenter, footer)
            painter.drawText(line, Qt.AlignRight | Qt.AlignVCenter, f"{number} / {total}")
    painter.end()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("proof_dirs", type=Path, nargs="+", metavar="proof_dir", help="where a run recorded its proofs")
    parser.add_argument("out", type=Path)
    parser.add_argument("--ci-url", default="", help="the CI run the proofs ran in")
    parser.add_argument("--title", default=TITLE)
    parser.add_argument("--repo", type=Path, default=Path(__file__).parent,
                        help="the Git checkout that says which changes are new (default: the one this script is in)")
    parser.add_argument("--feature", type=Path, action="append", default=[], metavar="PROMISES_YAML",
                        help="a feature's agreed list (docs/promises/<name>.yaml), told first; repeatable")
    parser.add_argument("--evidence", type=Path, metavar="DIR",
                        help="where CI laid out the features' evidence (proof/<release>/); needed with --feature")
    args = parser.parse_args()
    if args.feature and args.evidence is None:
        parser.error("--feature needs --evidence")
    build(args.proof_dirs, args.out, args.ci_url, args.title, args.repo, args.feature, args.evidence)
    print(args.out)


if __name__ == "__main__":
    main()
