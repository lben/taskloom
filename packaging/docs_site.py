"""Build the documentation website: python packaging/docs_site.py OUT

The pages of Help > Documentation (same file names and anchors), over the built-in blocks, in the usual Read the
Docs layout: a sidebar with the pages, the current page's sections and a search box that runs in the browser, then
the page, previous/next links and a footer. Everything it needs is in OUT; nothing is loaded from another site.
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from taskloom import __version__  # noqa: E402
from taskloom.editor import docs  # noqa: E402
from taskloom.registry import discover  # noqa: E402

GITHUB = "https://github.com/lben/taskloom"
HEADING = re.compile(r"<h([1-6])([^>]*)>(.*?)</h\1>", re.S)

STYLE = """
:root { --bg: #fcfcfc; --fg: #404040; --side: #343131; --side-fg: #d9d9d9; --head: #2980b9; --link: #2980b9;
        --line: #e1e4e5; --code: #f3f4f4; --here: #fcfcfc; --here-fg: #404040; --sub: #e3e3e3;
        --sub-fg: #404040; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1d1f21; --fg: #d6d6d6; --side: #141414; --head: #1f5f8b; --link: #6cb6e8; --line: #3b3f42;
          --code: #2a2d30; --here: #2a2d30; --here-fg: #e6e6e6; --sub: #232527; --sub-fg: #c8c8c8; }
}
* { box-sizing: border-box; }
body { margin: 0; display: flex; align-items: flex-start; background: var(--bg); color: var(--fg);
       font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
nav { flex: 0 0 300px; position: sticky; top: 0; height: 100vh; overflow-y: auto; background: var(--side);
      color: var(--side-fg); font-size: 15px; }
nav a { color: var(--side-fg); text-decoration: none; display: block; padding: 3px 16px; }
nav a:hover { background: #4e4a4a; }
nav .brand { background: var(--head); padding: 14px 16px; }
nav .brand a { color: #fff; font-size: 20px; font-weight: bold; padding: 0 0 10px; }
nav .brand a:hover { background: none; }
nav input { width: 100%; padding: 6px 12px; border: 1px solid #2472a4; border-radius: 50px; font: inherit; }
nav ul { list-style: none; margin: 0; padding: 0; }
nav .caption { color: #55a5d9; font-weight: bold; text-transform: uppercase; font-size: 13px; margin: 16px 16px 4px; }
nav a[aria-current] { background: var(--here); color: var(--here-fg); font-weight: bold; }
nav .sections a { background: var(--sub); color: var(--sub-fg); padding-left: 32px; font-size: 14px; }
nav .sections a.h3 { padding-left: 48px; }
#docs-results:not(:empty) { margin: 8px 0; border-bottom: 1px solid #4e4a4a; }
#docs-results small { display: block; padding: 0 16px 4px; color: #a9a9a9; }
main { flex: 1; min-width: 0; padding: 24px 48px; }
article { max-width: 860px; }
a { color: var(--link); }
h1, h2, h3 { line-height: 1.25; }
code, pre { font-family: ui-monospace, Consolas, "SF Mono", Menlo, monospace; font-size: 90%; background: var(--code); }
code { padding: 1px 4px; border: 1px solid var(--line); }
pre { padding: 12px; overflow-x: auto; border: 1px solid var(--line); }
pre code { padding: 0; border: 0; background: none; font-size: 100%; }
table { border-collapse: collapse; }
td, th { border: 1px solid var(--line); padding: 4px 8px; }
.pager { max-width: 860px; display: flex; justify-content: space-between; gap: 16px; margin-top: 32px;
         padding-top: 16px; border-top: 1px solid var(--line); }
.pager a[rel=next] { margin-left: auto; text-align: right; }
footer { max-width: 860px; margin-top: 24px; color: #808080; font-size: 14px; }
@media (max-width: 800px) {
  body { display: block; }
  nav { position: static; height: auto; max-height: 50vh; }
  main { padding: 16px; }
}
"""

SEARCH = """const DOCS_INDEX = %s;
(function () {
  const box = document.getElementById('docs-search'), list = document.getElementById('docs-results');
  function show() {
    const words = box.value.toLowerCase().split(/\\s+/).filter(Boolean);
    list.replaceChildren();
    if (!words.length) return;
    const found = [];
    for (const [href, title, page, text] of DOCS_INDEX) {
      const head = title.toLowerCase(), all = head + ' ' + text.toLowerCase();
      // (sections whose heading has more of the words first)
      if (words.every(w => all.includes(w)))
        found.push([words.filter(w => head.includes(w)).length, href, title, page]);
    }
    found.sort((a, b) => b[0] - a[0]);
    if (!found.length) { const li = document.createElement('li'); li.textContent = 'Nothing found'; list.append(li); }
    for (const [, href, title, page] of found.slice(0, 30)) {
      const li = document.createElement('li'), a = document.createElement('a'), small = document.createElement('small');
      a.href = href; a.textContent = title; small.textContent = page;
      li.append(a, small); list.append(li);
    }
  }
  box.addEventListener('input', show);
  box.addEventListener('keydown', e => {
    const first = list.querySelector('a');
    if (e.key === 'Enter' && first) location.href = first.href;
  });
  show();
})();
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Taskloom {version} documentation</title>
<style>{style}</style>
</head>
<body>
<nav aria-label="Documentation">
<div class="brand"><a href="index.html">Taskloom {version}</a>
<input type="search" id="docs-search" placeholder="Search the docs" aria-label="Search the docs" autocomplete="off">
</div>
<ul id="docs-results"></ul>
{sidebar}
</nav>
<main>
<article>
{body}
</article>
<div class="pager">{pager}</div>
<footer>Taskloom {version} · MIT License · <a href="{github}">Taskloom on GitHub</a></footer>
</main>
<script src="search.js"></script>
</body>
</html>
"""


def text_of(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def sections(body: str) -> list[tuple[str, str, str, str]]:
    """The page's headings that have an id: (level, id as written, title, the text up to the next one)."""
    found = []
    marks = [(m, i.group(1)) for m in HEADING.finditer(body) if (i := re.search(r'\bid="([^"]*)"', m.group(2)))]
    for n, (m, anchor) in enumerate(marks):
        end = marks[n + 1][0].start() if n + 1 < len(marks) else len(body)
        found.append((m.group(1), anchor, text_of(m.group(3)), text_of(body[m.end():end])))
    return found


def build(out: Path):
    registry = discover(None)
    pages = docs.pages(registry)
    reference = ["properties.html"] + list(dict.fromkeys(docs.category_page(cls.category)
                                                         for cls in registry.blocks.values()))
    guide = [name for name in pages if name != "index.html" and name not in reference]
    order = ["index.html", *guide, *reference]
    titles = {name: "Contents" if name == "index.html" else text_of(HEADING.search(pages[name]).group(3))
              for name in order}
    found = {name: sections(pages[name]) for name in order}

    def items(names, current):
        lines = []
        for name in names:
            here = ' aria-current="page"' if name == current else ""
            lines.append(f'<li><a href="{name}"{here}>{html.escape(titles[name])}</a>')
            if name == current:
                lines.append('<ul class="sections">' + "".join(
                    f'<li><a class="h{level}" href="{name}#{anchor}">{html.escape(title)}</a></li>'
                    for level, anchor, title, _ in found[name] if level in ("2", "3")) + "</ul>")
            lines.append("</li>")
        return "<ul>" + "".join(lines) + "</ul>"

    out.mkdir(parents=True, exist_ok=True)
    for n, name in enumerate(order):
        sidebar = (items(["index.html"], name) + '<p class="caption">Guide</p>' + items(guide, name)
                   + '<p class="caption">Reference</p>' + items(reference, name))
        pager = "".join(
            f'<a rel="{rel}" href="{order[at]}">{text.format(html.escape(titles[order[at]]))}</a>'
            for rel, text, at in (("prev", "← Previous: {}", n - 1), ("next", "Next: {} →", n + 1))
            if 0 <= at < len(order))
        (out / name).write_text(PAGE.format(title=html.escape(titles[name]), version=__version__, style=STYLE,
                                            sidebar=sidebar, body=pages[name], pager=pager, github=GITHUB),
                                encoding="utf-8")
    index = [[f"{name}#{html.unescape(anchor)}", title, titles[name], text]
             for name in order for _, anchor, title, text in found[name]]
    (out / "search.js").write_text(SEARCH % json.dumps(index, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(order)} pages to {out}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path, help="the folder to write the site into")
    build(parser.parse_args().out)


if __name__ == "__main__":
    main()
