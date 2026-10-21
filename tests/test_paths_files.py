"""Copy Files, Move Files and Delete Files, run for real: `taskloom run` on real folders, a real SSH server,
a real SMTP server and the editor. What each test expects comes from what users read (README, the blocks'
descriptions and field help) or from what no file tool may do: lose files or delete what was not asked."""

import json
import os
import stat
import sys
import textwrap

import pytest

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions and links")
# Root may write to read-only files and folders, so permission failures cannot happen for it.
PERMISSIONS = pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions, not as root")


def run(env, text):
    """Write the flow next to the files it works on and run it; (CLI result, run id)."""
    make(env.root, {"flow.yaml": textwrap.dedent(text)})  # in UTF-8, as the editor saves flows
    result = env.taskloom("run", env.root / "flow.yaml")
    return result, env.last_run()[0]


def output(env, run_id, block, port):
    """What a block put out, as the run history (and the editor's data preview) holds it."""
    (previews,), = env.query("SELECT previews FROM blocks WHERE run_id=? AND block=?", run_id, block)
    return json.loads(json.loads(previews)[port])


def error(env, run_id, block):
    return env.query("SELECT error FROM blocks WHERE run_id=? AND block=?", run_id, block)[0][0]


def tree(root):
    """{relative path: text} of every file under root, and '<folder>/' for every empty folder."""
    found = {}
    for folder, dirs, names in os.walk(root):
        rel = os.path.relpath(folder, root)
        if not dirs and not names and rel != ".":
            found[rel.replace(os.sep, "/") + "/"] = None
        for name in names:
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as f:
                found[os.path.relpath(path, root).replace(os.sep, "/")] = f.read()
    return found


def make(root, files: dict):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")  # exactly this text, \n line ends on Windows too


# --- Copy Files ------------------------------------------------------------------------------------------


def test_copy_a_file_to_a_new_name_keeps_the_original(env):
    make(env.root, {"sales.csv": "day,total\n1,10\n"})
    result, run_id = run(env, """
        blocks:
          copy:
            type: files.copy
            config: {source: sales.csv, destination: sales_backup.csv}
    """)

    assert result.returncode == 0, result.stderr
    assert (env.root / "sales_backup.csv").read_text() == "day,total\n1,10\n"
    assert (env.root / "sales.csv").read_text() == "day,total\n1,10\n"
    assert output(env, run_id, "copy", "files") == ["sales_backup.csv"]


def test_copy_into_a_folder_creates_missing_folders_and_keeps_the_name(env):
    make(env.root, {"sales.csv": "s", "existing/other.txt": "o"})
    result, run_id = run(env, """
        blocks:
          new_folder:
            type: files.copy
            config: {source: sales.csv, destination: "out/2026/october/"}
          existing_folder:
            type: files.copy
            config: {source: sales.csv, destination: existing}
    """)

    assert result.returncode == 0, result.stderr
    assert (env.root / "out" / "2026" / "october" / "sales.csv").read_text() == "s"
    assert tree(env.root / "existing") == {"other.txt": "o", "sales.csv": "s"}
    assert output(env, run_id, "new_folder", "files") == [os.path.join("out/2026/october/", "sales.csv")]


def test_copy_a_pattern_copies_only_the_matching_files_of_that_folder(env):
    make(env.root, {"reports/east.csv": "e", "reports/west.csv": "w", "reports/notes.txt": "n",
                    "reports/old/north.csv": "old"})
    result, run_id = run(env, """
        blocks:
          copy:
            type: files.copy
            config: {source: "reports/*.csv", destination: "outbox/"}
    """)

    assert result.returncode == 0, result.stderr
    assert tree(env.root / "outbox") == {"east.csv": "e", "west.csv": "w"}
    assert output(env, run_id, "copy", "files") == [os.path.join("outbox/", "east.csv"), os.path.join("outbox/", "west.csv")]
    assert tree(env.root / "reports") == {"east.csv": "e", "west.csv": "w", "notes.txt": "n", "old/north.csv": "old"}


def test_copy_a_folder_copies_everything_under_it(env):
    files = {"app/config.yaml": "c", "app/lib/a.py": "a", "app/lib/deep/b.py": "b"}
    make(env.root, files)
    result, run_id = run(env, """
        blocks:
          copy:
            type: files.copy
            config: {source: app, destination: "backup/"}
    """)

    assert result.returncode == 0, result.stderr
    assert tree(env.root / "backup") == {"app/config.yaml": "c", "app/lib/a.py": "a", "app/lib/deep/b.py": "b"}
    assert tree(env.root / "app") == {"config.yaml": "c", "lib/a.py": "a", "lib/deep/b.py": "b"}
    assert len(output(env, run_id, "copy", "files")) == 3


def test_copy_replaces_an_existing_file_by_default(env):
    make(env.root, {"new.csv": "new", "out/new.csv": "old, and longer than the new one"})
    result, _ = run(env, """
        blocks:
          copy:
            type: files.copy
            config: {source: new.csv, destination: "out/"}
    """)

    assert result.returncode == 0, result.stderr
    assert (env.root / "out" / "new.csv").read_text() == "new"


def test_copy_with_overwrite_off_refuses_and_copies_nothing(env):
    make(env.root, {"in/a.csv": "new a", "in/b.csv": "new b", "out/b.csv": "keep me"})
    result, run_id = run(env, """
        blocks:
          copy:
            type: files.copy
            config: {source: "in/*.csv", destination: "out/", overwrite: false}
    """)

    assert result.returncode == 1
    assert tree(env.root / "out") == {"b.csv": "keep me"}  # not even a.csv, which was not there
    message = error(env, run_id, "copy")
    assert "already exists" in message and "b.csv" in message


def test_overwrite_written_as_text_is_refused_before_anything_is_copied(env):
    make(env.root, {"new.csv": "new", "out/new.csv": "keep me"})
    flow = env.write("flow.yaml", """
        blocks:
          copy:
            type: files.copy
            config: {source: new.csv, destination: "out/", overwrite: "false"}
    """)

    result = env.taskloom("run", flow)

    assert result.returncode == 2
    assert "overwrite" in result.stderr and "true or false" in result.stderr
    assert (env.root / "out" / "new.csv").read_text() == "keep me"


def test_a_missing_source_fails_clearly_stops_what_follows_and_reaches_the_error_handler(env):
    make(env.root, {"reports/notes.txt": "n"})
    result, run_id = run(env, """
        blocks:
          missing_file:
            type: files.copy
            config: {source: "reports/sales.csv", destination: "out/"}
          no_match:
            type: files.move
            config: {source: "reports/*.csv", destination: "out/"}
          next:
            type: files.delete
            config: {path: "reports/notes.txt"}
          on_error:
            type: logic.python
            config: {outputs: [], code: "open('error.txt', 'w').write(input['error'])"}
        edges:
          - missing_file.files -> next.after
          - missing_file.error -> on_error.input
    """)

    assert result.returncode == 1
    assert "nothing found at reports/sales.csv" in error(env, run_id, "missing_file")
    assert "nothing found at reports/*.csv" in error(env, run_id, "no_match")
    assert env.block_status(run_id)["next"] == "skipped"
    assert (env.root / "reports" / "notes.txt").exists()
    assert "nothing found at reports/sales.csv" in (env.root / "error.txt").read_text()
    assert not (env.root / "out").exists()


def test_paths_with_spaces_and_unicode(env):
    make(env.root, {"Monthly Reports/Résumé ü 2026.csv": "r", "Monthly Reports/日本 データ.csv": "j",
                    "Monthly Reports/keep me.txt": "k"})
    result, run_id = run(env, """
        blocks:
          copy:
            type: files.copy
            config: {source: "Monthly Reports/Résumé ü 2026.csv", destination: "Copies for Zoë/"}
          move:
            type: files.move
            config: {source: "Monthly Reports/日本*.csv", destination: "Archivé 2026/"}
          delete:
            type: files.delete
            config: {path: "Copies for Zoë/Résumé ü 2026.csv"}
        edges:
          - copy.files -> move.after
          - move.files -> delete.after
    """)

    assert result.returncode == 0, result.stderr
    assert tree(env.root / "Archivé 2026") == {"日本 データ.csv": "j"}
    assert tree(env.root / "Monthly Reports") == {"Résumé ü 2026.csv": "r", "keep me.txt": "k"}
    assert tree(env.root / "Copies for Zoë") == {}
    assert output(env, run_id, "delete", "deleted") == ["Copies for Zoë/Résumé ü 2026.csv"]


def test_full_local_paths_written_either_way(env):
    """Full paths, as a file dialog gives them; on Windows they start with a drive letter (C:\\... or C:/...)."""
    make(env.root, {"in/a.csv": "a", "in/b.csv": "b", "in/notes.txt": "n"})
    result, run_id = run(env, f"""
        blocks:
          copy:
            type: files.copy
            config: {{source: '{env.root / "in" / "a.csv"}', destination: '{env.root / "out"}{os.sep}'}}
          move:
            type: files.move
            config: {{source: '{(env.root / "in").as_posix()}/*.csv', destination: '{(env.root / "moved").as_posix()}/'}}
          delete:
            type: files.delete
            config: {{path: '{env.root / "in" / "notes.txt"}'}}
        edges:
          - copy.files -> move.after
          - move.files -> delete.after
    """)

    assert result.returncode == 0, result.stderr
    assert tree(env.root / "out") == {"a.csv": "a"}
    assert tree(env.root / "moved") == {"a.csv": "a", "b.csv": "b"}
    assert tree(env.root / "in") == {}
    (copied,) = output(env, run_id, "copy", "files")
    assert os.path.samefile(copied, env.root / "out" / "a.csv")
    (deleted,) = output(env, run_id, "delete", "deleted")
    assert deleted.endswith("notes.txt")


def test_a_large_binary_file_is_copied_byte_for_byte(env):
    data = os.urandom(3 * (1 << 20) + 12345)  # several chunks, the last one partial
    (env.root / "dump.bin").write_bytes(data)
    result, _ = run(env, """
        blocks:
          copy:
            type: files.copy
            config: {source: dump.bin, destination: "copy/dump.bin"}
    """)

    assert result.returncode == 0, result.stderr
    assert (env.root / "copy" / "dump.bin").read_bytes() == data


def test_parameters_fill_in_source_and_destination(env):
    make(env.root, {"exports/sales_2026-10-02.csv": "today", "exports/sales_2026-10-01.csv": "yesterday"})
    result, _ = run(env, """
        params:
          day: {type: date, value: 2026-10-02}
        blocks:
          archive:
            type: files.copy
            config: {source: "exports/sales_{day:%Y-%m-%d}.csv", destination: "archive/{day:%Y}/{day:%m}/"}
    """)

    assert result.returncode == 0, result.stderr
    assert tree(env.root / "archive") == {"2026/10/sales_2026-10-02.csv": "today"}


def test_copied_files_go_on_as_email_attachments(env, smtp):
    make(env.root, {"reports/east.csv": "region,total\nEast,10\n", "reports/west.csv": "region,total\nWest,20\n"})
    result, _ = run(env, """
        blocks:
          collect:
            type: files.copy
            config: {source: "reports/*.csv", destination: "outbox/"}
          mail:
            type: email.send
            config: {connection: mailer, to: [team@example.com], subject: "Regional totals"}
        edges:
          - collect.files -> mail.attachments
    """)

    assert result.returncode == 0, result.stderr
    (message,) = smtp.messages
    attached = {a.get_filename(): a.get_content() for a in message.iter_attachments()}
    assert {name: (text if isinstance(text, str) else text.decode()) for name, text in attached.items()} == {
        "east.csv": "region,total\nEast,10\n", "west.csv": "region,total\nWest,20\n"}


def test_a_pattern_that_finds_one_file_copied_to_a_file_path(env):
    """The day's export has a time stamp in its name; the next system wants it under a fixed name."""
    make(env.root, {"exports/sales_2026-10-02_0715.csv": "today"})
    result, _ = run(env, """
        blocks:
          latest:
            type: files.copy
            config: {source: "exports/sales_*.csv", destination: "upload/sales.csv"}
    """)

    assert result.returncode == 0, result.stderr
    assert (env.root / "upload" / "sales.csv").is_file(), f"made {tree(env.root / 'upload')}"
    assert (env.root / "upload" / "sales.csv").read_text() == "today"


def test_a_file_name_with_brackets_is_that_file(env):
    make(env.root, {"in/report[1].csv": "the one asked for", "in/report1.csv": "another file"})
    result, _ = run(env, """
        blocks:
          copy:
            type: files.copy
            config: {source: "in/report[1].csv", destination: "out/"}
    """)

    assert result.returncode == 0, result.stderr
    assert tree(env.root / "out") == {"report[1].csv": "the one asked for"}


@PERMISSIONS
def test_copy_into_a_read_only_folder_fails_clearly(env):
    make(env.root, {"a.csv": "a"})
    locked = env.root / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        result, run_id = run(env, """
            blocks:
              copy:
                type: files.copy
                config: {source: a.csv, destination: "locked/"}
        """)
    finally:
        locked.chmod(0o755)

    assert result.returncode == 1
    message = error(env, run_id, "copy")
    assert "Permission denied" in message and "locked" in message
    assert list(locked.iterdir()) == []


@PERMISSIONS
def test_a_read_only_file_can_be_copied_and_moved(env):
    make(env.root, {"in/frozen.csv": "frozen", "in/also.csv": "also"})
    for name in ("frozen.csv", "also.csv"):
        (env.root / "in" / name).chmod(0o444)
    result, _ = run(env, """
        blocks:
          copy:
            type: files.copy
            config: {source: "in/frozen.csv", destination: "copies/"}
          move:
            type: files.move
            config: {source: "in/also.csv", destination: "moved/"}
    """)

    assert result.returncode == 0, result.stderr
    assert (env.root / "copies" / "frozen.csv").read_text() == "frozen"
    assert (env.root / "moved" / "also.csv").read_text() == "also"
    assert sorted(p.name for p in (env.root / "in").iterdir()) == ["frozen.csv"]


# --- Move Files ------------------------------------------------------------------------------------------


def test_move_a_file_to_a_new_name(env):
    make(env.root, {"inbox/today.csv": "t"})
    result, run_id = run(env, """
        blocks:
          move:
            type: files.move
            config: {source: inbox/today.csv, destination: done/2026-10-02.csv}
    """)

    assert result.returncode == 0, result.stderr
    assert tree(env.root / "done") == {"2026-10-02.csv": "t"}
    assert list((env.root / "inbox").iterdir()) == []
    assert output(env, run_id, "move", "files") == ["done/2026-10-02.csv"]


def test_move_a_folder_moves_everything_under_it(env):
    make(env.root, {"build/app.jar": "jar", "build/lib/x.jar": "x", "build/lib/deep/y.jar": "y"})
    result, _ = run(env, """
        blocks:
          move:
            type: files.move
            config: {source: build, destination: "releases/"}
    """)

    assert result.returncode == 0, result.stderr
    assert tree(env.root / "releases") == {"build/app.jar": "jar", "build/lib/x.jar": "x", "build/lib/deep/y.jar": "y"}
    assert not (env.root / "build").exists()


def test_move_a_pattern_leaves_the_files_that_do_not_match(env):
    make(env.root, {"inbox/a.csv": "a", "inbox/b.csv": "b", "inbox/readme.txt": "r", "inbox/sub/c.csv": "c"})
    result, run_id = run(env, """
        blocks:
          move:
            type: files.move
            config: {source: "inbox/*.csv", destination: "processed/"}
    """)

    assert result.returncode == 0, result.stderr
    assert tree(env.root / "processed") == {"a.csv": "a", "b.csv": "b"}
    assert tree(env.root / "inbox") == {"readme.txt": "r", "sub/c.csv": "c"}
    assert output(env, run_id, "move", "files") == [os.path.join("processed/", "a.csv"), os.path.join("processed/", "b.csv")]


def test_move_with_overwrite_off_keeps_the_source_when_the_target_exists(env):
    make(env.root, {"inbox/a.csv": "new", "processed/a.csv": "old"})
    result, run_id = run(env, """
        blocks:
          move:
            type: files.move
            config: {source: "inbox/a.csv", destination: "processed/", overwrite: false}
    """)

    assert result.returncode == 1
    assert "already exists" in error(env, run_id, "move")
    assert (env.root / "inbox" / "a.csv").read_text() == "new"
    assert (env.root / "processed" / "a.csv").read_text() == "old"


@PERMISSIONS
def test_a_move_whose_copy_fails_keeps_every_source(env):
    make(env.root, {"inbox/a.csv": "a", "inbox/b.csv": "b", "processed/b.csv": "read-only"})
    (env.root / "processed" / "b.csv").chmod(0o444)
    result, run_id = run(env, """
        blocks:
          move:
            type: files.move
            config: {source: "inbox/*.csv", destination: "processed/"}
    """)

    assert result.returncode == 1
    assert "Permission denied" in error(env, run_id, "move")
    assert tree(env.root / "inbox") == {"a.csv": "a", "b.csv": "b"}
    assert (env.root / "processed" / "b.csv").read_text() == "read-only"


@PERMISSIONS
def test_a_move_out_of_a_folder_it_cannot_delete_from_fails_and_does_not_count_as_moved(env):
    """A share you may read but not change: the files are not moved, so the step after the move must not run
    (otherwise the next run would take the same files again)."""
    make(env.root, {"share/a.csv": "a", "share/b.csv": "b", "processed/keep.txt": "k"})
    share = env.root / "share"
    share.chmod(0o555)
    try:
        result, run_id = run(env, """
            blocks:
              move:
                type: files.move
                config: {source: "share/*.csv", destination: "out/"}
              next:
                type: files.delete
                config: {path: "processed/keep.txt"}
            edges:
              - move.files -> next.after
        """)
    finally:
        share.chmod(0o755)

    assert result.returncode == 1
    status = env.block_status(run_id)
    assert (status["move"], status["next"]) == ("failed", "skipped")
    message = error(env, run_id, "move")
    assert "Permission denied" in message and "share" in message
    assert tree(share) == {"a.csv": "a", "b.csv": "b"}
    assert (env.root / "processed" / "keep.txt").exists()


@pytest.mark.parametrize("source", ["data/*", "data"])
def test_moving_into_a_folder_inside_the_source_loses_nothing(env, source):
    """Archiving into a subfolder of the folder being moved (e.g. data/* -> data/archive/, run every day)."""
    make(env.root, {"data/today.csv": "today", "data/archive/yesterday.csv": "yesterday"})
    result, _ = run(env, f"""
        blocks:
          archive:
            type: files.move
            config: {{source: "{source}", destination: "data/archive/"}}
    """)

    kept = set(tree(env.root / "data").values())
    assert {"today", "yesterday"} <= kept, f"files lost (run {'succeeded' if result.returncode == 0 else 'failed'})"


def test_move_keeps_empty_folders(env):
    make(env.root, {"project/notes.txt": "n"})
    (env.root / "project" / "empty").mkdir()
    (env.root / "drafts").mkdir()  # an empty folder of its own
    result, _ = run(env, """
        blocks:
          move_project:
            type: files.move
            config: {source: project, destination: "moved/"}
          move_drafts:
            type: files.move
            config: {source: drafts, destination: "moved/"}
    """)

    assert result.returncode == 0, result.stderr
    assert tree(env.root / "moved") == {"project/notes.txt": "n", "project/empty/": None, "drafts/": None}


def test_moving_files_of_the_same_name_from_several_folders_loses_nothing(env):
    make(env.root, {"east/report.csv": "east", "west/report.csv": "west"})
    result, _ = run(env, """
        blocks:
          gather:
            type: files.move
            config: {source: "*/report.csv", destination: "all/", overwrite: false}
    """)

    kept = {text for folder in ("east", "west", "all") for text in tree(env.root / folder).values()}
    assert {"east", "west"} <= kept, f"files lost (run {'succeeded' if result.returncode == 0 else 'failed'})"


def test_moving_files_of_the_same_name_with_overwrite_left_on_loses_nothing(env):
    """Overwrite is on unless you untick it, so this is the move most users make."""
    make(env.root, {"east/report.csv": "east", "west/report.csv": "west"})
    result, _ = run(env, """
        blocks:
          gather:
            type: files.move
            config: {source: "*/report.csv", destination: "all/"}
    """)

    kept = {text for folder in ("east", "west", "all") for text in tree(env.root / folder).values()}
    assert {"east", "west"} <= kept, f"files lost (run {'succeeded' if result.returncode == 0 else 'failed'})"


@pytest.mark.parametrize("block, overwrite", [("files.copy", True), ("files.move", True), ("files.move", False)])
def test_gathering_files_whose_names_differ_only_in_case_loses_nothing(env, block, overwrite):
    """Report.csv and report.csv are one name on Windows, macOS and Windows network folders: an error, or both kept
    apart, are both fine; one written over the other is not (on a case-sensitive disk both simply arrive)."""
    make(env.root, {"East/Report.csv": "east", "west/report.csv": "west"})
    result, _ = run(env, f"""
        blocks:
          gather:
            type: {block}
            config: {{source: "*/*.csv", destination: "all/", overwrite: {str(overwrite).lower()}}}
    """)

    arrived = set(tree(env.root / "all").values())
    kept = set(tree(env.root / "East").values()) | set(tree(env.root / "west").values()) | arrived
    assert {"east", "west"} <= kept, f"files lost (run {'succeeded' if result.returncode == 0 else 'failed'})"
    if result.returncode == 0:
        assert {"east", "west"} <= arrived, f"reported success, but all/ holds {tree(env.root / 'all')}"


@pytest.mark.parametrize("block", ["files.copy", "files.move"])
def test_a_pattern_that_finds_several_files_sent_to_a_file_path_loses_nothing(env, block):
    """Several matches cannot all become one file: an error or a folder of them are both fine, a lost file is not."""
    make(env.root, {"exports/sales_2026-10-01.csv": "first", "exports/sales_2026-10-02.csv": "second"})
    result, _ = run(env, f"""
        blocks:
          latest:
            type: {block}
            config: {{source: "exports/sales_*.csv", destination: "upload/sales.csv"}}
    """)

    arrived = set(tree(env.root / "upload").values())
    assert {"first", "second"} <= set(tree(env.root / "exports").values()) | arrived, "files lost"
    if result.returncode == 0:
        assert {"first", "second"} <= arrived, "reported success, but one file was written over the other"


# --- Delete Files ----------------------------------------------------------------------------------------


def test_delete_a_file_leaves_the_files_next_to_it(env):
    make(env.root, {"out/old.csv": "o", "out/new.csv": "n", "out/old.csv.bak": "b"})
    result, run_id = run(env, """
        blocks:
          delete:
            type: files.delete
            config: {path: out/old.csv}
    """)

    assert result.returncode == 0, result.stderr
    assert tree(env.root / "out") == {"new.csv": "n", "old.csv.bak": "b"}
    assert output(env, run_id, "delete", "deleted") == ["out/old.csv"]


def test_delete_a_folder_and_everything_in_it(env):
    make(env.root, {"tmp/a.txt": "a", "tmp/sub/deep/b.txt": "b", "tmpfiles/keep.txt": "k", "tmp.txt": "t"})
    result, run_id = run(env, """
        blocks:
          delete:
            type: files.delete
            config: {path: tmp}
    """)

    assert result.returncode == 0, result.stderr
    assert not (env.root / "tmp").exists()
    assert (env.root / "tmpfiles" / "keep.txt").exists() and (env.root / "tmp.txt").exists()
    assert output(env, run_id, "delete", "deleted") == ["tmp"]


def test_delete_a_pattern_deletes_only_the_matches_in_that_folder(env):
    make(env.root, {"work/a.tmp": "a", "work/b.tmp": "b", "work/keep.csv": "k", "work/sub/c.tmp": "c",
                    "other/d.tmp": "d"})
    result, run_id = run(env, """
        blocks:
          delete:
            type: files.delete
            config: {path: "work/*.tmp"}
    """)

    assert result.returncode == 0, result.stderr
    assert tree(env.root / "work") == {"keep.csv": "k", "sub/c.tmp": "c"}
    assert tree(env.root / "other") == {"d.tmp": "d"}
    assert output(env, run_id, "delete", "deleted") == [os.path.join("work", "a.tmp"), os.path.join("work", "b.tmp")]


def test_delete_something_that_is_not_there_deletes_nothing(env):
    make(env.root, {"work/keep.csv": "k"})
    result, run_id = run(env, """
        blocks:
          missing_file:
            type: files.delete
            config: {path: work/gone.csv}
          no_match:
            type: files.delete
            config: {path: "work/*.tmp"}
    """)

    assert tree(env.root / "work") == {"keep.csv": "k"}
    assert set(env.block_status(run_id).values()) <= {"success", "failed"}, result.stderr


@POSIX
def test_delete_a_folder_never_deletes_what_its_links_point_to(env):
    make(env.root, {"shared/important.csv": "i", "shared/sub/more.csv": "m", "elsewhere.csv": "e", "cache/a.tmp": "a"})
    (env.root / "cache" / "to_shared").symlink_to(env.root / "shared", target_is_directory=True)
    (env.root / "cache" / "to_file.csv").symlink_to(env.root / "elsewhere.csv")
    result, _ = run(env, """
        blocks:
          delete:
            type: files.delete
            config: {path: cache}
    """)

    assert result.returncode == 0, result.stderr
    assert not os.path.lexists(env.root / "cache")
    assert tree(env.root / "shared") == {"important.csv": "i", "sub/more.csv": "m"}
    assert (env.root / "elsewhere.csv").read_text() == "e"


@POSIX
def test_delete_a_pattern_that_matches_a_link_to_a_folder_removes_the_link_only(env):
    make(env.root, {"shared/important.csv": "i", "cache/a.tmp": "a", "cache/z.tmp": "z"})
    (env.root / "cache" / "m_link").symlink_to(env.root / "shared", target_is_directory=True)
    result, run_id = run(env, """
        blocks:
          delete:
            type: files.delete
            config: {path: "cache/*"}
    """)

    assert tree(env.root / "shared") == {"important.csv": "i"}  # never what the link points to
    assert result.returncode == 0, error(env, run_id, "delete")
    assert list((env.root / "cache").iterdir()) == []


def test_delete_a_name_with_brackets_deletes_that_file_only(env):
    make(env.root, {"out/report[1].csv": "asked for", "out/report1.csv": "not asked for"})
    result, _ = run(env, """
        blocks:
          delete:
            type: files.delete
            config: {path: "out/report[1].csv"}
    """)

    assert (env.root / "out" / "report1.csv").exists(), "deleted a file that was not asked for"
    assert result.returncode == 0, result.stderr
    assert not (env.root / "out" / "report[1].csv").exists()


@PERMISSIONS
def test_delete_in_a_read_only_folder_fails_clearly_and_keeps_the_file(env):
    make(env.root, {"locked/a.csv": "a"})
    locked = env.root / "locked"
    locked.chmod(0o555)
    try:
        result, run_id = run(env, """
            blocks:
              delete:
                type: files.delete
                config: {path: locked/a.csv}
        """)
    finally:
        locked.chmod(0o755)

    assert result.returncode == 1
    message = error(env, run_id, "delete")
    assert "Permission denied" in message and "a.csv" in message
    assert (locked / "a.csv").read_text() == "a"


def test_read_only_files_are_moved_and_deleted_or_fail_clearly_keeping_them(env):
    """Read-only here is the file's own flag (the Windows read-only attribute; owner read only on Linux).
    Either the move or delete happens, or the block fails naming the file and keeps it; never success with it left."""
    make(env.root, {"in/frozen.csv": "frozen", "old/locked.csv": "locked", "cache/a.tmp": "a", "cache/ro.tmp": "ro"})
    read_only = {"move": env.root / "in" / "frozen.csv", "delete_file": env.root / "old" / "locked.csv",
                 "delete_folder": env.root / "cache" / "ro.tmp"}
    for path in read_only.values():
        os.chmod(path, stat.S_IREAD)
    try:
        result, run_id = run(env, """
            blocks:
              move:
                type: files.move
                config: {source: "in/frozen.csv", destination: "moved/"}
              delete_file:
                type: files.delete
                config: {path: "old/locked.csv"}
              delete_folder:
                type: files.delete
                config: {path: cache}
        """)
    finally:
        for path in read_only.values():
            if path.exists():
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)

    status = env.block_status(run_id)
    for block, path in read_only.items():
        if status[block] == "success":
            assert not path.exists(), f"{block} reported success, but {path.name} is still there"
        else:
            assert status[block] == "failed", result.stderr
            assert path.exists(), f"{block} failed, but {path.name} is gone"
            assert path.name in error(env, run_id, block)
    if status["move"] == "success":
        assert (env.root / "moved" / "frozen.csv").read_text() == "frozen"
    else:
        assert read_only["move"].read_text() == "frozen"


# --- To and from a server --------------------------------------------------------------------------------


@POSIX
def test_folders_with_subfolders_spaces_and_unicode_to_and_from_a_server(env, server):
    make(env.root, {"Site Files/index.html": "home", "Site Files/img/logo ü.svg": "logo", "Site Files/a/b/c.txt": "c"})
    make(server.home_path, {"outbox/r 1.csv": "one", "outbox/r 2.csv": "two", "outbox/keep.txt": "k"})
    result, run_id = run(env, """
        blocks:
          upload:
            type: files.copy
            config: {source: "Site Files", destination: "appserver:~/deploy/2026 v1/"}
          download:
            type: files.copy
            config: {source: "appserver:~/deploy/2026 v1/Site Files", destination: "check/"}
          fetch:
            type: files.move
            config: {source: "appserver:~/outbox/*.csv", destination: "Inbox ✓/"}
          clean:
            type: files.delete
            config: {path: "appserver:~/deploy"}
        edges:
          - upload.files -> download.after
          - download.files -> fetch.after
          - fetch.files -> clean.after
    """)

    assert result.returncode == 0, result.stderr
    site = {"index.html": "home", "img/logo ü.svg": "logo", "a/b/c.txt": "c"}
    assert tree(env.root / "check" / "Site Files") == site
    assert tree(env.root / "Inbox ✓") == {"r 1.csv": "one", "r 2.csv": "two"}
    assert tree(server.home_path / "outbox") == {"keep.txt": "k"}
    assert not (server.home_path / "deploy").exists()
    uploaded = output(env, run_id, "upload", "files")  # one entry per file sent, each ending in its path
    assert sorted(u.rsplit("Site Files/", 1)[-1] for u in uploaded) == sorted(site), uploaded


@POSIX
def test_overwrite_off_keeps_the_servers_files(env, server):
    make(env.root, {"out/a.csv": "new a", "out/b.csv": "new b"})
    make(server.home_path, {"incoming/b.csv": "keep me"})
    result, run_id = run(env, """
        blocks:
          upload_all:
            type: files.copy
            config: {source: "out/*.csv", destination: "appserver:~/incoming/", overwrite: false}
          move_one:
            type: files.move
            config: {source: "out/b.csv", destination: "appserver:~/incoming/b.csv", overwrite: false}
          upload_new:
            type: files.copy
            config: {source: "out/a.csv", destination: "appserver:~/fresh/", overwrite: false}
    """)

    assert tree(server.home_path / "incoming") == {"b.csv": "keep me"}  # not even a.csv, which was not there
    assert tree(env.root / "out") == {"a.csv": "new a", "b.csv": "new b"}
    assert tree(server.home_path / "fresh") == {"a.csv": "new a"}  # nothing there yet, so it goes
    assert result.returncode == 1
    status = env.block_status(run_id)
    assert (status["upload_all"], status["move_one"], status["upload_new"]) == ("failed", "failed", "success")
    for block in ("upload_all", "move_one"):
        message = error(env, run_id, block)
        assert "already exists" in message and "b.csv" in message


@POSIX
def test_overwrite_on_replaces_longer_files_to_and_from_a_server(env, server):
    """Overwrite is on unless you untick it: the file already there is replaced whole, not written over its start."""
    make(env.root, {"out/report.csv": "new", "inbox/report.csv": "old local copy, longer than the new one"})
    make(server.home_path, {"incoming/report.csv": "old server copy, longer than the new one", "outbox/report.csv": "fresh"})
    result, _ = run(env, """
        blocks:
          upload:
            type: files.copy
            config: {source: out/report.csv, destination: "appserver:~/incoming/"}
          fetch:
            type: files.move
            config: {source: "appserver:~/outbox/report.csv", destination: "inbox/"}
    """)

    assert result.returncode == 0, result.stderr
    assert (server.home_path / "incoming" / "report.csv").read_text() == "new"
    assert (env.root / "inbox" / "report.csv").read_text() == "fresh"
    assert tree(server.home_path / "outbox") == {}


@PERMISSIONS
def test_changes_a_server_refuses_fail_clearly_and_change_nothing(env, server):
    """A read-only file or folder on the server: the block fails with the server's refusal and changes nothing."""
    make(env.root, {"out/a.csv": "new a", "out/b.csv": "new b"})
    make(server.home_path, {"incoming/b.csv": "read-only", "locked/old.csv": "old"})
    (server.home_path / "incoming" / "b.csv").chmod(0o444)
    locked = server.home_path / "locked"
    locked.chmod(0o555)
    try:
        result, run_id = run(env, """
            blocks:
              over_file:
                type: files.copy
                config: {source: out/b.csv, destination: "appserver:~/incoming/"}
              into_folder:
                type: files.copy
                config: {source: out/a.csv, destination: "appserver:~/locked/"}
              delete:
                type: files.delete
                config: {path: "appserver:~/locked/old.csv"}
        """)
    finally:
        locked.chmod(0o755)

    assert result.returncode == 1
    assert (server.home_path / "incoming" / "b.csv").read_text() == "read-only"
    assert tree(locked) == {"old.csv": "old"}
    status = env.block_status(run_id)
    for block in ("over_file", "into_folder", "delete"):
        message = error(env, run_id, block)
        assert status[block] == "failed" and "Permission denied" in message, (block, status[block], message)


@PERMISSIONS
def test_a_move_from_a_server_folder_it_cannot_change_fails_and_does_not_count_as_moved(env, server):
    """Fetching from a server outbox you may read but not change: the files stay there, so the step after the move
    must not run (the next run takes them again)."""
    make(server.home_path, {"outbox/a.csv": "a", "outbox/b.csv": "b"})
    make(env.root, {"processed/keep.txt": "k"})
    outbox = server.home_path / "outbox"
    outbox.chmod(0o555)
    try:
        result, run_id = run(env, """
            blocks:
              fetch:
                type: files.move
                config: {source: "appserver:~/outbox/*.csv", destination: "inbox/"}
              next:
                type: files.delete
                config: {path: "processed/keep.txt"}
            edges:
              - fetch.files -> next.after
        """)
    finally:
        outbox.chmod(0o755)

    assert result.returncode == 1
    status = env.block_status(run_id)
    assert (status["fetch"], status["next"]) == ("failed", "skipped")
    assert tree(outbox) == {"a.csv": "a", "b.csv": "b"}
    assert (env.root / "processed" / "keep.txt").exists()
    message = error(env, run_id, "fetch")
    assert "Permission denied" in message, message


@POSIX
def test_a_server_file_copied_onto_itself_written_two_ways_is_refused(env, server):
    make(server.home_path, {"app/config.yaml": "port: 8080\n"})
    result, run_id = run(env, f"""
        blocks:
          copy:
            type: files.copy
            config: {{source: "appserver:~/app/config.yaml", destination: "appserver:{server.home_path}/app/"}}
    """)

    assert (server.home_path / "app" / "config.yaml").read_text() == "port: 8080\n", "the file was emptied"
    assert result.returncode == 1
    assert "onto itself" in error(env, run_id, "copy")


@POSIX
@pytest.mark.parametrize("source, destination", [("~/logs/*", "~/logs/archive/"), ("~/logs", "~/logs/archive/"),
                                                 ("~/logs/*", "{home}/logs/archive/")])
def test_moving_into_a_folder_inside_the_source_on_a_server_loses_nothing(env, server, source, destination):
    """The daily archive job on a server: logs/* -> logs/archive/ (the folder itself may be written in full)."""
    make(server.home_path, {"logs/today.log": "today", "logs/archive/yesterday.log": "yesterday"})
    destination = destination.format(home=server.home_path)
    result, _ = run(env, f"""
        blocks:
          archive:
            type: files.move
            config: {{source: "appserver:{source}", destination: "appserver:{destination}"}}
    """)

    kept = set(tree(server.home_path / "logs").values())
    assert {"today", "yesterday"} <= kept, f"files lost (run {'succeeded' if result.returncode == 0 else 'failed'})"


@POSIX
def test_delete_on_a_server_never_deletes_what_links_point_to(env, server):
    """deploy/current -> releases/r2 is the usual layout; deleting deploy (or cache/*) removes links, not releases."""
    home = server.home_path
    make(home, {"releases/r1/app.jar": "r1", "releases/r2/app.jar": "r2", "shared.cfg": "cfg",
                "deploy/start.sh": "s", "cache/a.tmp": "a"})
    (home / "deploy" / "current").symlink_to("../releases/r2", target_is_directory=True)
    (home / "deploy" / "app.cfg").symlink_to(home / "shared.cfg")
    (home / "cache" / "previous").symlink_to(home / "releases" / "r1", target_is_directory=True)
    result, run_id = run(env, """
        blocks:
          clean_deploy:
            type: files.delete
            config: {path: "appserver:~/deploy"}
          clean_cache:
            type: files.delete
            config: {path: "appserver:~/cache/*"}
    """)

    assert tree(home / "releases") == {"r1/app.jar": "r1", "r2/app.jar": "r2"}  # never what a link points to
    assert (home / "shared.cfg").read_text() == "cfg"
    assert result.returncode == 0, result.stderr
    assert not os.path.lexists(home / "deploy")
    assert list((home / "cache").iterdir()) == []
    assert sorted(d.rsplit("/", 1)[-1] for d in output(env, run_id, "clean_cache", "deleted")) == ["a.tmp", "previous"]


@POSIX
def test_a_misspelled_connection_name_is_an_error_not_a_local_folder(env, server):
    make(env.root, {"out/a.csv": "a"})
    result, run_id = run(env, """
        blocks:
          upload:
            type: files.copy
            config: {source: "out/a.csv", destination: "apserver:~/incoming/"}
    """)

    assert result.returncode == 1, "reported success, but nothing reached the server"
    assert "apserver" in error(env, run_id, "upload")
    assert not (env.root / "apserver:~").exists()


# --- In the editor ---------------------------------------------------------------------------------------


def test_copy_files_set_up_in_the_editor(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QCheckBox, QFileDialog, QLineEdit

    from test_editor import add_from_palette, drag_port_to_port, wait_done

    make(tmp_path, {"reports/east.csv": "e", "reports/west.csv": "w"})
    flow_path = tmp_path / "copy.yaml"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(flow_path), ""))
    tab = win.new_flow()
    add_from_palette(win, "Copy Files")
    (copy,) = [b for b in tab.doc.blocks if b != "start"]
    tab.scene.nodes[copy].setSelected(True)

    def field(help_text):
        return next(e for e in win.properties.findChildren(QLineEdit) if e.toolTip() == help_text)

    for help_text, value in (("A file, folder or pattern; server paths as connection:path", "reports/*.csv"),
                             ("A file path, or a folder (end with /)", "outbox/")):
        edit = field(help_text)
        edit.setText(value)
        edit.editingFinished.emit()
    (overwrite,) = win.properties.findChildren(QCheckBox)
    assert overwrite.isChecked()  # on unless you untick it
    overwrite.setChecked(False)
    drag_port_to_port(tab, "start", "start", copy, "after")
    assert win.save(tab)

    assert tab.doc.blocks[copy]["config"] == {"source": "reports/*.csv", "destination": "outbox/", "overwrite": False}
    win.run_flow(tab)
    wait_done(qtbot, tab)
    assert tab.last["status"] == "success"
    assert tree(tmp_path / "outbox") == {"east.csv": "e", "west.csv": "w"}

    (tmp_path / "reports" / "east.csv").write_text("changed")
    win.run_flow(tab)
    qtbot.waitUntil(lambda: len(tab.runs) == 2 and not tab.running and tab.last["status"] != "success", timeout=60_000)
    assert tab.last["status"] == "failed" and tab.scene.nodes[copy].status == "failed"
    assert (tmp_path / "outbox" / "east.csv").read_text() == "e"


@POSIX
def test_a_missing_server_source_fails_clearly(env, server):
    make(server.home_path, {"logs/app.txt": "t"})
    result, run_id = run(env, """
        blocks:
          missing_file:
            type: files.copy
            config: {source: "appserver:~/logs/app.log", destination: "got/"}
          no_match:
            type: files.copy
            config: {source: "appserver:~/logs/*.log", destination: "got/"}
          missing_folder:
            type: files.move
            config: {source: "appserver:~/old_logs/*.log", destination: "got/"}
    """)

    assert result.returncode == 1
    assert "nothing found at appserver:~/logs/app.log" in error(env, run_id, "missing_file")
    assert "nothing found at appserver:~/logs/*.log" in error(env, run_id, "no_match")
    assert "old_logs" in error(env, run_id, "missing_folder")
    assert not (env.root / "got").exists()
