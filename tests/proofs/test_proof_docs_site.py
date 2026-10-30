"""Proof: the documentation website. `python packaging/docs_site.py site` writes the pages of Help > Documentation as
a website in the Read the Docs layout, with a search box that runs in the browser; the pages job of the build on
GitHub publishes it on GitHub Pages after each release. Shown in the headless Chrome every GitHub runner has, on the
site served under /taskloom/ as GitHub Pages will serve it, next to the same entry in the app's column."""

import json
import os
import re
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest
import yaml
from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QImage, QPixmap, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from proofs.conftest import ZOOM_MARGIN, menu_item
from taskloom import __version__
from taskloom.editor.canvas import HEADER_H, NODE_W
from tests.test_docs_help import anchor_at, click_learn_more, heading_at_top, hover, row_named, wait_for
from tests.test_docs_site import REMOTE_IN_CSS, REMOTE_IN_JS, Html, as_list, chrome, is_remote, resolve
from tests.test_editor import write_flow

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "build.yml"
SITE_URL = "https://lben.github.io/taskloom/"
GITHUB = "https://github.com/lben/taskloom"
DESKTOP, PHONE = (1280, 1000), (390, 844)  # Chrome's window, in pixels
ASKED = ("We should maintain a clean, clear, simple to follow, comprehensive and always up to date documentation "
         "website (very similar to https://about.readthedocs.com/). — then, asked where it should be and how it "
         "should look: GitHub Pages, and the usual Read the Docs layout with a search box")
FLOW = """
    blocks:
      sales_chart:
        type: reports.chart
        config: {kind: bar, x: region, y: [amount], title: Sales by region}
        ui: {x: 0, y: 0}
    edges: []
"""

# The page the proof works the site from, around the site's page (it is not part of the site): it opens the page in
# a frame as big as Chrome's window, types and clicks in it as the page's own events from the keyboard and the mouse
# would, then says where things are and what they say. Chrome's screenshot is then the site's page, as it shows.
PROBE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; overflow: hidden; background: #fff; } iframe { border: 0; display: block; }
</style></head><body><script>
const wait = ms => new Promise(done => setTimeout(done, ms));
const box = el => {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return [r.left, r.top, r.width, r.height];
};
const around = els => {
  const rs = els.filter(Boolean).map(el => el.getBoundingClientRect());
  const left = Math.min(...rs.map(r => r.left)), top = Math.min(...rs.map(r => r.top));
  return [left, top, Math.max(...rs.map(r => r.right)) - left, Math.max(...rs.map(r => r.bottom)) - top];
};
const link = a => a && {box: box(a), text: a.textContent.trim(), href: a.getAttribute('href')};
function section(heading) {  // a heading and what follows it, up to the next heading with an id: as the search has it
  const parts = [heading];
  for (let n = heading.nextElementSibling; n && !(/^H[1-6]$/.test(n.tagName) && n.id); n = n.nextElementSibling)
    parts.push(n);
  return parts;
}
function look(frame) {
  const win = frame.contentWindow, doc = frame.contentDocument, nav = doc.querySelector('nav');
  const current = nav.querySelector('a[aria-current]'), sections = current && current.nextElementSibling;
  const id = decodeURIComponent(win.location.hash.slice(1)), target = id && doc.getElementById(id);
  const parts = target ? section(target) : [], footer = doc.querySelector('footer');
  return {
    location: win.location.pathname + win.location.hash, width: win.innerWidth,
    sidebar: box(nav), brand: box(nav.querySelector('.brand')), title: nav.querySelector('.brand a').textContent,
    search: box(doc.getElementById('docs-search')),
    results: [...doc.querySelectorAll('#docs-results li')].map(li => {
      const a = li.querySelector('a');
      return {box: box(li), text: li.innerText.split('\n').map(s => s.trim()).filter(Boolean),
              href: a ? a.getAttribute('href') : ''};
    }),
    lists: [...nav.querySelectorAll('.caption')].map(c => ({
      caption: c.textContent, box: around([c, c.nextElementSibling]),
      pages: [...c.nextElementSibling.children].map(li => li.firstElementChild.textContent)})),
    order: [...nav.querySelectorAll('a')].filter(a => !a.closest('.sections, #docs-results, .brand'))
      .map(a => a.getAttribute('href')),
    current: link(current),
    sections: sections && sections.matches('ul.sections')
      ? {box: box(sections), hrefs: [...sections.querySelectorAll('a')].map(a => a.getAttribute('href'))} : null,
    content: box(doc.querySelector('article')), heading: link(doc.querySelector('article h1')),
    previous: link(doc.querySelector('.pager a[rel=prev]')), next: link(doc.querySelector('.pager a[rel=next]')),
    footer: {box: box(footer), text: footer.textContent, links: [...footer.querySelectorAll('a')].map(a => a.href)},
    target: target ? {id, heading: box(target), box: around(parts), under: around(parts.slice(1)),
                      text: parts.map(p => p.innerText).join('\n')} : null,
    loaded: [win.location.href, ...win.performance.getEntriesByType('resource').map(e => e.name)],
  };
}
(async () => {
  let said;
  try {
    const ask = JSON.parse(new URLSearchParams(location.search).get('ask'));
    const frame = document.createElement('iframe');
    frame.style.width = ask.width + 'px';
    frame.style.height = ask.height + 'px';
    document.body.appendChild(frame);
    const loaded = () => new Promise(done => { frame.onload = done; });
    let next = loaded();
    frame.src = ask.page;
    await next;
    await wait(500);
    for (const [what, arg] of ask.steps) {
      const doc = frame.contentDocument;
      if (what === 'type') {  // click into the search box, and type
        const input = doc.getElementById('docs-search');
        input.focus();
        input.value = arg;
        input.dispatchEvent(new Event('input', {bubbles: true}));
      } else if (what === 'click') {
        const a = doc.querySelector(arg);
        if (!a) throw new Error('nothing to click: ' + arg);
        next = loaded();
        a.click();
        await Promise.race([next, wait(5000)]);
      } else if (what === 'end') {  // scroll to the end of the page
        frame.contentWindow.scrollTo(0, doc.documentElement.scrollHeight);
      }
      await wait(500);
    }
    said = look(frame);
  } catch (e) { said = {error: String(e && e.stack || e)}; }
  await fetch('__result', {method: 'POST', body: JSON.stringify(said)});
})();
</script></body></html>"""


class Served:
    """The site, served as GitHub Pages will serve it: under /taskloom/, by a plain static file server on this
    computer. At /taskloom/__probe.html, the proof's own page (PROBE). What Chrome asks for is recorded."""

    def __init__(self, site: Path):
        self.reports, self.asked = [], []
        served = self

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kw):
                super().__init__(*args, directory=str(site), **kw)

            def translate_path(self, path):  # only under /taskloom/, as on GitHub Pages
                return super().translate_path(path[len("/taskloom"):] if path.startswith("/taskloom/") else "/-/none")

            def do_GET(self):
                if self.path.split("?")[0] == "/taskloom/__probe.html":
                    self._send(PROBE.encode("utf-8"))
                else:
                    super().do_GET()

            def do_POST(self):
                served.reports.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")))
                self._send(b"")

            def _send(self, body):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_request(self, code="-", size="-"):
                path = self.path.split("?")[0]
                # (not the proof's own page and report, nor the icon Chrome asks any site for by itself)
                if path not in ("/taskloom/__probe.html", "/taskloom/__result", "/favicon.ico"):
                    served.asked.append((path, int(code)))

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.root = f"http://127.0.0.1:{self.server.server_address[1]}/taskloom/"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)

    def in_chrome(self, browser: str, picture: Path, page: str, size: tuple, steps=()) -> dict:
        """Open `page` in headless Chrome, its window `size`; do `steps` (("type", words), ("click", a CSS selector),
        ("end",)); Chrome's screenshot into `picture`. What the page then showed, and where."""
        self.reports.clear()
        ask = {"page": page, "width": size[0], "height": size[1], "steps": [list(s) for s in steps]}
        done = subprocess.run([browser, "--headless", "--disable-gpu", "--no-sandbox", "--no-first-run",
                               "--no-default-browser-check", "--disable-extensions", "--hide-scrollbars",
                               "--force-device-scale-factor=1", "--blink-settings=preferredColorScheme=1",
                               f"--user-data-dir={picture.parent / (picture.stem + '-profile')}",
                               f"--window-size={size[0]},{size[1]}", "--virtual-time-budget=30000",
                               f"--screenshot={picture}", self.root + "__probe.html?ask=" + quote(json.dumps(ask))],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        assert self.reports, f"Chrome reported nothing (exit {done.returncode}): {done.stderr[-2000:]}"
        said = self.reports[0]
        assert "error" not in said, said.get("error")
        assert picture.is_file(), f"Chrome took no screenshot (exit {done.returncode}): {done.stderr[-2000:]}"
        return said


def rect(found, image: QRect = None) -> QRect:
    """A [left, top, width, height] Chrome measured as a rectangle of its screenshot (cut to it, when given)."""
    r = QRect(round(found[0]), round(found[1]), round(found[2]), round(found[3]))
    return r & image if image is not None else r


def picture(qtbot, path: Path) -> QLabel:
    """A window that shows Chrome's screenshot, pixel for pixel (its pixels are the screenshot's)."""
    label = QLabel()
    qtbot.addWidget(label)
    label.setPixmap(QPixmap(str(path)))
    label.setFixedSize(label.pixmap().size())
    label.show()
    QTest.qWait(50)
    return label


def side_by_side(parts: list[tuple[str, QImage]]) -> tuple[QWidget, list[QLabel]]:
    """A window that shows `parts` (a title and a picture each) side by side; and the pictures' labels."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setSpacing(28)
    shown = []
    for title, image in parts:
        column = QVBoxLayout()
        column.addWidget(QLabel(f"<b>{title}</b>"))
        label = QLabel()
        label.setPixmap(QPixmap.fromImage(image))
        label.setFixedSize(image.size())
        column.addWidget(label)
        column.addStretch()
        layout.addLayout(column)
        shown.append(label)
    row.show()
    QTest.qWait(50)
    return row, shown


def entry_lines(browser, anchor: str, until: str) -> tuple[QRect, str]:
    """The entry of the column's page from its heading (`anchor`) to the next entry (`until`): where its lines are, in
    the pixels of the column's page, and its text."""
    start, end = anchor_at(browser, anchor), anchor_at(browser, until)
    assert start is not None and end is not None, (anchor, until)
    end.movePosition(QTextCursor.PreviousCharacter)
    top, bottom = browser.cursorRect(start), browser.cursorRect(end)
    area = QRect(0, top.top() - 6, browser.viewport().width(), bottom.bottom() - top.top() + 12)
    start.setPosition(end.position(), QTextCursor.KeepAnchor)
    return area & browser.viewport().rect(), start.selectedText()


def words(text: str) -> str:
    return " ".join(text.split())  # (split() also splits at the paragraph breaks of Qt's selected text)


def links_and_anchors(site: Path) -> dict:
    """Every link of every page, and of the search's list of sections: each followed to its file and its anchor."""
    files = {p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file()}
    parsed = {name: Html((site / name).read_text(encoding="utf-8")) for name in sorted(files) if name.endswith(".html")}
    found = {"links": 0, "anchors": 0, "outside": set(), "problems": []}

    def follow(where: str, page: str, href: str):
        target = resolve(href, page)
        if target is None:
            found["outside"].add(href)
            return
        found["links"] += 1
        file, anchor = target
        found["anchors"] += bool(anchor)
        if file not in files:
            found["problems"].append(f"{where}: {href} leads to no file of the site")
        elif anchor and anchor not in parsed[file].ids:
            found["problems"].append(f"{where}: {href}: {file} has no {anchor}")

    for name, page in parsed.items():
        found["problems"] += [f"{name}: the id {i} is there {page.ids.count(i)} times"
                              for i in sorted(set(page.ids)) if page.ids.count(i) > 1]
        for link in page.links:
            follow(name, name, link.href)
        for tag, a in page.tags:
            if tag in ("script", "link", "img") and a.get("src", a.get("href")):
                follow(name, name, a.get("src", a.get("href")))
    index = json.loads(re.match(r"const DOCS_INDEX = (.*);\n", (site / "search.js").read_text(encoding="utf-8"))[1])
    for href, *_ in index:
        follow("the search's list", "index.html", href)
    found["sections"] = len(index)
    found["pages"] = len(parsed)
    return found


def loaded_from_elsewhere(site: Path) -> list[str]:
    """What a page, a style or a script of the site would load from another site (as tests/test_docs_site.py)."""
    found = []
    for path in sorted(site.rglob("*")):
        name, text = path.name, path.read_text(encoding="utf-8") if path.is_file() else ""
        if name.endswith(".js"):
            found += [f"{name}: {m[0]}" for m in REMOTE_IN_JS.finditer(text)]
        elif name.endswith(".html"):
            page = Html(text)
            found += [f"{name}: <{tag} {key}={a[key]}>" for tag, a in page.tags for key in ("src", "href", "data")
                      if tag in ("script", "link", "img", "iframe", "source", "embed", "object", "audio", "video")
                      and is_remote(a.get(key, ""))]
            found += [f"{name}: {m[0]}" for css in page.styles for m in REMOTE_IN_CSS.finditer(css)]
            found += [f"{name}: {m[0]}" for _, js in page.scripts for m in REMOTE_IN_JS.finditer(js)]
    return found


def pages_job() -> str:
    """The pages job of the build on GitHub, as written, with the comment above it."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = end = lines.index("  pages:")
    while lines[start - 1].lstrip().startswith("#"):
        start -= 1
    while end + 1 < len(lines) and not re.match(r"  \S", lines[end + 1]):
        end += 1
    return "\n".join(line[2:] for line in lines[start:end + 1]).rstrip()


def open_flow(win, monkeypatch, path):
    """File > Open…, choosing `path` in the file dialog: the tab it opens in."""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    menu_item(file_menu, "Open…").trigger()
    QApplication.processEvents()
    return win.current


def click_block(win, block: str):
    """Click a block's title bar on the canvas, as you would."""
    tab = win.current
    at = tab.view.mapFromScene(tab.scene.nodes[block].mapToScene(QPointF(NODE_W / 2, HEADER_H / 2)))
    QTest.mouseClick(tab.view.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QApplication.processEvents()
    assert win.properties.block_id == block, f"{block} is not the block Properties shows"


def test_the_documentation_website(proof, win, qtbot, tmp_path, monkeypatch):
    p = proof("The documentation website, in the Read the Docs layout, with search", ASKED)
    p.idea("A documentation website in the Read the Docs layout, with a search box, made from the very pages of Help "
           "> Documentation; GitHub publishes it at https://lben.github.io/taskloom/ after each release.")
    p.idea("One command, python packaging/docs_site.py site, writes the website into the folder site: the guide (the "
           "README, a page for each of its sections), the Properties panel page and a page for each kind of block, "
           "under the same names and with the same anchors as in the app's Documentation column. It documents the "
           "blocks that come with Taskloom, not those in the blocks folder of the computer that makes it.")
    p.idea("Every page has the same frame. On the left, a sidebar: Taskloom and its version, the search box, "
           "Contents, the Guide and the Reference lists, and under the page you are on, its sections. On the right, "
           "the page, in a column about 860 pixels wide, then links to the previous and the next page, and a footer "
           "with the version and a link to Taskloom on GitHub. On a narrow screen, such as a phone's, the sidebar "
           "goes above the page.")
    p.idea("The search runs in your browser, with no server: the website comes with a list of every section of every "
           "page, its heading and its text. As you type, it shows the sections that have all the words you typed, "
           "those with more of them in their heading first; Enter opens the first. It searches the website's pages, "
           "not the app.")
    p.idea("After each release (a v tag), a job of the build on GitHub, pages, makes the website the same way and "
           "publishes it on GitHub Pages. Nothing waits for that job, so it can never hold up a release. The website "
           "first goes live at the next release, once you have switched the repository's Settings > Pages > Source "
           "to GitHub Actions (once); until then that job fails on its own, and the release is made all the same.")
    p.why_simplest("The website is the app's documentation in a frame: each page is the column's page, unchanged, "
                   "inside one page layout written in the script itself. So the website, the column and the cards' "
                   "Learn more cannot disagree, and there is nothing more to keep up to date: change the README or a "
                   "block's help, and the next release's website says so.")
    p.why_simplest("It is one short script that adds nothing to install: besides Taskloom's own code, which makes the "
                   "pages, it uses only Python's own library. No website generator, no theme to download. The search "
                   "is one more file the script writes: the list of sections, and the few lines that look through it "
                   "in the browser.")
    p.why_simplest("Every link between its pages is relative, so the same files work under /taskloom/ on GitHub "
                   "Pages, from a folder on your disk, or at any other address.")
    p.alternative("Sphinx or MkDocs, the tools Read the Docs websites are usually made with",
                  "They make their pages from source files of their own. The reference, which the app makes from the "
                  "blocks, would have to be made a second time for them and kept in step with the app's, and the "
                  "build would need a new tool and a theme. The pages already existed: they only needed a frame.")
    p.alternative("Hosting it on Read the Docs",
                  "Another service, with an account and a configuration of its own, that builds the website on its "
                  "servers. You chose GitHub Pages: the same GitHub build that makes the releases publishes it.")
    p.alternative("A search service, such as Algolia's DocSearch",
                  "The website would load a script from another site and send it what you type, and it needs an "
                  "account. The website is small (step 1 lists its files): the list of every section of every page "
                  "is one file of the website, searched in the browser in an instant.")
    p.alternative("Publishing it on every change, rather than from each release",
                  "It would describe changes no release has yet. Published from the release, it matches the version "
                  "you can download.")
    p.alternative("Making the release wait for the website",
                  "A problem publishing the website (before Pages is switched on, say) would hold up the release "
                  "itself. As it is, nothing waits for the pages job: at worst, the website stays as it was.")
    p.fits("The anchors are the app's: a block is blocks-<kind of block>.html#<block type>, and its setting "
           "#<block type>.<setting>, as blocks-reports.html#reports.chart.kind. What Learn more opens in the column is "
           "the same address on the website.")
    p.fits("The README stays the guide's one source, as in the app; the reference is made from the blocks' own help "
           "and examples, the ones the cards of the Properties tab show.")
    p.fits("Every build on GitHub makes the website as the pages job does, follows every link and anchor, checks that "
           "nothing is loaded from another site, and tries its layout and search in Chrome, as this proof does. The "
           "pages job is set up like the build's other jobs (uv, Python 3.12), and comes after the release job, as "
           "the release comes after the proofs.")

    # --- making it -----------------------------------------------------------------------------------------
    p.step("Make the website: run python packaging/docs_site.py site, in Taskloom's folder, as the pages job does.")
    site = tmp_path / "site"
    made = subprocess.run([sys.executable, "packaging/docs_site.py", str(site)], cwd=REPO,
                          env={**os.environ, "TASKLOOM_HOME": str(tmp_path / "builder-home")},
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    p.check("It runs without an error", made.returncode == 0)
    files = sorted(f.relative_to(site).as_posix() for f in site.rglob("*") if f.is_file())
    p.text("What it printed, and the files in the folder", made.stdout.strip() + "\n\n" + ", ".join(files))
    app = win.docs.browser.pages()  # the pages of the app's Documentation column
    p.check(f"It wrote a page for each of the {len(app)} pages of the app's Documentation column, under the same "
            "names, and search.js, the search", set(files) == set(app) | {"search.js"})
    p.check("Each page holds the column's page unchanged: the same text, the same anchors",
            all(app[name] in (site / name).read_text(encoding="utf-8") for name in app))

    p.step("In the app, choose Help > Documentation: the column it opens shows the pages the website is made from.")
    help_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")
    menu_item(help_menu, "Documentation").trigger()
    column = win.docs.browser
    wait_for(qtbot, lambda: win.docs.isVisible() and column.source().fileName() == "index.html",
             "Help > Documentation did not open the column at Contents")
    qtbot.wait(200)  # (the column takes its width from the Inspector once it shows)
    top = QRect(win.docs.mapTo(win, QPoint(0, 0)), QSize(win.docs.width(), 400))
    p.shot(win, "① The app's Documentation column, at the far right, opens at Contents: the README's introduction, "
                "then the Guide and the Reference. The website has these same pages.",
           highlight=[win.docs], badges=True, zoom=top)
    p.check("① The column shows Contents, the page the website has as index.html",
            column.source().toString() == "index.html")

    # --- the layout at a desktop's width -----------------------------------------------------------------
    p.step(f"Serve the folder under /taskloom/, as GitHub Pages will, and open it in Chrome, {DESKTOP[0]} by "
           f"{DESKTOP[1]} pixels.")
    p.note("The proof uses the headless Chrome that GitHub's Windows and Linux runners come with, and a plain file "
           "server on this computer, at http://127.0.0.1:<port>/taskloom/ as the website will be at "
           "https://lben.github.io/taskloom/. For each picture a fresh Chrome opens the step's page, in light colours "
           "(the website also follows dark mode), in a page of the proof's own as big as Chrome's window; a small "
           "script there clicks a link as the mouse would, scrolls, and puts words in the search box with the event "
           "your typing gives it. Each picture is Chrome's own screenshot; the proof adds the outlines.")
    try:
        browser = chrome()
    except pytest.skip.Exception:  # (on a developer's computer without Chrome; on CI, chrome() fails)
        browser = None
    p.check("Chrome is on this computer, to show the website in", browser)
    served = Served(site)
    try:
        shots = tmp_path / "chrome"
        shots.mkdir()
        loaded = []  # every address Chrome loaded, in the website's pages

        def with_chrome(name, page, size=DESKTOP, steps=()):
            said = served.in_chrome(browser, shots / f"{name}.png", page, size, steps)
            loaded.extend(said["loaded"])
            return said, shots / f"{name}.png"

        contents, path = with_chrome("contents", "index.html")
        shown = picture(qtbot, path)
        whole = shown.rect()
        guide, reference = contents["lists"]
        brand, current = rect(contents["brand"]), rect(contents["current"]["box"])
        # (the close-up's frame ends between Contents and the Guide's caption, not across the caption's text)
        gap = (current.bottom() + guide["box"][1]) // 2
        close_up = QRect(brand.topLeft(), QPoint(brand.right(), gap - ZOOM_MARGIN))
        p.shot(shown, f"Contents, in the Read the Docs layout: ① {contents['title']} and the search box; ② Contents, "
                      "highlighted, the page you are on; ③ the Guide; ④ the Reference; ⑤ the page itself.",
               highlight=[brand, current, rect(guide["box"], whole), rect(reference["box"], whole),
                          rect(contents["content"], whole)],
               badges=True, zoom=close_up)
        shown.close()
        index = Html(app["index.html"])
        app_guide = [norm for link in index.links if (norm := "".join(link.text).strip())
                     and re.fullmatch(r"[\w-]+\.html", link.href) and link.href != "properties.html"
                     and not link.href.startswith("blocks-")]
        p.check(f"① It says Taskloom {__version__}, with a search box",
                contents["title"] == f"Taskloom {__version__}" and contents["search"] is not None)
        p.check("② Contents is highlighted", contents["current"]["text"] == "Contents")
        p.check(f"③ The Guide: the README's {len(app_guide)} sections, in the app's order",
                guide["caption"] == "Guide" and guide["pages"] == app_guide)
        app_reference = ["".join(link.text).strip() for link in index.links
                         if re.fullmatch(r"properties\.html|blocks-[\w-]+\.html", link.href)]
        p.check(f"④ The Reference: Properties panel and the {len(app_reference) - 1} kinds of block, in the app's "
                "order",
                reference["caption"] == "Reference"
                and reference["pages"] == app_reference and app_reference[0] == "Properties panel")
        p.check("⑤ The page is right of the sidebar, in a column about 860 pixels wide",
                contents["sidebar"][0] == 0 and contents["sidebar"][2] <= contents["content"][0]
                and 600 <= contents["content"][2] <= 861)

        p.step("Click Servers, under Reference.")
        servers, path = with_chrome("servers", "index.html", steps=[("click", 'nav a[href="blocks-servers.html"]')])
        shown = picture(qtbot, path)
        whole = shown.rect()
        listed = rect(servers["sections"]["box"], whole)
        p.shot(shown, "The Servers page: ① Servers is highlighted in the sidebar, and ② under it are its sections, "
                      "each block, then each of its settings; ③ the page, the same as in the app's column.",
               highlight=[rect(servers["current"]["box"]), listed, rect(servers["content"], whole)],
               badges=True, zoom=[rect(servers["current"]["box"]), listed])
        shown.close()
        own = [i for tag, a in Html(app["blocks-servers.html"]).tags if tag in ("h2", "h3") for i in [a.get("id")] if i]
        p.check("The link opens the Servers page", servers["location"] == "/taskloom/blocks-servers.html"
                and servers["heading"]["text"] == "Servers")
        p.check("① Servers is the page highlighted", servers["current"]["text"] == "Servers")
        p.check(f"② Under it, the sidebar lists the page's {len(own)} sections, in the page's order: its blocks, each "
                "followed by its settings", servers["sections"]["hrefs"] == [f"blocks-servers.html#{i}" for i in own])

        p.step("Scroll to the end of the page.")
        end, path = with_chrome("servers-end", "blocks-servers.html", steps=[("end",)])
        shown = picture(qtbot, path)
        pager = rect(end["previous"]["box"]) | rect(end["next"]["box"])
        p.shot(shown, f"At the end of the page: ① {end['previous']['text']} and {end['next']['text']}, the pages "
                      "before and after Servers in the sidebar; ② the footer, with the version and a link to Taskloom "
                      "on GitHub.",
               highlight=[pager, rect(end["footer"]["box"])], badges=True,
               zoom=[pager, rect(end["footer"]["box"])])
        shown.close()
        at = end["order"].index("blocks-servers.html")
        p.check(f"① ← Previous leads to {end['order'][at - 1]} and Next → to {end['order'][at + 1]}: the pages before "
                "and after Servers in the sidebar",
                (end["previous"]["href"], end["next"]["href"]) == (end["order"][at - 1], end["order"][at + 1]))
        p.check(f"② The footer says Taskloom {__version__} and links to Taskloom on GitHub, {GITHUB}",
                f"Taskloom {__version__}" in end["footer"]["text"] and GITHUB in end["footer"]["links"])

        # --- at a phone's width ----------------------------------------------------------------------------
        p.step(f"Open the same page at a phone's width: Chrome's window {PHONE[0]} by {PHONE[1]} pixels.")
        phone, path = with_chrome("phone", "blocks-servers.html", PHONE)
        shown = picture(qtbot, path)
        p.shot(shown, "On a phone: ① the sidebar is on top, the whole width of the screen; ② the page comes under it.",
               highlight=[rect(phone["sidebar"]), rect(phone["content"], shown.rect())], badges=True)
        shown.close()
        p.check(f"① The sidebar takes the whole width ({PHONE[0]} pixels), and ② the page starts under it",
                phone["width"] == PHONE[0] and phone["sidebar"][2] == PHONE[0]
                and phone["sidebar"][1] + phone["sidebar"][3] <= phone["heading"]["box"][1])
        p.note("Chrome's window is as narrow as a phone's screen, which is what the layout goes by; the proof does not "
               "imitate a phone's touch screen.")

        # --- the search --------------------------------------------------------------------------------
        p.step("Open the Schedules page, click the search box and type Remote Extract.")
        p.note("The search looks through the website's pages, their headings and their text: it does not search the "
               "app.")
        found, path = with_chrome("remote-extract", "schedules.html", steps=[("type", "Remote Extract")])
        p.check(f"② The search shows {len(found['results'])} sections: "
                + "; ".join(" on ".join(r["text"]) for r in found["results"]), found["results"])
        shown = picture(qtbot, path)
        first = found["results"][0]
        results = rect(first["box"]) | rect(found["results"][-1]["box"])
        p.shot(shown, "As you type in ① the search box, ② the sections that have both words show under it, each "
                      "with its page. The first is Remote Extract, the block's own entry, on the Servers page.",
               highlight=[rect(found["search"]), results], badges=True, zoom=[rect(found["brand"]), results])
        shown.close()
        p.check("② The first is Remote Extract, on the Servers page, and links to its entry: "
                "blocks-servers.html#servers.remote_extract",
                first["text"] == ["Remote Extract", "Servers"]
                and first["href"] == "blocks-servers.html#servers.remote_extract")

        p.step("Type histogram instead: a word only the help of a setting has.")
        chart = win.registry.get("reports.chart")
        kind = chart.config["kind"]
        found, path = with_chrome("histogram", "schedules.html", steps=[("type", "histogram")])
        shown = picture(qtbot, path)
        hit = next((r for r in found["results"] if r["href"] == "blocks-reports.html#reports.chart.kind"), None)
        p.check("A result links to the entry of Chart's Kind, on the Reports page: "
                "blocks-reports.html#reports.chart.kind", hit is not None)
        p.shot(shown, "① histogram in the search box; ② the one section that has it: Kind, on the Reports page, the "
                      "setting of the Chart block whose help says “… or a histogram (hist)”.",
               highlight=[rect(found["search"]), rect(hit["box"])], badges=True,
               zoom=[rect(found["brand"]), rect(hit["box"])])
        shown.close()
        p.check("② It is the one section found, and by its text: the word is in Kind's help, not in its name or in "
                "Chart's", found["results"] == [hit] and hit["text"] == ["Kind", "Reports"]
                and "histogram" in kind.help and "histogram" not in f"kind {chart.title}".lower())

        p.step("Click Kind, in the results.")
        landed, path = with_chrome("kind", "schedules.html", steps=[
            ("type", "histogram"), ("click", '#docs-results a[href="blocks-reports.html#reports.chart.kind"]')])
        shown = picture(qtbot, path)
        whole = shown.rect()
        on_site = rect(landed["target"]["box"], whole)
        p.shot(shown, "Kind, Chart's setting, is at the top of the Reports page, with ① what it is for, its name in "
                      "the flow file and its choices. ② Reports is highlighted in the sidebar.",
               highlight=[rect(landed["target"]["under"], whole), rect(landed["current"]["box"], whole)], badges=True,
               zoom=on_site)
        site_picture = QImage(str(path)).copy(on_site.adjusted(-16, -12, 16, 12) & whole)
        shown.close()
        p.check("The address is blocks-reports.html#reports.chart.kind, under /taskloom/",
                landed["location"] == "/taskloom/blocks-reports.html#reports.chart.kind")
        p.check("Kind's heading is at the top of the page", 0 <= landed["target"]["heading"][1] <= 40)
        p.check("② Reports is the page highlighted", landed["current"]["text"] == "Reports")
    finally:
        served.close()

    # --- the same entry in the app ------------------------------------------------------------------------------
    p.step("In the app, open a flow with a Chart block, click it, rest the mouse on its Kind and click Learn more › on "
           "the card.")
    p.note("The proof rests the mouse on Kind as Qt does: it moves the mouse there, then gives the row the event Qt "
           "gives it once the mouse has stayed for the usual tooltip delay. It clicks Learn more with Qt's mouse "
           "events, moving along the card's last line until the link lights up.")
    flow = write_flow(tmp_path / "sales-chart.yaml", FLOW)
    tab = open_flow(win, monkeypatch, flow)
    p.check("The flow opens", tab is not None and tab.doc.path == flow.resolve())
    click_block(win, "sales_chart")
    win.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=3_000)
    card = hover(qtbot, win, row_named(win, "Kind", "", chart).inputs[0], "Kind", "Chart › Kind")
    href = click_learn_more(card)
    wait_for(qtbot, lambda: heading_at_top(column, "reports.chart.kind"),
             "Kind's entry is not at the top of the column")
    ids = Html(app["blocks-reports.html"]).ids
    lines, in_app = entry_lines(column, "reports.chart.kind", ids[ids.index("reports.chart.kind") + 1])
    in_window = lines.translated(column.viewport().mapTo(win, QPoint(0, 0)))
    p.shot(win, "① The app's Documentation column, at the far right, opens at ② Kind: the entry the website showed.",
           highlight=[win.docs, in_window], badges=True, zoom=in_window)
    p.check("Learn more › leads to blocks-reports.html#reports.chart.kind: the address the website showed, after "
            "/taskloom/", href == "blocks-reports.html#reports.chart.kind"
            and landed["location"] == "/taskloom/" + href)
    p.check("② The column shows Kind's entry, its heading at the top", column.source().toString() == href
            and heading_at_top(column, "reports.chart.kind"))

    p.step("Compare them side by side: Kind on the website, and Kind in the app's column.")
    row, pictures = side_by_side([("On the website, in Chrome", site_picture),
                                  ("In the app's Documentation column", column.viewport().grab().toImage()
                                   .copy(lines.adjusted(0, -6, 0, 6) & column.viewport().rect()))])
    qtbot.addWidget(row)
    p.shot(row, "The same entry: ① on the website, ② in the app. The same heading, the same words, the same choices; "
                "only the fonts and the width differ. (The proof puts the two side by side: Chrome's picture once "
                "you clicked Kind, and the column once you clicked Learn more.)", highlight=pictures, badges=True)
    row.close()
    p.check("① and ② say the same, word for word: “" + words(in_app) + "”",
            words(landed["target"]["text"]) == words(in_app))
    p.check("Both are at the anchor reports.chart.kind of the page blocks-reports.html",
            landed["target"]["id"] == "reports.chart.kind" and column.source().fileName() == "blocks-reports.html"
            and column.source().fragment() == "reports.chart.kind")

    # --- every link, and nothing from another site ------------------------------------------------------------
    p.step("Follow every link of every page, and of the search's list of sections; look at what Chrome loaded.")
    checked = links_and_anchors(site)
    elsewhere = loaded_from_elsewhere(site)
    asked = sorted({path for path, _ in served.asked})
    missing = {path for path, code in served.asked if code not in (200, 304)}  # (304: Chrome had it already)
    p.text("What the proof found",
           f"{checked['pages']} pages, and {checked['sections']} sections in the search's list.\n"
           f"{checked['links']} links within the website, {checked['anchors']} of them to an entry (an anchor): "
           f"{len(checked['problems'])} broken.\n"
           f"{len(elsewhere)} things that a page, a style or a script loads from another site.\n\n"
           "The links that leave the website (they open another site when you click them):\n"
           + "\n".join(sorted(checked["outside"])) + "\n\n"
           "What Chrome asked the file server for, in all the steps above:\n"
           + "\n".join(f"{path}  {'NOT FOUND' if path in missing else 'found'}" for path in asked)
           + ("\n\n" + "\n".join(checked["problems"] + elsewhere) if checked["problems"] or elsewhere else ""))
    p.check(f"Every link and anchor resolves: each of the {checked['links']} links leads to a file of the website, "
            f"and each of the {checked['anchors']} anchors to an entry on its page; no id is there twice",
            checked["problems"] == [])
    p.check("Nothing is loaded from another site: no page, style or script asks for anything from one, and "
            f"everything Chrome loaded came from the file server, under /taskloom/ ({len(set(loaded))} addresses)",
            elsewhere == [] and loaded and all(url.startswith(served.root) for url in loaded))
    p.check("Every file Chrome asked for is under /taskloom/, and was found",
            asked and not missing and all(path.startswith("/taskloom/") for path in asked))

    # --- publishing it ------------------------------------------------------------------------------------------
    p.step("Read the pages job, in the build on GitHub.")
    p.text("The pages job, in the build on GitHub (.github/workflows/build.yml)", pages_job())
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    job = jobs["pages"]
    runs = [step.get("run", "") for step in job["steps"]]
    upload = next(step for step in job["steps"]
                  if str(step.get("uses", "")).startswith("actions/upload-pages-artifact"))
    p.check("It runs only for a tag that starts with v, a release: if: startsWith(github.ref, 'refs/tags/v')",
            job.get("if", "").replace(" ", "") == "startsWith(github.ref,'refs/tags/v')")
    waiting = [name for name, other in jobs.items() if "pages" in as_list(other.get("needs"))]
    p.check("It runs after the release job (needs: release), and no job waits for it",
            job.get("needs") == "release" and waiting == [])
    p.check("It makes the website with the command of the first step, and publishes that folder, site",
            "uv run python packaging/docs_site.py site" in runs and upload["with"]["path"] == "site")
    p.note("This proof cannot show the website on GitHub Pages: the pages job runs only when a version is released, "
           f"so its first real run is at the next release; the website will then be at {SITE_URL}. Before it, switch "
           "the repository's Settings > Pages > Source to GitHub Actions, once. Until then the pages job fails on its "
           "own, and the release is made all the same, since nothing waits for it. (If GitHub then refuses to publish "
           "from a tag, also add the tag rule v* once, under Settings > Environments > github-pages.)")
