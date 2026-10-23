"""Make one PDF of the proofs that tests/proofs recorded (run with PROOF_DIR set; see tests/proofs/conftest.py):

    python packaging/proof_pdf.py <proof_dir> <out.pdf> [--ci-url URL] [--title TEXT]

A title page (Taskloom version, date, platform, the CI run, each proof passed or failed), then each proof: what was
asked and what was checked, then the steps with the screenshots (what to look at under each), text and notes.
"""

import argparse
import html
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # it shows no window
if sys.platform == "win32":  # Qt's offscreen platform finds no fonts there unless pointed at them
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QMarginsF, QSizeF, QUrl  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QPageLayout, QPageSize, QPainter,  # noqa: E402
                           QPdfWriter, QPen, QTextDocument)

TITLE = "Taskloom: proof that it works"
OK, FAILED, GREY = "#1a7f37", "#c62828", "#666666"
MARK = "font-family: 'Segoe UI Symbol', 'DejaVu Sans', 'Apple Symbols'; font-weight: bold"
MONO = "font-family: Consolas, Menlo, 'DejaVu Sans Mono', 'Courier New'; font-size: 8pt; white-space: pre-wrap"
CAPTION_ROOM = 170  # under and over a screenshot on its page: its step, a note, its caption (in screen pixels)


def mark(ok: bool, text: str) -> str:
    return (f"<span style=\"color: {OK if ok else FAILED}\"><span style=\"{MARK}\">{'✓' if ok else '✗'}</span> "
            f"{html.escape(text)}</span>")


def outcome(record: dict) -> str:
    if record["status"] == "passed":
        return mark(True, "passed")
    return mark(False, "not shown (skipped)" if record["status"] == "skipped" else record["status"])


def title_page(title: str, records: list, session: dict | None, ci_url: str) -> str:
    problems = []
    if session is None:
        problems.append("The proof run did not finish: proofs may be missing.")
    elif session["exitstatus"] != 0:
        problems.append(f"The proof run failed (pytest exit status {session['exitstatus']}): see the CI run.")
    if not records:
        problems.append("No proof was recorded.")
    if failed := sum(r["status"] != "passed" for r in records):
        problems.append(f"{failed} of {len(records)} proofs did not pass.")
    summary = "<br>".join(mark(False, p) for p in problems) or mark(
        True, f"All {len(records)} proofs passed." if len(records) > 1 else "The proof passed.")
    about = [("Taskloom", ", ".join(sorted({r["version"] for r in records})) or "unknown"),
             ("Date", min((r["started"] for r in records), default="").replace("T", " ")),
             ("Platform", "; ".join(sorted({r["platform"] for r in records})))]
    rows = "".join(f"<tr><td><b>{name}</b></td><td>{html.escape(value)}</td></tr>" for name, value in about)
    if ci_url:
        rows += f"<tr><td><b>CI run</b></td><td><a href=\"{html.escape(ci_url)}\">{html.escape(ci_url)}</a></td></tr>"
    listed = "".join(f"<tr><td>{i}</td><td>{html.escape(r['title'])}</td><td>{outcome(r)}</td></tr>"
                     for i, r in enumerate(records, 1))
    return (f"<h1>{html.escape(title)}</h1>"
            "<p>Each feature or fix, shown working in the running app: what was asked, what was done, what the app "
            "showed, and what was checked.</p>"
            f"<table cellpadding=\"3\">{rows}</table>"
            f"<p style=\"font-size: 14pt; margin-top: 16px\">{summary}</p>"
            "<table border=\"1\" cellspacing=\"0\" cellpadding=\"5\" width=\"100%\"><tr><th width=\"4%\">#</th>"
            f"<th align=\"left\">Proof</th><th align=\"left\" width=\"22%\">Result</th></tr>{listed}</table>")


def section(number: int, record: dict, images: dict) -> str:
    entries = record["entries"]
    checks = [e for e in entries if e["kind"] == "check"]
    parts = [f"<h2 style=\"page-break-before: always\">Proof {number}: {html.escape(record['title'])}</h2>",
             f"<p>{outcome(record)}&nbsp;&nbsp;<span style=\"color: {GREY}; font-size: 8pt\">"
             f"{html.escape(record['test'])}</span></p>",
             f"<p><b>What was asked:</b> <i>“{html.escape(record['asked'])}”</i></p>",
             "<p><b>What was checked</b> (each again below, where it was checked):</p>",
             "".join(f"<p style=\"margin-left: 16px\">{mark(e['ok'], e['text'])}</p>" for e in checks)
             or f"<p style=\"margin-left: 16px\">{mark(False, 'nothing was checked')}</p>"]
    if record["error"]:
        parts.append(f"<p style=\"color: {FAILED}\"><b>What went wrong</b></p>"
                     f"<p style=\"{MONO}\">{html.escape(record['error'])}</p>")
    step = shot = 0
    for i, entry in enumerate(entries):
        kind = entry["kind"]
        if kind == "step":
            step += 1
            # A step shown in a screenshot starts a page, so it is on the page of its screenshot.
            following = next((e["kind"] for e in entries[i + 1:] if e["kind"] in ("step", "shot")), None)
            new_page = " style=\"page-break-before: always\"" if following == "shot" else ""
            parts.append(f"<p{new_page}><b>Step {step}.</b> {html.escape(entry['text'])}</p>")
        elif kind == "shot":
            shot += 1
            name, width, height = images[entry["file"]]
            window = f" · window “{html.escape(entry['window'])}”" if entry["window"] else ""
            parts.append(f"<p align=\"center\"><img src=\"{name}\" width=\"{width}\" height=\"{height}\"></p>"
                         f"<p align=\"center\"><span style=\"color: {GREY}; font-size: 8pt\">Screenshot {shot}{window}"
                         f"</span><br>{html.escape(entry['caption'])}</p>")
        elif kind == "text":
            parts.append(f"<p>{html.escape(entry['caption'])}</p>"
                         f"<p style=\"{MONO}; background-color: #f3f3f3\">{html.escape(entry['text'])}</p>")
        elif kind == "note":
            parts.append(f"<p style=\"color: #555555\"><i>Note: {html.escape(entry['text'])}</i></p>")
        elif kind == "check":
            parts.append(f"<p>{mark(entry['ok'], entry['text'])}</p>")
    return "".join(parts)


def framed(image: QImage) -> QImage:
    """The screenshot with a thin grey frame, so it stands out from the white paper."""
    image = image.convertToFormat(QImage.Format_RGB32)
    painter = QPainter(image)
    painter.setPen(QPen(QColor("#9e9e9e"), 2))
    painter.drawRect(1, 1, image.width() - 2, image.height() - 2)
    painter.end()
    return image


def build(proof_dir: Path, out: Path, ci_url: str = "", title: str = TITLE):
    app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
    proofs = sorted(((folder, json.loads((folder / "proof.json").read_text(encoding="utf-8")))
                     for folder in (proof_dir.iterdir() if proof_dir.is_dir() else [])
                     if (folder / "proof.json").is_file()), key=lambda p: p[1]["started"])
    session_file = proof_dir / "session.json"
    session = json.loads(session_file.read_text()) if session_file.is_file() else None

    writer = QPdfWriter(str(out))
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Landscape, QMarginsF(10, 10, 10, 10),
                                     QPageLayout.Millimeter))
    writer.setTitle(title)
    writer.setCreator("Taskloom packaging/proof_pdf.py")
    # Laid out in screen pixels (what the screenshots are in), on pages the size of the paper's printable area.
    dpi = app.primaryScreen().logicalDotsPerInch()
    paper = writer.pageLayout().paintRect(QPageLayout.Inch)
    page = QSizeF(paper.width() * dpi, paper.height() * dpi)
    doc = QTextDocument()
    doc.setPageSize(page)
    doc.setDocumentMargin(6)  # also between pages: nothing of the next page shows at the bottom of one
    font = QFont()
    font.setFamilies(["Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"])
    font.setPointSize(10)
    doc.setDefaultFont(font)
    doc.setDefaultStyleSheet("p { margin-top: 4px; margin-bottom: 4px }")

    sections = []
    for number, (folder, record) in enumerate(proofs, 1):
        images = {}
        for entry in record["entries"]:
            if entry["kind"] == "shot":
                name = f"proof{number}/{entry['file']}"
                image = framed(QImage(str(folder / entry["file"])))
                doc.addResource(QTextDocument.ImageResource, QUrl(name), image)
                # As big as on the screen, or smaller to fit the page with its step and caption.
                scale = min(dpi / 96, (page.width() - 12) / image.width(),
                            (page.height() - 12 - CAPTION_ROOM) / image.height())
                images[entry["file"]] = (name, round(image.width() * scale), round(image.height() * scale))
        sections.append(section(number, record, images))
    doc.setHtml(f"<html><body>{title_page(title, [r for _, r in proofs], session, ci_url)}"
                f"{''.join(sections)}</body></html>")
    doc.print_(writer)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("proof_dir", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--ci-url", default="", help="the CI run the proofs ran in")
    parser.add_argument("--title", default=TITLE)
    args = parser.parse_args()
    build(args.proof_dir, args.out, args.ci_url, args.title)
    print(args.out)


if __name__ == "__main__":
    main()
