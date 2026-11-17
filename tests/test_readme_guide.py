"""A short README, details in the guide (docs/promises/readme.yaml, RD-1..RD-3). README.md says what Taskloom is, who
it is for, what it can do, how to get it and start it, and where the docs are, in at most 150 lines. The README's
detailed text is in docs/guide.md, which Help > Documentation and the website are built from: here as the installed
apps show it (the editor's column and the website are checked against that file in tests/test_docs_help.py and
tests/test_docs_site.py, and every test that checked the README's words now reads the guide). Every link in the README
works: checked without the internet, so a web address may only be the project's own (its GitHub repository and
releases, or its documentation website). The README's example flow passes Validate (docs/promises/braces.yaml, BR-8)."""

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
import yaml
from markdown_it import MarkdownIt

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
GUIDE = REPO / "docs" / "guide.md"
GITHUB = "https://github.com/lben/taskloom"
SITE_URL = "https://lben.github.io/taskloom/"
MARKDOWN = MarkdownIt("commonmark").enable("table")  # (as GitHub reads it; bare web addresses are added in links())

# Every "## " and "### " heading README.md had before its detailed text moved into docs/guide.md (commit 0edfb47),
# in order. The guide keeps each, at its level and in this order.
OLD_HEADINGS = [
    "## Install",
    "## The editor",
    "## Versions with Git: Save & Commit",
    "## Flows",
    "### Splitter and Merger: lines that run at the same time",
    "### Loop: the blocks inside it, once per item",
    "## Checks",
    "### Check File: is the file there?",
    "### Check Table: is the table as expected?",
    "## Statistics",
    "## Detect Anomalies",
    "## Reports and email",
    "## Schedules",
    "## Servers",
    "### API Request: a call to a web API",
    "### Session: API Request blocks that share cookies",
    "## Checking flows after an update",
    "## Your files",
    "## Writing a block",
    "## Development",
    "## License",
]

# What Taskloom can do, as words the README's part on it uses (RD-1): it names most of them.
CAN_DO = {"schedules": r"schedul", "database queries": r"\bquer|\bSQL\b", "email": r"e-?mail",
          "files": r"\bfiles?\b", "servers": r"\bservers?\b|\bSSH\b", "checks": r"\bchecks?\b",
          "reports": r"\breports?\b|\bcharts?\b|\bExcel\b", "web APIs": r"\bAPIs?\b", "run history": r"\bhistory\b",
          "retries": r"\bretr(?:y|ies)\b"}


# --- reading Markdown ----------------------------------------------------------------------------------------------

def guide_text() -> str:
    assert GUIDE.is_file(), "docs/guide.md is missing: the README's detailed text has not moved there"
    return GUIDE.read_text(encoding="utf-8")


def parts(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The text's introduction (the lines before its first '## ') and its '## ' sections, in order (lines inside code
    blocks are not headings, and are left empty)."""
    intro, sections, fence = [], [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fence = not fence
        elif not fence and line.startswith("## "):
            sections.append((line[3:].strip(), []))
            continue
        (sections[-1][1] if sections else intro).append("" if fence or line.startswith("```") else line)
    return intro, sections


def guide_parts() -> tuple[list[str], list[tuple[str, list[str]]]]:
    """docs/guide.md's introduction and its '## ' sections, in order: Help > Documentation's guide pages."""
    return parts(guide_text())


def plain_line(lines: list[str]) -> str | None:
    """A line of prose with no Markdown in it, as it reads on a page."""
    return next((line.strip() for line in lines
                 if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ,.;:()'/\-]{29,}", line.strip())
                 and not re.match(r"\d+[.)] ", line.strip())), None)  # (a numbered item loses its number)


def slug(heading: str) -> str:
    """A GitHub-style heading id: lower case, punctuation dropped, each space a hyphen."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def headings(text: str) -> list[str]:
    """The text's '## ' and '### ' heading lines, in order (a # line inside a code block is no heading)."""
    found, fence = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fence = not fence
        elif not fence and re.match(r"#{2,3} ", line):
            found.append(line.rstrip())
    return found


def links(text: str) -> list[str]:
    """Every link and picture of the Markdown as GitHub shows it: [..](..), <..>, ![..](..), href= and src= in HTML,
    and web addresses written out in the text (GitHub makes those links too)."""
    found = []

    def walk(tokens):
        for token in tokens:
            if token.type == "link_open":
                found.append(token.attrGet("href"))
            elif token.type == "image":
                found.append(token.attrGet("src"))
            elif token.type in ("html_inline", "html_block"):
                found.extend(re.findall(r"""(?:href|src)\s*=\s*["']([^"']+)""", token.content))
            elif token.type == "text":
                found.extend(re.findall(r"https?://[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'\"]", token.content))
            walk(token.children or [])

    walk(MARKDOWN.parse(text))
    return found


def anchors(text: str) -> set[str]:
    """The ids GitHub gives the Markdown's headings: the heading's text in lower case, punctuation dropped, a hyphen
    for each space; one already given gets -1, -2, ..."""
    tokens, given, found = MARKDOWN.parse(text), {}, set()
    for i, token in enumerate(tokens):
        if token.type == "heading_open":
            base = slug("".join(c.content for c in tokens[i + 1].children if c.type in ("text", "code_inline")))
            n = given.get(base, 0)
            given[base] = n + 1
            found.add(base if n == 0 else f"{base}-{n}")
    return found


def site_pages() -> dict[str, str]:
    """The documentation website's pages (packaging/docs_site.py writes one file for each, under its name)."""
    from taskloom.editor import docs
    from taskloom.registry import discover
    return docs.pages(discover(None))


def file_problems(href: str, path: str, fragment: str) -> list[str]:
    """`path`, relative to the repository's top folder, is a file or folder of the repository; `fragment`, if any,
    is a heading of that Markdown file (or a line of another file: L12)."""
    target = (REPO / unquote(path).lstrip("/")).resolve()
    if not target.is_relative_to(REPO) or not target.exists():
        return [f"{href}: there is no {unquote(path)} in the repository"]
    if not fragment:
        return []
    if target.is_file() and target.suffix.lower() == ".md":
        ids = anchors(target.read_text(encoding="utf-8"))
        return [] if unquote(fragment) in ids else [f"{href}: {target.relative_to(REPO)} has no heading #{fragment}"]
    return [] if re.fullmatch(r"L\d+(?:-L\d+)?", fragment) else [f"{href}: #{fragment} is no place in that file"]


def link_problems(text: str) -> list[str]:
    """What is wrong with each link of README text `text` (none: every link works)."""
    problems, pages = [], None
    for href in links(text):
        parts_ = urlsplit(href)
        if not parts_.scheme and not parts_.netloc:  # relative: to a file or folder here, or a heading of the README
            if parts_.path:
                problems += file_problems(href, parts_.path, parts_.fragment)
            elif unquote(parts_.fragment) not in anchors(text):
                problems.append(f"{href}: the README has no heading #{parts_.fragment}")
        elif href.startswith(SITE_URL):
            pages = pages if pages is not None else site_pages()
            name = unquote(parts_.path)[len(urlsplit(SITE_URL).path):] or "index.html"
            if name not in pages:
                problems.append(f"{href}: the website has no page {name}")
            elif parts_.fragment and f'id="{parts_.fragment}"' not in pages[name]:
                problems.append(f"{href}: the website's {name} has no #{parts_.fragment}")
        elif href == GITHUB or href.startswith((GITHUB + "/", GITHUB + "#", GITHUB + "?")):
            shown = re.fullmatch(r"/[^/]+/[^/]+/(?:blob|tree)/[^/]+/(.+)", parts_.path)
            if shown:  # a file of the repository as GitHub shows it
                problems += file_problems(href, shown.group(1), parts_.fragment)
        else:
            problems.append(f"{href}: not the project's own GitHub repository, releases or documentation website")
    return problems


# --- RD-1: the README is short and says the essentials -------------------------------------------------------------

@pytest.mark.invariant("RD-1")
def test_the_readme_has_at_most_150_lines():
    lines = README.read_text(encoding="utf-8").splitlines()

    assert len(lines) <= 150, f"README.md has {len(lines)} lines"


@pytest.mark.invariant("RD-1")
def test_the_readme_says_what_taskloom_is_who_it_is_for_what_it_can_do_how_to_get_and_start_it_and_the_docs():
    """RD-1: before its first section, what Taskloom is (automations or flows made of blocks); then sections on who it
    is for, on what it can do (naming most of what Taskloom does), on how to get it (a link to the releases) and start
    it (the editor's or the command line's program), and on where the docs are (Help > Documentation in the app, the
    website, and docs/guide.md)."""
    intro, sections = parts(README.read_text(encoding="utf-8"))
    intro = "\n".join(intro)

    def under(pattern: str) -> str:
        """The sections whose heading matches `pattern`, as one text."""
        return "\n".join("\n".join(lines) for heading, lines in sections if re.search(pattern, heading, re.I))

    problems = []
    if not (re.search(r"\bblocks?\b", intro, re.I) and re.search(r"automat|\bflows?\b", intro, re.I)):
        problems.append("what Taskloom is: the text before the first section speaks of no automations made of blocks")
    if not under(r"\bwho\b|\baudience\b").strip():
        problems.append("who it is for: no section on it (a heading with 'who')")
    can = under(r"\bwhat\b.*\b(?:can|do|does|build)\b|\bfeatures?\b")
    named = [thing for thing, pattern in CAN_DO.items() if re.search(pattern, can, re.I)]
    if len(named) < 6:
        problems.append(f"what it can do: no section on it naming most of {list(CAN_DO)} (it names {named})")
    get = under(r"\b(?:get|install|download|start)\b")
    if not any(href.startswith(GITHUB + "/releases") for href in links(get)):
        problems.append("how to get it: no section on it linking the releases")
    if not re.search(r"taskloomw?\.exe|\btaskloom (?:editor|run)\b", get):
        problems.append("how to start it: no section on getting it names taskloomw.exe, taskloom editor or run")
    about_docs = under(r"\bdoc")
    shown = links(about_docs)
    if not re.search(r"\bHelp\W+Documentation\b", about_docs):
        problems.append("where the docs are: no section on the docs names Help > Documentation")
    if not any(href.startswith(SITE_URL) for href in shown):
        problems.append(f"where the docs are: no section on the docs links the website {SITE_URL}")
    if not any(urlsplit(href).path in ("docs/guide.md", "./docs/guide.md") for href in shown):
        problems.append("where the docs are: no section on the docs links the guide, docs/guide.md")
    assert problems == [], "\n".join(problems)


# --- RD-2: nothing is lost: the guide holds the README's detailed text ---------------------------------------------

@pytest.mark.invariant("RD-2")
def test_the_guide_has_every_heading_the_readme_had_at_its_level_and_in_its_order():
    have = headings(guide_text())
    missing, at = [], 0
    for heading in OLD_HEADINGS:
        if heading in have[at:]:
            at = have.index(heading, at) + 1
        else:
            missing.append(heading)

    assert missing == [], f"docs/guide.md lacks these headings of the README, or has them out of order: {missing}"


INSTALLED = r'''
import json
from taskloom.editor import docs
from taskloom.registry import discover
print(json.dumps({"module": docs.__file__, "pages": docs.pages(discover(None))}))
'''


def put(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        shutil.copy(source, target)


@pytest.mark.invariant("RD-2")
@pytest.mark.parametrize("build", ["wheel", "Windows build"])
def test_the_installed_apps_help_documentation_is_the_guide_of_docs_guide_md(build, tmp_path, monkeypatch):
    """RD-2 where users read it: Taskloom as its wheel installs it (the package, and the files pyproject.toml adds to
    it) and as the Windows build lays it out (the modules, and the data files packaging/taskloom.spec adds), with no
    source checkout around it: Help > Documentation's guide pages are docs/guide.md's '## ' sections, linked from
    Contents in its order, each with a sentence of its section."""
    _, sections = guide_parts()
    expected = [slug(heading) + ".html" for heading, _ in sections]
    root = tmp_path / "installed"
    if build == "wheel":
        shutil.copytree(REPO / "taskloom", root / "taskloom", ignore=shutil.ignore_patterns("__pycache__"))
        wheel = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"]["build"]
        for source, target in wheel["targets"]["wheel"].get("force-include", {}).items():
            put(REPO / source, root / target)
    else:
        from tests.test_inspect_mermaid import spec_build  # (it runs the spec, with PyInstaller's helpers)

        shutil.copytree(REPO / "taskloom", root / "taskloom", ignore=lambda folder, names: [
            n for n in names if n == "__pycache__" or (Path(folder, n).is_file() and not n.endswith(".py"))])
        for source, folder in spec_build(monkeypatch, headless=False)["datas"]:
            if Path(source).resolve().is_relative_to(REPO):
                put(Path(source), root / folder / (Path(source).name if Path(source).is_file() else ""))
    env = {**os.environ, "TASKLOOM_HOME": str(tmp_path / "home"),
           "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}
    done = subprocess.run([sys.executable, "-c", INSTALLED], cwd=root, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout.splitlines()[-1])
    assert Path(result["module"]).resolve().is_relative_to(root.resolve()), result["module"]
    pages = result["pages"]

    guide = [name for name in pages if name not in ("index.html", "properties.html") and not name.startswith("blocks-")]
    linked = list(dict.fromkeys(name for name in re.findall(r'href="([^"#]+)', pages["index.html"]) if name in guide))
    assert sorted(guide) == sorted(expected) and linked == expected, (guide, linked, expected)
    problems = []
    for (heading, lines), name in zip(sections, expected):
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", pages[name])).split())
        sentence = plain_line(lines)
        if heading not in text or (sentence and " ".join(sentence.split()) not in text):
            problems.append(f"{name} does not show the guide's section {heading!r}")
    assert problems == [], "\n".join(problems)


def pages_as_installed(tmp_path: Path, monkeypatch, guide: str | None) -> dict[str, str]:
    """Help > Documentation's pages (docs.pages) of Taskloom as installed in tmp_path/site-packages, with no source
    checkout around it (no docs/ folder above the package): taskloom/guide.md, where the wheel puts docs/guide.md,
    holds `guide` (None: it is missing, a broken install). Built in this process: the docs module's __file__ says
    where it is installed."""
    from taskloom.editor import docs
    from taskloom.registry import discover
    package = tmp_path / "site-packages" / "taskloom"
    (package / "editor").mkdir(parents=True)
    if guide is not None:
        (package / "guide.md").write_text(guide, encoding="utf-8")
    monkeypatch.setattr(docs, "__file__", str(package / "editor" / "docs.py"))
    return docs.pages(discover(None))


def shown(page: str) -> str:
    """A page's text as it reads, its words one space apart."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


# A guide written in UTF-8 with a dash and an accent (two or three bytes each there), as docs/guide.md is.
INSTALLED_GUIDE = ("Automations made of blocks – this is the installed guide.\n\n## Schedules\n\n"
                   "A Schedule block starts the flow on weekdays (Mon–Fri) at 07:30, Zürich time.\n")


@pytest.mark.invariant("RD-2")
def test_help_documentation_as_installed_shows_the_guide_md_beside_the_package_as_written(tmp_path, monkeypatch):
    """RD-2 as installed: the wheel puts docs/guide.md into the package as taskloom/guide.md, and with no source
    checkout around it Help > Documentation shows that file: Contents begins with its introduction and its "## "
    section is a page, their dashes and accents as written (the file is UTF-8, whatever the computer's own encoding:
    on Windows it is often another)."""
    pages = pages_as_installed(tmp_path, monkeypatch, INSTALLED_GUIDE)

    contents = shown(pages["index.html"])
    assert contents.startswith("Automations made of blocks – this is the installed guide."), contents[:300]
    assert "A Schedule block starts the flow on weekdays (Mon–Fri) at 07:30, Zürich time." in shown(
        pages.get("schedules.html", "")), sorted(pages)


@pytest.mark.invariant("RD-2")
def test_help_documentation_as_installed_without_its_guide_md_says_so_in_these_words(tmp_path, monkeypatch):
    """The broken install of RD-2: taskloom/guide.md missing, no source checkout around it. Contents begins with the
    heading Taskloom and says, in these words, that the guide is not included and the reference is; then come its
    Guide part (empty) and its Reference."""
    pages = pages_as_installed(tmp_path, monkeypatch, None)

    contents = shown(pages["index.html"])
    assert contents.startswith("Taskloom The guide is not included in this installation (its guide.md is missing); "
                               "the reference below is. Guide Reference"), contents[:300]


# --- RD-3: every link in the README works --------------------------------------------------------------------------

@pytest.mark.invariant("RD-3")
def test_every_link_in_the_readme_leads_to_a_file_a_heading_or_the_projects_own_pages():
    """RD-3: each relative link is a file or folder of the repository (and its #anchor a heading there, as GitHub
    names it), or a heading of the README itself; a web address is the project's GitHub repository (a file it shows
    there exists) or its releases, or a page and anchor of the documentation website as packaging/docs_site.py
    writes it. Nothing is fetched from the internet."""
    text = README.read_text(encoding="utf-8")

    problems = link_problems(text)

    assert links(text), "the README links nothing (not even its docs)"
    assert problems == [], "\n".join(problems)


# --- BR-8: the README's example flow passes Validate ---------------------------------------------------------------

def readme_flows() -> list[str]:
    """Each ```yaml block of README.md, as written there."""
    return [token.content for token in MARKDOWN.parse(README.read_text(encoding="utf-8"))
            if token.type == "fence" and token.info.strip() == "yaml"]


def connections_named(flow: dict) -> dict:
    """A connection of the kind each Connection setting asks for, under each name the flow gives one (Validate only
    looks the name up: nothing connects)."""
    from taskloom.block import fields
    from taskloom.registry import discover
    registry, found = discover(None), {}
    for spec in flow["blocks"].values():
        cls = registry.get(spec["type"])
        for key, value in (spec.get("config") or {}).items():
            setting = cls.config.get(key) if cls else None
            if isinstance(setting, fields.Connection) and value:
                found[value] = {"kind": setting.kind}
    return found


@pytest.mark.invariant("BR-8")
def test_the_readme_example_flow_passes_validate_with_no_error_and_no_did_you_mean_warning(env):
    """BR-8 for the README: each YAML flow it shows, taken from README.md as written, passes the real `taskloom
    validate`, run in a temporary Taskloom home (never ~/.taskloom or the system's credential store) that holds a
    connection of the right kind under each name the flow uses: it exits 0 with no error and no BR-4 warning (a
    single-brace {name} that is plain text: did you mean {{name}}?)."""
    flows = readme_flows()

    assert flows, "README.md shows no ```yaml flow"
    for n, text in enumerate(flows, 1):
        env.write("home/connections.yaml", yaml.safe_dump(connections_named(yaml.safe_load(text))))
        path = env.root / f"readme-flow-{n}.yaml"
        path.write_text(text, encoding="utf-8")

        validated = env.taskloom("validate", path)

        said = validated.stdout + validated.stderr
        wrong = [line for line in said.splitlines() if line.startswith("error:") or "did you mean" in line.lower()]
        assert validated.returncode == 0 and wrong == [], f"README.md's flow {n}:\n{said}"
