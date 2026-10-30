"""The documentation website (documentation, phase 2). `python packaging/docs_site.py OUT` writes a static site in the
usual Read the Docs layout, from the same pages, file names and anchors as the in-app Documentation column
(taskloom.editor.docs.pages over the built-in blocks, discover(None)), with the standard library (no Jinja2) and
nothing loaded from another site. The pages job of .github/workflows/build.yml publishes it on GitHub Pages
(https://lben.github.io/taskloom/) from each v* release tag, after the release job, and nothing waits for it.

The site is built once for this module, as CI builds it (from the repository root), with Jinja2 unimportable and a
Taskloom home whose blocks folder holds blocks of the user's own (the site documents the built-in blocks only).

What the tests read of the layout, as HTML marks it: the sidebar is the page's first <nav> or <aside> element; its
search box is an <input> in it; the current page's link in it is marked with aria-current or a "current"/"active"
class, on the link or on its parent; previous/next links are marked rel="prev"/"next" or start with Previous/Next;
the footer is a <footer> element. The browser tests use the headless Chrome or Chromium of the machine (CI's runners
have one), on the site served under /taskloom/ as GitHub Pages serves it."""

import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import pytest
import yaml

from taskloom import __version__
from taskloom.block import setting_label
from taskloom.editor import docs
from taskloom.registry import discover

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "packaging" / "docs_site.py"
WORKFLOW = REPO / ".github" / "workflows" / "build.yml"
SITE_URL = "https://lben.github.io/taskloom/"
GITHUB = "https://github.com/lben/taskloom"

# Blocks of the user's own, in the Taskloom home the site is built with: one in a category of its own, one in Files.
USER_BLOCKS = '''
from taskloom import Block, fields


class Tally(Block):
    type_id = "my.tally"
    title = "Tally"
    description = "Counts the rows of a table."
    category = "My Tools"
    config = {"column": fields.Text(required=False, help="The column to count", examples=["amount"])}

    def run(self, ctx):
        return {}


class Archive(Block):
    type_id = "my.archive"
    title = "Archive Files"
    description = "Moves old files into an archive folder."
    category = "Files"

    def run(self, ctx):
        return {}
'''


# --- the site ----------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    """The site, built by `python packaging/docs_site.py OUT` from the repository root, with Jinja2 unimportable (the
    builder uses the standard library) and a Taskloom home holding blocks of the user's own."""
    root = tmp_path_factory.mktemp("docs-site")
    (root / "home" / "blocks").mkdir(parents=True)
    (root / "home" / "blocks" / "mine.py").write_text(USER_BLOCKS, encoding="utf-8")
    (root / "no-jinja").mkdir()
    (root / "no-jinja" / "jinja2.py").write_text(
        'raise ImportError("Jinja2 is not a dependency of Taskloom")\n', encoding="utf-8")
    out = root / "site"
    env = {**os.environ, "TASKLOOM_HOME": str(root / "home"), "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
           "PYTHONPATH": os.pathsep.join(filter(None, [str(root / "no-jinja"), os.environ.get("PYTHONPATH")]))}
    done = subprocess.run([sys.executable, str(SCRIPT.relative_to(REPO)), str(out)], cwd=REPO, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert done.returncode == 0, f"packaging/docs_site.py exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
    assert out.is_dir(), f"packaging/docs_site.py wrote no folder {out}"
    return out


def app_pages() -> dict[str, str]:
    """The pages of the in-app Documentation column, over the built-in blocks."""
    return docs.pages(discover(None))


def site_files(site: Path) -> list[str]:
    return sorted(p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file())


def html_files(site: Path) -> list[str]:
    return [name for name in site_files(site) if name.endswith(".html")]


def slug(heading: str) -> str:
    """A GitHub-style heading id: lower case, punctuation dropped, each space a hyphen."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def norm(text: str) -> str:
    return " ".join(str(text).split())


# --- reading a page ----------------------------------------------------------------------------------------------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "pre", "table", "tr", "td", "th", "br",
         "dl", "dt", "dd", "section", "blockquote", "hr", "nav", "aside", "footer", "header", "main", "article"}


@dataclass
class Link:
    href: str
    rel: str
    sidebar: bool
    footer: bool
    highlighted: bool  # marked current (aria-current, or a current/active class), or its parent is
    text: list = field(default_factory=list)


def marked(attrs: dict) -> bool:
    classes = attrs.get("class", "").lower().split()
    return (attrs.get("aria-current", "false").lower() not in ("", "false")
            or any("current" in c or c == "active" or c.endswith("-active") for c in classes))


class Html(HTMLParser):
    """A page as the tests read it: its text (without scripts and styles), ids, links, the sidebar's text and inputs,
    the footer's text, its tags with their attributes, and the contents of its <script> and <style> elements."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.stack: list[list] = []  # [tag, marked, role ("sidebar", "footer" or None)]
        self.ids: list[str] = []
        self.links: list[Link] = []
        self.tags: list[tuple[str, dict]] = []
        self.inputs: list[dict] = []  # in the sidebar
        self.scripts: list[tuple[dict, str]] = []
        self.styles: list[str] = []
        self.parts, self.sidebar_parts, self.footer_parts = [], [], []
        self.has_sidebar = False
        self._link: Link | None = None
        self._raw: tuple[str, dict, list] | None = None
        self.feed(source)
        self.close()
        self.text = "".join(self.parts)
        self.sidebar_text = norm("".join(self.sidebar_parts))
        self.footer_text = norm("".join(self.footer_parts))

    def inside(self, role) -> bool:
        return any(r == role for _, _, r in self.stack)

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        self.tags.append((tag, a))
        if a.get("id"):
            self.ids.append(a["id"])
        if tag == "a" and a.get("name"):
            self.ids.append(a["name"])
        role = None
        if tag in ("nav", "aside") and not self.has_sidebar:
            role, self.has_sidebar = "sidebar", True
        elif tag == "footer":
            role = "footer"
        if tag == "a" and "href" in a:
            parent = bool(self.stack) and self.stack[-1][1]
            self._link = Link(a["href"], a.get("rel", "").lower(), self.inside("sidebar"), self.inside("footer"),
                              marked(a) or parent)
            self.links.append(self._link)
        if tag == "input" and self.inside("sidebar"):
            self.inputs.append(a)
        if tag in ("script", "style"):
            self._raw = (tag, a, [])
        if tag in BLOCK:
            self._add("\n")
        if tag not in VOID:
            self.stack.append([tag, marked(a), role])

    def handle_endtag(self, tag):
        if self._raw and tag == self._raw[0]:
            kind, a, data = self._raw
            (self.scripts.append((a, "".join(data))) if kind == "script" else self.styles.append("".join(data)))
            self._raw = None
        if tag == "a":
            self._link = None
        if tag in BLOCK:
            self._add("\n")
        if any(t == tag for t, _, _ in self.stack):
            while self.stack and self.stack.pop()[0] != tag:
                pass

    def handle_data(self, data):
        if self._raw:
            self._raw[2].append(data)
            return
        self._add(data)
        if self._link is not None:
            self._link.text.append(data)

    def _add(self, text):
        self.parts.append(text)
        if self.inside("sidebar"):
            self.sidebar_parts.append(text)
        if self.inside("footer"):
            self.footer_parts.append(text)


def read(site: Path, name: str) -> Html:
    return Html((site / name).read_text(encoding="utf-8"))


def resolve(href: str, page: str) -> tuple[str, str] | None:
    """The site file and anchor a link of `page` leads to (None: it leaves the site, e.g. https: or mailto:)."""
    split = urlsplit(href)
    if split.scheme or split.netloc:
        return None
    path = unquote(split.path)
    name = posixpath.normpath(posixpath.join(posixpath.dirname(page), path)) if path else page
    if path.endswith("/") or name == ".":
        name = posixpath.join("" if name == "." else name, "index.html")
    return name, unquote(split.fragment)


def is_remote(url: str) -> bool:
    return bool(re.match(r"\s*(https?:)?//", url, re.I))


def page_order(page: Html, name: str, pages) -> list[str]:
    """The pages in the order the sidebar first links them."""
    order = []
    for link in page.links:
        target = resolve(link.href, name) if link.sidebar else None
        if target and target[0] in pages and target[0] not in order:
            order.append(target[0])
    return order


# --- P1: the same pages as the column ----------------------------------------------------------------------------

def test_the_site_has_a_page_for_every_page_of_the_column_with_its_text_and_anchors(site):
    """Spec P1 (and correction a): `python packaging/docs_site.py OUT`, run as CI runs it and with Jinja2 unimportable,
    writes one file per page of the in-app column, under the same file name; each holds that page's text, in order,
    and every anchor it has, and says it is UTF-8 so its text reads as written however it is opened."""
    pages = app_pages()
    missing = sorted(set(pages) - set(site_files(site)))
    assert missing == [], f"the site has no file for the column's pages {missing}"
    problems = []
    for name, body in pages.items():
        raw = (site / name).read_text(encoding="utf-8")
        if not re.search(r"<meta\s[^>]*charset\s*=\s*[\"']?utf-8", raw, re.I):
            problems.append(f"{name}: no <meta charset=\"utf-8\">")
        page, app = Html(raw), Html(body)
        text, at = norm(page.text), 0
        for line in filter(None, (norm(line) for line in app.text.split("\n"))):
            found = text.find(line, at)
            if found < 0:
                problems.append(f"{name}: the column's text {line[:100]!r} is not on the site's page (in order)")
                break
            at = found + len(line)
        problems += [f"{name}: no anchor {i!r}" for i in app.ids if i not in page.ids]
    assert problems == [], "\n".join(problems)


def test_the_site_documents_the_built_in_blocks_only_not_the_blocks_of_the_home_it_is_built_in(site):
    """Spec P1 (discover(None)): blocks in the builder's Taskloom home (a category of their own, or a built-in one)
    are not on the site: no page for their category, no entry on any page."""
    assert "blocks-my-tools.html" not in site_files(site), "the user's own category got a page on the site"
    found = {name: [i for i in read(site, name).ids if i.startswith("my.")] for name in html_files(site)}
    assert {name: ids for name, ids in found.items() if ids} == {}, "the user's own blocks are on the site"


# --- P3 / P-T1: links and anchors ------------------------------------------------------------------------------------

def test_every_link_resolves_to_a_file_and_an_anchor_ids_are_unique_and_every_built_in_anchor_exists(site):
    """Spec P3 and P-T1, rule 5: every relative href (and src) of every page resolves to a file of the site and, with
    a #fragment, to an id there; none is root-relative (the site lives under /taskloom/ on GitHub Pages); ids are
    unique per page; every built-in <type_id> and <type_id>.<key> is on blocks-<category slug>.html and every
    properties.<row> on properties.html."""
    files = set(site_files(site))
    parsed = {name: read(site, name) for name in html_files(site)}
    problems = []
    for name, page in parsed.items():
        problems += [f"{name}: the id {i!r} is there {page.ids.count(i)} times"
                     for i in sorted(set(page.ids)) if page.ids.count(i) > 1]
        refs = [(link.href, "href") for link in page.links] + [
            (a[key], key) for tag, a in page.tags for key in ("src", "href")
            if tag in ("script", "link", "img", "iframe", "source") and a.get(key)]
        for ref, key in refs:
            if urlsplit(ref).path.startswith("/") and not is_remote(ref):
                problems.append(f"{name}: {key}={ref!r} is root-relative: it breaks under {SITE_URL}")
                continue
            target = resolve(ref, name)
            if target is None:
                continue
            file, anchor = target
            if file not in files:
                problems.append(f"{name}: {key}={ref!r} leads to no file of the site")
            elif anchor and (file not in parsed or anchor not in parsed[file].ids):
                problems.append(f"{name}: {key}={ref!r}: {file} has no id {anchor!r}")
    registry = discover(None)
    for type_id, cls in registry.blocks.items():
        if registry.sources[type_id] != "built-in":
            continue
        page = f"blocks-{slug(cls.category)}.html"
        ids = parsed[page].ids if page in parsed else []
        problems += [f"{page}: no anchor {anchor!r}"
                     for anchor in [type_id, *(f"{type_id}.{key}" for key in cls.config)] if anchor not in ids]
    properties = parsed["properties.html"].ids if "properties.html" in parsed else []
    problems += [f"properties.html: no anchor 'properties.{row}'" for row in docs.ROWS
                 if f"properties.{row}" not in properties]
    assert problems == [], "\n".join(problems)


REMOTE_IN_CSS = re.compile(r"""@import\s+(url\(\s*)?["']?\s*(https?:)?//|url\(\s*["']?\s*(https?:)?//""", re.I)
REMOTE_IN_JS = re.compile(r"""\b(fetch|import|importScripts)\s*\(\s*["'`]\s*(https?:)?//|\bfrom\s*["']\s*(https?:)?//"""
                          r"""|\.src\s*=\s*["'`]\s*(https?:)?//""", re.I)


def test_nothing_is_loaded_from_another_site(site):
    """Spec P2, P-T1 and correction c: the search, CSS and JS are inline or local. No <script src>, <link>, <img>,
    <iframe> or <source> points at http(s) (or //host), no CSS @import or url() does (in <style>, style= or a .css
    file), and no script fetches or imports from another site."""
    problems = []
    for name in site_files(site):
        if name.endswith(".css"):
            problems += [f"{name}: {m.group(0)!r}" for m in
                         REMOTE_IN_CSS.finditer((site / name).read_text(encoding="utf-8"))]
        elif name.endswith((".js", ".mjs")):
            problems += [f"{name}: {m.group(0)!r}" for m in
                         REMOTE_IN_JS.finditer((site / name).read_text(encoding="utf-8"))]
        elif name.endswith(".html"):
            page = read(site, name)
            for tag, a in page.tags:
                for key in ("src", "href", "data"):
                    if tag in ("script", "link", "img", "iframe", "source", "embed", "object", "audio", "video") \
                            and is_remote(a.get(key, "")):
                        problems.append(f"{name}: <{tag} {key}={a[key]!r}>")
                if REMOTE_IN_CSS.search(a.get("style", "")):
                    problems.append(f"{name}: <{tag} style={a['style']!r}>")
            problems += [f"{name}: <style> {m.group(0)!r}" for css in page.styles for m in REMOTE_IN_CSS.finditer(css)]
            problems += [f"{name}: <script> {m.group(0)!r}" for _, js in page.scripts for m in REMOTE_IN_JS.finditer(js)]
    assert problems == [], "\n".join(problems)


# --- P2: the layout ------------------------------------------------------------------------------------------------

def test_every_page_has_the_sidebar_with_the_version_guide_and_reference_its_sections_and_a_search_box(site):
    """Spec P2: on every page, a left sidebar with "Taskloom <version>", the Guide and Reference lists linking every
    page (the guide in README order, as Contents has it), the current page highlighted with links to its h2/h3
    sections right after it, and a search box with the script that runs it in the browser; and a viewport meta, so
    a phone gets the narrow layout where the sidebar stacks above the content."""
    pages = app_pages()
    guide = [name for name in (posixpath.basename(link.href.split("#")[0])
                               for link in Html(pages["index.html"]).links) if name in pages
             and name != "properties.html" and not name.startswith("blocks-")]
    guide = list(dict.fromkeys(guide))
    assert "schedules.html" in guide, guide
    problems = []
    for name, body in pages.items():
        page = read(site, name)
        where = f"{name}:"
        if not page.has_sidebar:
            problems.append(f"{where} no sidebar (<nav> or <aside>)")
            continue
        if f"Taskloom {__version__}" not in page.sidebar_text:
            problems.append(f"{where} the sidebar does not say 'Taskloom {__version__}'")
        problems += [f"{where} the sidebar has no {word!r} list" for word in ("Guide", "Reference")
                     if word not in page.sidebar_text]
        order = page_order(page, name, pages)
        problems += [f"{where} the sidebar does not link {other}" for other in sorted(set(pages) - set(order))]
        if [p for p in order if p in guide] != guide:
            problems.append(f"{where} the sidebar's guide is {[p for p in order if p in guide]}, not README's {guide}")
        sidebar = [(link, resolve(link.href, name)) for link in page.links if link.sidebar]
        highlighted = [target for link, target in sidebar if link.highlighted and target and not target[1]]
        if name not in [file for file, _ in highlighted]:
            problems.append(f"{where} the sidebar does not highlight this page's link")
        if any(file != name for file, _ in highlighted):
            problems.append(f"{where} the sidebar highlights other pages: {sorted({f for f, _ in highlighted})}")
        sections = [i for tag, a in Html(body).tags if tag in ("h2", "h3") for i in [a.get("id")] if i]
        start = next((n for n, (link, target) in enumerate(sidebar)
                      if link.highlighted and target and target[0] == name), None)
        if start is not None:
            run = []
            for _, target in sidebar[start:]:
                if not target or target[0] != name:
                    break
                run.append(target[1])
            missing = [i for i in sections if i not in run]
            if missing:
                problems.append(f"{where} the sidebar does not list its sections {missing[:5]} under this page")
        if not any(a.get("type", "text").lower() in ("search", "text") for a in page.inputs):
            problems.append(f"{where} the sidebar has no search box")
        if not page.scripts:
            problems.append(f"{where} no script, so the search box cannot run")
        if not any(tag == "meta" and a.get("name", "").lower() == "viewport" and "width=device-width" in
                   a.get("content", "").replace(" ", "").lower() for tag, a in page.tags):
            problems.append(f"{where} no <meta name=\"viewport\" content=\"width=device-width\">")
    assert problems == [], "\n".join(problems)


def test_previous_and_next_links_follow_the_sidebar_and_the_footer_has_the_version_and_a_github_link(site):
    """Spec P2: each page links the page before it and the page after it in the sidebar's order (Previous and Next),
    and has a footer with the version and a link to the project on GitHub."""
    pages = app_pages()
    problems = []
    for name in pages:
        page = read(site, name)
        order = page_order(page, name, pages)
        if name not in order:
            problems.append(f"{name}: not in its own sidebar")
            continue
        at = order.index(name)
        for word, rels, other in (("previous", ("prev", "previous"), order[at - 1] if at else None),
                                  ("next", ("next",), order[at + 1] if at + 1 < len(order) else None)):
            found = [resolve(link.href, name) for link in page.links if not link.sidebar and (
                set(link.rel.split()) & set(rels)
                or re.match(rf"\W*{word if word == 'next' else 'prev(ious)?'}\b", norm("".join(link.text)), re.I))]
            targets = sorted({t[0] for t in found if t})
            if other is not None and targets != [other]:
                problems.append(f"{name}: its {word} link leads to {targets}, not to {other}")
        if __version__ not in page.footer_text:
            problems.append(f"{name}: no <footer> with the version {__version__}")
        if not any(link.footer and link.href.startswith(GITHUB) for link in page.links):
            problems.append(f"{name}: its footer does not link {GITHUB}")
    assert problems == [], "\n".join(problems)


# --- P2 in a browser -----------------------------------------------------------------------------------------------

def chrome() -> str:
    """The machine's Chrome or Chromium (CI's runners have one; without one CI fails, a developer's run skips)."""
    for command in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if shutil.which(command):
            return shutil.which(command)
    for path in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if Path(path).is_file():
            return path
    if os.environ.get("CI"):
        pytest.fail("no Chrome or Chromium on this CI runner: the site's layout and search are checked in one")
    pytest.skip("no Chrome or Chromium on this machine: the site's layout and search are checked in one")


PROBE = r"""<!doctype html><html><head><meta charset="utf-8"></head><body><script>
const wait = ms => new Promise(done => setTimeout(done, ms));
function open(width, height, page) {
  const frame = document.createElement('iframe');
  frame.style.cssText = `width: ${width}px; height: ${height}px; border: 0; display: block`;
  document.body.appendChild(frame);
  return new Promise(done => { frame.onload = () => done(frame); frame.src = page; });
}
const box = el => { const r = el.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top,
                                                                    bottom: r.bottom, width: r.width, height: r.height}; };
const shown = doc => [...doc.querySelectorAll('a[href]')]
  .filter(a => a.getClientRects().length && getComputedStyle(a).visibility !== 'hidden').map(a => a.href);
async function layout([page, width, id]) {
  const frame = await open(width, 900, page);
  await wait(500);
  const doc = frame.contentDocument, side = doc.querySelector('nav, aside'), heading = doc.getElementById(id);
  const paragraphs = heading ? [...heading.parentElement.children].filter(el => el.tagName === 'P') : [];
  return {page, width, viewport: frame.contentWindow.innerWidth, sidebar: side && box(side),
          heading: heading && box(heading), text: Math.max(0, ...paragraphs.map(p => box(p).width))};
}
async function search([page, query]) {
  const frame = await open(1400, 1000, page);
  const doc = frame.contentDocument, win = frame.contentWindow;
  const before = shown(doc), side = doc.querySelector('nav, aside');
  const input = (side && [...side.querySelectorAll('input')].find(i => ['search', 'text'].includes(i.type)))
    || doc.querySelector('input[type=search]');
  if (!input) return {page, query, error: 'no search box'};
  input.focus();
  input.value = query;
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: query.slice(-1)}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
  await wait(1000);
  const typed = shown(doc);
  let leaving = false;
  win.addEventListener('beforeunload', () => { leaving = true; });
  const loaded = new Promise(done => { frame.onload = done; });
  for (const type of ['keydown', 'keypress', 'keyup'])
    input.dispatchEvent(new KeyboardEvent(type, {bubbles: true, cancelable: true, key: 'Enter', code: 'Enter',
                                                 keyCode: 13, which: 13}));
  await wait(300);
  if (!leaving && input.form) input.form.requestSubmit();
  await Promise.race([loaded, wait(3000)]);
  await wait(1000);
  return {page, query, before, typed, entered: shown(frame.contentDocument)};
}
(async () => {
  const ask = JSON.parse(new URLSearchParams(location.search).get('ask')), result = [];
  try {
    for (const item of ask.items) result.push(await (ask.what === 'layout' ? layout : search)(item));
  } catch (e) { result.push({error: String(e && e.stack || e)}); }
  await fetch('__result', {method: 'POST', body: JSON.stringify(result)});
})();
</script></body></html>"""


def in_chrome(site: Path, tmp_path: Path, what: str, items: list) -> list[dict]:
    """Runs the probe page in headless Chrome over the site, served (as GitHub Pages serves it) under /taskloom/
    by a plain static file server, and returns what it measured."""
    browser = chrome()
    results = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=str(site), **kw)

        def translate_path(self, path):
            return super().translate_path(path[len("/taskloom"):] if path.startswith("/taskloom/") else "/-/none")

        def do_GET(self):
            if self.path.split("?")[0].split("#")[0] == "/taskloom/__probe.html":
                self._send(PROBE.encode("utf-8"))
            else:
                super().do_GET()

        def do_POST(self):
            if self.path == "/taskloom/__result":
                results.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")))
            self._send(b"")

        def _send(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log = tmp_path / "chrome.log"
    try:
        url = (f"http://127.0.0.1:{server.server_address[1]}/taskloom/__probe.html?ask="
               + quote(json.dumps({"what": what, "items": items})))
        with open(log, "w", encoding="utf-8") as errors:
            process = subprocess.Popen([browser, "--headless", "--disable-gpu", "--no-sandbox", "--no-first-run",
                                        "--no-default-browser-check", "--disable-extensions",
                                        f"--user-data-dir={tmp_path / 'chrome-profile'}", "--window-size=1500,1100",
                                        "--virtual-time-budget=60000", "--dump-dom", url],
                                       stdout=subprocess.DEVNULL, stderr=errors)
            try:
                deadline = time.monotonic() + 90
                while not results and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.1)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(30)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
    assert results, f"Chrome reported nothing (exit {process.returncode}): {log.read_text(errors='replace')[-2000:]}"
    assert not any("error" in r for r in results[0]), results[0]
    return results[0]


def test_in_a_browser_the_sidebar_is_left_of_a_content_column_about_860_px_and_stacks_above_it_on_a_phone(
        site, tmp_path):
    """Spec P2: at a desktop width the sidebar is at the left of the content, and the content column is about
    860 px wide (not the whole rest of the window); at a phone's width the sidebar stacks above the content. Shown on
    a guide page and a reference page, by the first heading of the page's text and the paragraphs beside it."""
    pages = app_pages()
    first = {name: next(a["id"] for tag, a in Html(pages[name]).tags if tag in ("h1", "h2", "h3") and a.get("id"))
             for name in ("schedules.html", "blocks-files.html")}
    measured = in_chrome(site, tmp_path, "layout", [[name, width, first[name]] for name in first
                                                    for width in (1400, 390)])
    problems = []
    for m in measured:
        where = f"{m['page']} at {m['width']} px:"
        side, heading = m["sidebar"], m["heading"]
        if not side or not heading or side["width"] <= 0 or side["height"] <= 0:
            problems.append(f"{where} no visible sidebar or content heading: {m}")
            continue
        if m["width"] > 1000:
            if not (side["right"] <= heading["left"] + 1 and side["top"] < heading["bottom"]):
                problems.append(f"{where} the sidebar {side} is not at the left of the content {heading}")
            if not 600 <= m["text"] <= 940:
                problems.append(f"{where} the content's text is {m['text']:.0f} px wide, not about 860 px")
        elif side["bottom"] > heading["top"] + 1:
            problems.append(f"{where} the sidebar {side} does not stack above the content {heading}")
    assert problems == [], "\n".join(problems)


def test_in_a_browser_the_search_box_finds_sections_of_other_pages_by_their_heading_and_by_their_text(site, tmp_path):
    """Spec P2 and the user's answer "Yes, with search": the sidebar's search box runs in the browser, with no server
    but static files (served under /taskloom/ as on GitHub Pages). Typing a block's heading on a guide page (and
    pressing Enter, if its results show on a page of their own) shows a link to that block's section on its reference
    page; typing a word only a setting's help has shows a link to that setting's section (or its block's)."""
    registry = discover(None)
    assert registry.get("servers.remote_extract").title == "Remote Extract"
    chart = registry.get("reports.chart")
    kind = chart.config["kind"]
    assert "histogram" in kind.help and "histogram" not in f"{setting_label('kind', kind)} {chart.title}".lower()
    expected = {"Remote Extract": {("blocks-servers.html", "servers.remote_extract")},
                "histogram": {("blocks-reports.html", "reports.chart.kind"), ("blocks-reports.html", "reports.chart")}}
    measured = in_chrome(site, tmp_path, "search", [["schedules.html", query] for query in expected])

    def on_site(hrefs):
        found = set()
        for href in hrefs:
            split = urlsplit(href)
            if split.path.startswith("/taskloom/"):
                found.add((split.path[len("/taskloom/"):] or "index.html", unquote(split.fragment)))
        return found

    problems = []
    for m in measured:
        new = (on_site(m["typed"]) | on_site(m["entered"])) - on_site(m["before"])
        if not new & expected[m["query"]]:
            problems.append(f"searching {m['query']!r} on {m['page']} shows no link to {sorted(expected[m['query']])}"
                            f"; the links it adds: {sorted(new)[:10]}")
    assert problems == [], "\n".join(problems)


# --- P4: publishing ------------------------------------------------------------------------------------------------

def as_list(needs) -> list:
    return [needs] if isinstance(needs, str) else list(needs or [])


def test_the_pages_job_publishes_the_site_from_each_release_tag_after_the_release_and_nothing_waits_for_it():
    """Spec P4 and correction b: a pages job of its own runs only on v* tags, after the release job (needs:
    release), with pages: write and id-token: write in the github-pages environment; it checks out, runs uv sync,
    builds the site with `uv run python packaging/docs_site.py <dir>`, then configure-pages, upload-pages-artifact
    of that same folder and deploy-pages. No job needs it, so it can never hold up or fail a release."""
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    assert "pages" in jobs, f"no pages job in {WORKFLOW.name}: {sorted(jobs)}"
    job = jobs["pages"]
    assert "startsWith(github.ref,'refs/tags/v')" in str(job.get("if", "")).replace(" ", ""), job.get("if")
    assert as_list(job.get("needs")) == ["release"], job.get("needs")
    permissions = job.get("permissions") or {}
    assert (permissions.get("pages"), permissions.get("id-token")) == ("write", "write"), permissions
    environment = job.get("environment")
    assert (environment.get("name") if isinstance(environment, dict) else environment) == "github-pages", environment
    assert [name for name, other in jobs.items() if "pages" in as_list(other.get("needs"))] == []
    assert "pages" not in as_list(jobs["release"].get("needs"))

    steps = job.get("steps") or []

    def first(test) -> int:
        found = [n for n, step in enumerate(steps) if test(step)]
        assert found, f"the pages job has no such step: {steps}"
        return found[0]

    uses = lambda action: lambda step: str(step.get("uses", "")).startswith(action + "@")  # noqa: E731
    build_line = re.compile(r"uv run python packaging/docs_site\.py\s+(\S+)")
    checkout, setup_uv = first(uses("actions/checkout")), first(uses("astral-sh/setup-uv"))
    sync = first(lambda step: "uv sync" in str(step.get("run", "")))
    build = first(lambda step: build_line.search(str(step.get("run", ""))))
    configure, upload, deploy = (first(uses("actions/configure-pages")), first(uses("actions/upload-pages-artifact")),
                                 first(uses("actions/deploy-pages")))
    assert checkout < sync and setup_uv < sync < build < upload < deploy and configure < deploy, steps
    out = build_line.search(str(steps[build]["run"])).group(1).strip("'\"")
    uploaded = str((steps[upload].get("with") or {}).get("path", "_site/"))
    assert posixpath.normpath(uploaded) == posixpath.normpath(out), (
        f"the site is built into {out!r} but the upload-pages-artifact step uploads {uploaded!r}")


def test_readme_says_where_the_site_is_how_it_is_published_and_the_one_time_pages_switch():
    """Correction b: README says the site is at https://lben.github.io/taskloom/, that it is built by
    packaging/docs_site.py and published from each release tag, and the one-time switch of Settings > Pages >
    Source to GitHub Actions."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    sections = re.split(r"(?m)^## ", text)
    words = (SITE_URL, "docs_site.py", "tag", "Settings", "Pages", "Source", "GitHub Actions")
    assert any(all(word in section for word in words) for section in sections), (
        f"no README section says all of {words}")
