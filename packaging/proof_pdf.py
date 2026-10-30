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
MONO = "font-family: Consolas, Menlo, 'DejaVu Sans Mono', 'Courier New'; font-size: 8pt; white-space: pre-wrap"
FOOTER = 28  # under each page's text: the footer line
MAX_ZOOM = 1.5  # a close-up is shown at most this much bigger than on the screen
MIN_SCALE = 0.5  # a page is shrunk at most this much to fit on fewer pages
GAP = 16


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


def cover(title: str, records: list, sessions: list, ci_url: str, pages: list, since: tuple | None,
          new: list) -> str:
    """`since`: what news() says; `new`: whether each change is new."""
    problems = []
    for proof_dir, session in sessions:
        if session is None:
            problems.append(f"The proof run of {proof_dir.name} did not finish: proofs may be missing.")
        elif session["exitstatus"] != 0:
            problems.append(f"The proof run on {session['system']} failed (pytest exit status "
                            f"{session['exitstatus']}): see the CI run.")
    if not records:
        problems.append("No proof was recorded.")
    if failed := sum(r["status"] != "passed" for r in records):
        problems.append(f"{failed} of {len(records)} changes did not pass their proof.")
    verdict = "<br>".join(mark(False, p, "15pt") for p in problems) or mark(
        True, "The change passed its proof." if len(records) == 1 else f"All {len(records)} changes passed their "
                                                                       "proofs.", "15pt")
    about = [("Version", ", ".join(sorted({r["version"] for r in records})) or "unknown"),
             ("Date", when(min(records, key=lambda r: r["started"])) if records else "unknown"),
             ("Platform", "; ".join(sorted({r["platform"] for r in records})) or "unknown")]
    if ci_url:
        about.append(("CI run", f"<a href=\"{html.escape(ci_url)}\" style=\"color: {IDEA_BAR}\">"
                                f"{html.escape(ci_url)}</a>"))
    facts = "".join(f"<tr><td style=\"color: {MUTED}; padding: 3px 18px 3px 0\">{name}</td>"
                    f"<td style=\"padding: 3px 0\">{value if name == 'CI run' else esc(value)}</td></tr>"
                    for name, value in about)
    cell = f"valign=\"top\" style=\"padding: 12px 0; border-top: 1px solid {RULE}"

    def row(i: int, about: str) -> str:
        """Change i's line: its number, `about` it (its title...), its result, the system it was shown on, its page."""
        r = records[i - 1]
        return (f"<tr><td width=\"5%\" {cell}; font-size: 18pt; font-weight: 600; color: {ACCENT}\">{i}</td>"
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
    listed = [
        row(i, f"<span style=\"{big}\">{esc(r['title'])}</span>{marked if new[i - 1] else ''}<br>"
               f"<span style=\"font-size: 11pt; color: {MUTED}\">{esc(r['idea'][0] if r['idea'] else '')}</span>")
        for i, r in enumerate(records, 1)]
    news_list = ""
    if since:
        heading, base, _ = since
        linked = [row(i, f"<a href=\"#change-{i}\" style=\"{big}; color: {IDEA_BAR}\">{esc(r['title'])}</a>")
                  for i, r in enumerate(records, 1) if new[i - 1]]
        news_list = listing(heading if linked else f"Nothing new since {base}", linked)
    return (label("Taskloom", ACCENT) +
            f"<p style=\"font-size: 34pt; font-weight: 600; margin-top: 0; margin-bottom: 8px\">{esc(title)}</p>"
            f"<p style=\"font-size: 13pt; color: {MUTED}; line-height: 140%; margin-bottom: 22px\">Each change in this "
            "version, shown working in the running app: what was asked, the idea behind it and why it is built that "
            "way, then the app doing it, step by step, with what was checked.</p>"
            f"<table cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top: 18px\">{facts}</table>"
            f"<table cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top: 26px\"><tr><td>{verdict}</td></tr></table>"
            f"{news_list}{listing('In this proof', listed)}")


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


def build(proof_dirs: list, out: Path, ci_url: str = "", title: str = TITLE, repo: Path = Path(__file__).parent):
    QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
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
    proofs = [(folder, record) for folder, record in proofs
              if record["status"] != "skipped" or folder.name not in shown]
    records = [record for _, record in proofs]
    version = ", ".join(sorted({r["version"] for r in records}))
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
            doc.setPageSize(body / scale)
        else:
            doc.setTextWidth(body.width())
        doc.setHtml(f"<html><body>{text}</body></html>")
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

    pages = []  # (html, footer)
    starts = []  # the page each change starts on
    for number, (folder, record) in enumerate(proofs, 1):
        images = {}
        for entry in record["entries"]:
            for file in (entry.get("file"), entry.get("zoom")):
                if entry["kind"] == "shot" and file:
                    name = f"proof{number}/{file}"
                    resources[name] = framed(QImage(str(folder / file)))
                    images[file] = (name, resources[name].size())
        marked = f" · {since[0]}" if new[number - 1] else ""
        over = f"Change {number}{marked}"
        footer = f"Change {number}: {record['title']} · on {record['system']}"
        starts.append(len(pages))
        pages += [(text, footer) for text in story(number, f"Change {number} of {len(proofs)}{marked}", record)]
        step, steps = 0, sum(e["kind"] == "step" for e in record["entries"])
        for page in groups(record["entries"]):
            text, step = step_page(over, page, step, steps, images, body, measure)
            pages.append((text, footer))
        pages.append((checked(over, record), footer))

    docs = [fitted(text) for text, _ in pages]
    counts = [doc.pageCount() for doc in docs]
    first = [sum(counts[:i]) for i in range(len(counts))]
    cover_doc = fitted(cover(title, records, sessions, ci_url, [0] * len(records), since, new),
                       rows=True)  # its page count
    shift = cover_doc.pageCount()
    cover_doc = fitted(cover(title, records, sessions, ci_url, [shift + first[i] + 1 for i in starts], since, new),
                       rows=True)
    docs, footers = [cover_doc, *docs], ["", *(footer for _, footer in pages)]
    total = sum(doc.pageCount() for doc in docs)

    painter = QPainter(writer)
    painter.setRenderHint(QPainter.LosslessImageRendering)  # screenshots as sharp as on the screen, not as JPEG
    painter.scale(writer.resolution() / DPI, writer.resolution() / DPI)
    small = QFont(font)
    small.setPixelSize(11)  # in the page's pixels (a point size would count the PDF's resolution too)
    number = 0
    for doc, footer in zip(docs, footers):
        for i in range(doc.pageCount()):
            if number:
                writer.newPage()
            number += 1
            page = doc.pageSize()
            painter.save()
            painter.scale(body.width() / page.width(), body.height() / page.height())
            painter.translate(0, -i * page.height())
            doc.drawContents(painter, QRectF(0, i * page.height(), page.width(), page.height()))
            painter.restore()
            if not footer:  # the cover
                continue
            painter.setFont(small)
            painter.setPen(QColor(FAINT))
            line = QRectF(8, body.height() + 6, body.width() - 16, FOOTER - 6)
            painter.drawText(line, Qt.AlignLeft | Qt.AlignVCenter, f"Taskloom {version} · {title}")
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
    args = parser.parse_args()
    build(args.proof_dirs, args.out, args.ci_url, args.title, args.repo)
    print(args.out)


if __name__ == "__main__":
    main()
