"""The proof PDF (packaging/proof_pdf.py) opens with what is new in this version: each change whose proof scenario
(module.function in tests/proofs, also the name of its proof folder) did not exist at the base tag, the nearest
v tag reachable from the parent of the commit the PDF is built from. The cover lists them, under "New in <version>"
when that commit is the release's tag, else "New since <base tag>", each with its number, title (a link to its first
page), result and page; 'In this proof' and each of their pages mark them NEW. Without git, the history or a base
tag: no list and no mark.

Each PDF is built by the real script, as CI builds it, from proof records like the ones tests/proofs writes, in a
test repository made with the real git program (Git config of the test's own); it is read back with QtPdf: the text
of each page and the links on the cover."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QModelIndex  # noqa: E402
from PySide6.QtPdf import QPdfDocument, QPdfLinkModel  # noqa: E402

from taskloom import __version__  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PROOF_PDF = REPO / "packaging" / "proof_pdf.py"
GIT = shutil.which("git")  # read once, before a test empties PATH
pytestmark = pytest.mark.skipif(GIT is None, reason="what is new is found with the git program, not installed")

A, B = "tests/proofs/test_proof_a.py", "tests/proofs/test_proof_b.py"
# The changes' titles, as their proofs name them (no "new" in them: the PDF's NEW marks are looked for).
OLD, ADDED, LATE = "Blocks line up", "Lines bend round blocks", "Runs keep their logs"
SINCE = "new since v0.1.0"


@pytest.fixture(autouse=True)
def own_git(tmp_path, monkeypatch):
    """Git of the test's own: an empty global config, no system config, none of the developer's GIT_* variables,
    and no repository found above the test's folder."""
    config = tmp_path / "gitconfig"
    config.write_bytes(b"")
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.resolve()))


# --- a repository with releases, made with the git program ---------------------------------------------------------

def git(folder, *args) -> str:
    done = subprocess.run([GIT, "-C", str(folder), *map(str, args)], capture_output=True, encoding="utf-8",
                          errors="replace", timeout=60)
    assert done.returncode == 0, f"git {' '.join(map(str, args))}: {done.stderr}"
    return done.stdout


def proofs(*names, asked="Can it?") -> str:
    """A proof module with these scenarios, written as the real ones are (test_added is shown on Linux)."""
    text = '"""Proofs."""\n\nimport pytest\n'
    for name in names:
        marked = '@pytest.mark.linux_proof("It needs a Linux server.")\n' if name == "test_added" else ""
        text += f'\n\n{marked}def {name}(proof, win, qtbot):\n    p = proof("{name}", "{asked}")\n'
    return text


def write(folder: Path, files: dict):
    for path, text in files.items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(text, encoding="utf-8", newline="")


def new_repo(folder: Path) -> Path:
    folder.mkdir(parents=True)
    git(folder, "init", "-q")
    return folder


def commit(repo: Path, files: dict, tag: str = ""):
    write(repo, files)
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "proofs")
    if tag:
        git(repo, "tag", tag)


def releases(folder: Path, release: bool = False) -> Path:
    """v0.0.1 (no scenario yet), v0.1.0 (test_old); then test_added (and test_old's body changed); then the commit
    built, adding test_proof_b.test_new: the release v<version> when `release`."""
    repo = new_repo(folder)
    commit(repo, {A: proofs()}, tag="v0.0.1")
    commit(repo, {A: proofs("test_old")}, tag="v0.1.0")
    commit(repo, {A: proofs("test_old", "test_added", asked="Can it, really?")})
    commit(repo, {B: proofs("test_new")}, tag=f"v{__version__}" if release else "")
    return repo


# --- proof records, as tests/proofs/conftest.py writes them --------------------------------------------------------

def record(proof_dir: Path, scenario: str, title: str, minute: int, system="Windows", status="passed"):
    folder = proof_dir / scenario
    folder.mkdir(parents=True)
    ok = status == "passed"
    entries = [{"kind": "step", "text": "Open the flow."}, {"kind": "check", "text": "It opened", "ok": True},
               {"kind": "step", "text": "Press Run now."}, {"kind": "check", "text": "It did what was asked", "ok": ok}]
    (folder / "proof.json").write_text(json.dumps({
        "title": title, "asked": "Can it do this?", "status": status,
        "error": "" if ok else "AssertionError: it did not do what was asked", "system": system,
        "why_not_windows": "It needs a Linux server." if system == "Linux" else "",
        "started": f"2026-10-01T10:{minute:02}:00+00:00", "version": __version__,
        "idea": ["It is shown in the editor."], "why_simplest": ["It reuses what is there."],
        "alternatives": [{"option": "A separate window", "why_not": "One more thing to learn."}],
        "fits": ["As the rest of the editor does."], "entries": entries,
        "platform": "Windows-11 · Python 3.12 · PySide6 6.9"}, ensure_ascii=False), encoding="utf-8")


def session(proof_dir: Path, exitstatus: int, system: str):
    (proof_dir / "session.json").write_text(json.dumps({"exitstatus": exitstatus, "system": system}),
                                            encoding="utf-8")


def as_ci_records(root: Path) -> list:
    """The two proof folders CI makes one PDF of: the Windows run (test_added skipped there) and the Linux run
    (test_added shown there, failed). In the PDF: 1 test_new, 2 test_old, 3 test_added."""
    windows, linux = root / "proofs", root / "proofs-linux"
    record(windows, "test_proof_b.test_new", LATE, 1)
    record(windows, "test_proof_a.test_old", OLD, 2)
    record(windows, "test_proof_a.test_added", ADDED, 3, status="skipped")
    record(linux, "test_proof_a.test_added", ADDED, 4, system="Linux", status="failed")
    session(windows, 0, "Windows")
    session(linux, 1, "Linux")
    return [windows, linux]


# --- the PDF, built by the real script and read back with QtPdf ----------------------------------------------------

def squashed(text: str) -> str:
    return "".join(text.split()).lower()


def loose(text: str) -> str:
    """A pattern for `text` as the PDF's text reads back on every system: the headings' letters are spaced out,
    and on Windows the reader puts stray spaces between them (e.g. "ne w s ince v0. 1. 0")."""
    return r"\s*".join(map(re.escape, squashed(text)))


NEW = r"\bn\s?e\s?w\b"  # the word "new", also read back as "ne w"


class Pdf:
    """Each page's text (lower case; `flat`: its spaces and line breaks as one space), and the cover's links to
    pages of the PDF: (the text the link is on, squashed; the index of the page it goes to)."""

    def __init__(self, path: Path):
        doc = QPdfDocument()
        assert doc.load(str(path)) == QPdfDocument.Error.None_, f"cannot read {path}"
        self.texts = [doc.getAllText(page).text().lower() for page in range(doc.pageCount())]
        self.flat = [" ".join(text.split()) for text in self.texts]
        self.cover = range(self.first_page(1))
        links = {}
        model = QPdfLinkModel()
        model.setDocument(doc)
        for page in self.cover:
            text = doc.getAllText(page).text()
            middles = []  # of each character of the page, once a link needs them
            model.setPage(page)
            for row in range(model.rowCount(QModelIndex())):
                link = model.data(model.index(row, 0), QPdfLinkModel.Role.Link.value)
                if link.page() < 0:  # a web link (the CI run's)
                    continue
                # The text it is on: the characters whose middle is in it.
                middles = middles or [doc.getSelectionAtIndex(page, i, 1).boundingRectangle().center()
                                      for i in range(len(text))]
                on = "".join(char for char, middle in zip(text, middles)
                             if any(rect.contains(middle) for rect in link.rectangles()))
                # Qt's model can list a link once for each of its rectangles: each link once.
                links[(page, link.page(), tuple(r.getRect() for r in link.rectangles()))] = (squashed(on), link.page())
        self.links = list(links.values())
        doc.close()

    def pages_of(self, number: int, title: str = "") -> list:
        """The pages of change `number`: those with its footer."""
        return [i for i, text in enumerate(self.flat) if f"change {number}: {title.lower()}" in text]

    def first_page(self, number: int, title: str = "") -> int:
        pages = self.pages_of(number, title)
        assert pages, f"no page of change {number} {title}"
        return pages[0]

    def cover_text(self) -> str:
        return " ".join(self.flat[page] for page in self.cover)

    def linked(self, *titles) -> dict:
        """{title: the page its link goes to} of these titles; asserts each link is on one of them."""
        found = {}
        for on, page in self.links:
            title = next((t for t in titles if squashed(t) in on), None)
            assert title is not None, f"a link on {on!r} (to page index {page}), not on a new change's title"
            found[title] = page
        return found


def build(tmp_path: Path, proof_dirs: list, *args, script: Path = PROOF_PDF, env=None, cwd=None) -> Pdf:
    out = tmp_path / "proof.pdf"
    done = subprocess.run([sys.executable, str(script), *map(str, proof_dirs), str(out), *map(str, args)],
                          capture_output=True, encoding="utf-8", errors="replace", timeout=180, env=env,
                          cwd=cwd or tmp_path)
    assert done.returncode == 0, f"proof_pdf.py failed:\n{done.stdout}\n{done.stderr}"
    return Pdf(out)


def assert_marked(pdf: Pdf, changes: list, mark: str, new: set):
    """Each page of a new change: each of its over-heading lines ("Change N of M", "Change N · See it working ·
    Step 1 of 2", ...) carries the mark. An old change's pages: no "new" anywhere."""
    for number, title in enumerate(changes, 1):
        pages = pdf.pages_of(number, title)
        assert pages, f"no page of change {number} {title}"
        for page in pages:
            if title in new:
                over = [" ".join(line.split()) for line in pdf.texts[page].splitlines()
                        if re.search(rf"\b{loose('change')}\s+{number}\b(?!:)", line)]
                assert over, f"page {page + 1} of change {number} has no over-heading: {pdf.flat[page]}"
                assert all(re.search(loose(mark), line) for line in over), \
                    f"page {page + 1} of change {number} ({title}) is not marked {mark!r}: {over}"
            else:
                assert not re.search(NEW, pdf.flat[page]), \
                    f"page {page + 1} of change {number} ({title}, not new) says new: {pdf.flat[page]}"


# --- the tests -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("release", [False, True], ids=["a later commit: new since the last release",
                                                        "the release's build: new in its version"])
def test_the_cover_lists_each_new_change_with_a_link_to_it_and_marks_it_new(tmp_path, qapp, release):
    """Spec 1-5 and 7: the user's case. The PDF of a later commit (or of the release, its commit tagged
    v<version>) lists, before 'In this proof', the changes whose scenario did not exist at v0.1.0, the nearest v tag
    from the parent commit: test_new (a module added) and test_added (a function added to a module), not test_old
    (only its body changed), in PDF order with number, result, system and page; each title links to its first page;
    each is marked NEW in 'In this proof' and on each of its pages. The rest of the cover is as before."""
    repo = releases(tmp_path / "repo", release=release)
    pdf = build(tmp_path, as_ci_records(tmp_path), "--repo", repo)

    heading = f"new in {__version__}" if release else SINCE
    changes = [LATE, OLD, ADDED]
    first = {title: pdf.first_page(number, title) for number, title in enumerate(changes, 1)}
    cover = pdf.cover_text()
    found, proof_at = re.search(loose(heading), cover), re.search(loose("in this proof"), cover).start()
    assert found, f"no {heading!r} on the cover: {cover}"
    assert found.start() < proof_at, "the list is not before 'In this proof'"
    if release:
        assert not re.search(loose("new since"), cover)
    listed = cover[found.end():proof_at]
    late, old, added = (re.escape(title.lower()) for title in changes)
    assert re.search(rf"\b1\b.*?{late}.*?passed.*?windows.*?page {first[LATE] + 1}\b"
                     rf".*?\b3\b.*?{added}.*?failed.*?linux.*?page {first[ADDED] + 1}\b", listed), \
        f"not 1 {LATE} (passed on Windows, page {first[LATE] + 1}) then 3 {ADDED} (failed on Linux, page " \
        f"{first[ADDED] + 1}): {listed}"
    assert OLD.lower() not in listed, f"{OLD} is not new: {listed}"

    assert pdf.linked(LATE, ADDED) == {LATE: first[LATE], ADDED: first[ADDED]}, pdf.links
    assert len(pdf.links) == 2, f"one link per new change: {pdf.links}"

    in_this_proof = cover[proof_at:]
    assert re.search(rf"\b1\b.*?{late}.*?page {first[LATE] + 1}\b.*?\b2\b.*?{old}.*?page {first[OLD] + 1}\b"
                     rf".*?\b3\b.*?{added}.*?page {first[ADDED] + 1}\b", in_this_proof), in_this_proof
    assert re.search(rf"{late}\W*new\b", in_this_proof) and re.search(rf"{added}\W*new\b", in_this_proof), \
        f"no NEW after each new change's title: {in_this_proof}"
    assert len(re.findall(NEW, in_this_proof)) == 2, f"NEW only for the 2 new changes: {in_this_proof}"
    assert "1 of 3 changes did not pass their proof" in cover, cover

    assert_marked(pdf, changes, heading, {LATE, ADDED})


def test_nothing_new_since_the_base_tag(tmp_path, qapp):
    """Spec 3: when no change is new (a scenario whose body changed is not new), the section reads "Nothing new
    since v0.1.0", with no link and no NEW mark anywhere."""
    repo = new_repo(tmp_path / "repo")
    commit(repo, {A: proofs("test_old")}, tag="v0.1.0")
    commit(repo, {A: proofs("test_old", asked="Can it, really?")})
    windows = tmp_path / "proofs"
    record(windows, "test_proof_a.test_old", OLD, 1)
    session(windows, 0, "Windows")
    pdf = build(tmp_path, [windows], "--repo", repo)

    cover = pdf.cover_text()
    nothing = re.search(loose("nothing new since v0.1.0"), cover)
    assert nothing, cover
    assert nothing.start() < re.search(loose("in this proof"), cover).start()
    assert pdf.links == []
    assert len(re.findall(NEW, " ".join(pdf.flat))) == 1, "a NEW mark where nothing is new"


@pytest.mark.parametrize("case", ["not a repository", "no v tag", "shallow checkout", "no git program"])
def test_without_history_or_a_base_tag_no_list_and_no_mark(tmp_path, qapp, case):
    """Spec 3: if git, the history or a base tag is not available, the PDF is built as before: no section, no
    link, no NEW mark anywhere (never a wrong 'new')."""
    env = None
    if case == "not a repository":
        repo = tmp_path / "plain"
        write(repo, {A: proofs("test_old", "test_added"), B: proofs("test_new")})
    elif case == "no v tag":
        repo = new_repo(tmp_path / "repo")
        commit(repo, {A: proofs("test_old")}, tag="nightly")
        commit(repo, {A: proofs("test_old", "test_added"), B: proofs("test_new")})
    elif case == "shallow checkout":  # as actions/checkout makes by default: HEAD only (here the release's tag)
        full = releases(tmp_path / "full", release=True)
        repo = tmp_path / "repo"
        git(tmp_path, "clone", "-q", "--depth", "1", full.as_uri(), repo)
        assert git(repo, "rev-parse", "--is-shallow-repository").strip() == "true"
    else:
        repo = releases(tmp_path / "repo")
        path = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                               if d and not shutil.which("git", path=d))
        assert shutil.which("git", path=path) is None
        env = {**os.environ, "PATH": path}
    pdf = build(tmp_path, as_ci_records(tmp_path), "--repo", repo, env=env)

    saying_new = [text for text in pdf.flat if re.search(NEW, text)]
    assert saying_new == [], f"'new' in the PDF ({case}): {saying_new}"
    assert pdf.links == []


def test_by_default_it_reads_the_repository_proof_pdf_lives_in(tmp_path, qapp):
    """Spec 1: without --repo (as CI runs it), what is new is found in the repository proof_pdf.py is in, not in
    the current folder."""
    repo = releases(tmp_path / "repo")
    (repo / "packaging").mkdir()
    for script in PROOF_PDF.parent.glob("*.py"):
        shutil.copy(script, repo / "packaging")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    pdf = build(tmp_path, as_ci_records(tmp_path), script=repo / "packaging" / "proof_pdf.py", cwd=elsewhere)

    assert re.search(loose(SINCE), pdf.cover_text()), pdf.cover_text()
    assert pdf.linked(LATE, ADDED) == {LATE: pdf.first_page(1, LATE), ADDED: pdf.first_page(3, ADDED)}, pdf.links
    assert_marked(pdf, [LATE, OLD, ADDED], SINCE, {LATE, ADDED})


def test_the_links_reach_each_new_change_when_the_cover_takes_several_pages(tmp_path, qapp):
    """Spec 3 and 5, as in the real PDF (some 30 changes, a cover of several pages): each new change's link goes
    to its own first page, after all the cover's pages, and only the new changes are marked."""
    olds = [f"test_old_{i:02}" for i in range(24)]
    repo = new_repo(tmp_path / "repo")
    commit(repo, {A: proofs(*olds)}, tag="v0.1.0")
    commit(repo, {A: proofs(*olds, "test_added"), B: proofs("test_new")})
    windows = tmp_path / "proofs"
    order = [*olds[:12], "test_new", *olds[12:], "test_added"]
    titles = [LATE if name == "test_new" else ADDED if name == "test_added" else f"Blocks line up, case {name[-2:]}"
              for name in order]
    for minute, (name, title) in enumerate(zip(order, titles)):
        record(windows, f"test_proof_{'b' if name == 'test_new' else 'a'}.{name}", title, minute)
    session(windows, 0, "Windows")
    pdf = build(tmp_path, [windows], "--repo", repo)

    assert len(pdf.cover) >= 2, "the cover should take several pages here"
    assert pdf.linked(LATE, ADDED) == {LATE: pdf.first_page(13, LATE), ADDED: pdf.first_page(26, ADDED)}, pdf.links
    assert len(pdf.links) == 2, pdf.links
    assert_marked(pdf, titles, SINCE, {LATE, ADDED})


def test_ci_builds_the_pdf_from_a_checkout_with_its_history_and_tags():
    """Spec 6: the CI job that builds the PDF checks out the whole history and its tags (fetch-depth: 0), or the
    PDF it attaches to the release would never say what is new."""
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8"))["jobs"]
    builders = [name for name, job in jobs.items()
                if any("proof_pdf.py" in step.get("run", "") for step in job["steps"])]
    assert builders, "no CI job builds the proof PDF"
    for name in builders:
        checkout = next(step for step in jobs[name]["steps"] if step.get("uses", "").startswith("actions/checkout"))
        assert checkout.get("with", {}).get("fetch-depth") == 0, f"CI job {name}: {checkout}"
