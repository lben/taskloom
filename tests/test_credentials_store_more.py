"""Stage 5 (credentials) as a user meets the list and the store, beyond tests/test_credentials_store.py: the exact
lines `taskloom secret list` prints, the exact list file credentials.yaml that `taskloom secret set` writes (names
only, in the order made), the exact messages for a name refused, a damaged list, a delete refused and a value not
set, where `secret set` says it saved, Validate's message for a reference that is no name, and Save's all or nothing
as the Credentials window calls it (credentials.save). Values are in the tests' stand-in store (tests/credential_store);
promises: docs/promises/credentials.yaml (CR-*)."""

import os
import sys

import keyring.backends.fail
import keyring.core
import pytest
import yaml
from keyring.errors import KeyringError
from taskloom_test_store import FileStore

from taskloom import credentials
from test_credentials_refs import api, validate, write_flow
from test_credentials_store import NO_STORE, ON_A_PC, card_entries, entries, plant, run, secret_set, taskloom, write_cards

LINUX = pytest.mark.skipif(ON_A_PC, reason="secrets.yaml is Linux's place for values until stage 6 (CR-13)")
PC = pytest.mark.skipif(not ON_A_PC, reason="on Windows and macOS there is the credential store or nothing (CR-13)")
LOCKABLE = pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                              reason="a folder made read-only stops only a user who is not root, and not on Windows")
RULE = "use letters, digits, _ and -"
NEEDS = "a reference needs CARD.key, a card and one of its keys (e.g. UAT.password)"


def list_text(env) -> str:
    return (env.home / "credentials.yaml").read_text(encoding="utf-8")


def refused(done, *lines: str, code: int = 1):
    """The command printed nothing and said exactly `lines` as its errors, with exit code `code`."""
    assert (done.returncode, done.stdout, done.stderr.splitlines()) == (code, "", list(lines))


# --- taskloom secret list ------------------------------------------------------------------------------------------

@pytest.mark.invariant("CR-9")
@pytest.mark.invariant("CR-15")
def test_secret_list_prints_each_card_its_keys_and_pointers_in_list_order_and_which_key_has_no_value(
        env, credential_store):
    """Each card on a line (with the card it takes its values from), then each key indented, a pointer as key -> its
    row, and "no value stored" after the one key with no value: the lines exactly, and no value among them."""
    write_cards(env, {"UAT": {"rows": {"host": None, "password": None, "token": None}},
                      "PROD": {"rows": {"password": None}},
                      "DEFAULT": {"uses": "UAT", "rows": {"archive": "PROD.password", "username": None}}})
    plant(credential_store, {"UAT.host": "uat-db.example.test", "UAT.password": "Uat-Pass-31",
                             "PROD.password": "Prod-Pass-31", "DEFAULT.username": "made-up-user-31"})

    shown = taskloom(env, "secret", "list")

    assert (shown.returncode, shown.stderr) == (0, "")
    assert shown.stdout.splitlines() == ["UAT", "  host", "  password", "  token  (no value stored)",
                                         "PROD", "  password",
                                         "DEFAULT (takes its values from UAT)", "  archive -> PROD.password",
                                         "  username"]


@pytest.mark.invariant("CR-9")
def test_secret_list_with_no_card_yet_says_how_to_store_a_value(env, credential_store):
    """No list file, then one with no card: both times one line saying how to store the first value."""
    first = taskloom(env, "secret", "list")
    (env.home / "credentials.yaml").write_text("cards:\n", encoding="utf-8")
    second = taskloom(env, "secret", "list")

    for shown in (first, second):
        assert (shown.returncode, shown.stderr) == (0, "")
        assert shown.stdout.splitlines() == ["no cards yet: store a value with taskloom secret set CARD.key"]


# --- the list file that taskloom secret set writes -----------------------------------------------------------------

@pytest.mark.invariant("CR-7")
@pytest.mark.invariant("CR-9")
def test_secret_set_lists_each_new_card_and_key_in_credentials_yaml_as_names_only_in_the_order_made(
        env, credential_store):
    """UAT.password, then Zürich.Größe (names as written, not escaped), then DEFAULT.user on a card written by hand to
    take its values from UAT: the list file is exactly the cards in that order, uses before rows, every key null,
    and the values are in the store only."""
    assert secret_set(env, "UAT.password", "Uat-Pass-32").returncode == 0
    assert list_text(env) == "cards:\n  UAT:\n    rows:\n      password: null\n"

    assert secret_set(env, "Zürich.Größe", "Wert-32").returncode == 0
    both = "cards:\n  UAT:\n    rows:\n      password: null\n  Zürich:\n    rows:\n      Größe: null\n"
    assert list_text(env) == both

    (env.home / "credentials.yaml").write_text(both + "  DEFAULT:\n    uses: UAT\n", encoding="utf-8")
    assert secret_set(env, "DEFAULT.user", "made-up-user-32").returncode == 0
    assert list_text(env) == both + "  DEFAULT:\n    uses: UAT\n    rows:\n      user: null\n"
    assert card_entries(credential_store) == {"UAT.password": "Uat-Pass-32", "Zürich.Größe": "Wert-32",
                                              "DEFAULT.user": "made-up-user-32"}


@pytest.mark.invariant("CR-3")
@pytest.mark.invariant("CR-9")
def test_setting_a_listed_key_leaves_a_hand_written_list_as_it_is_and_setting_a_pointing_row_makes_it_its_own(
        env, credential_store):
    """A list written by hand (a comment, rows on one line): setting the listed uat.PASSWORD changes no character of
    it; setting UAT.alias, which pointed at UAT.password, makes it a row of its own value (null in the list)."""
    hand = "# the made-up test servers\ncards:\n  UAT:\n    rows: {password: null, alias: UAT.password}\n"
    (env.home / "credentials.yaml").write_text(hand, encoding="utf-8")

    assert secret_set(env, "uat.PASSWORD", "Uat-Pass-33").returncode == 0
    assert list_text(env) == hand

    assert secret_set(env, "UAT.alias", "Alias-Value-33").returncode == 0
    assert list_text(env) == "cards:\n  UAT:\n    rows:\n      password: null\n      alias: null\n"
    assert card_entries(credential_store) == {"UAT.password": "Uat-Pass-33", "UAT.alias": "Alias-Value-33"}


@pytest.mark.invariant("CR-17")
@pytest.mark.invariant("CR-15")
def test_an_empty_value_is_stored_and_listed_as_a_value(env, credential_store):
    """`secret set UAT.note --stdin` with only a line break: the value "" is stored, and the list shows no "no value
    stored" on its row."""
    assert secret_set(env, "UAT.note", "").returncode == 0

    shown = taskloom(env, "secret", "list")

    assert card_entries(credential_store) == {"UAT.note": ""}
    assert shown.stdout.splitlines() == ["UAT", "  note"]


# --- where secret set says it saved --------------------------------------------------------------------------------

@pytest.mark.invariant("CR-7")
def test_secret_set_says_it_saved_in_the_system_credential_store_and_writes_no_plain_file(env, credential_store):
    done = secret_set(env, "UAT.password", "Uat-Pass-34")

    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout.splitlines() == ["saved secret 'UAT.password' in the system credential store"]
    assert card_entries(credential_store) == {"UAT.password": "Uat-Pass-34"}
    assert sorted(p.name for p in env.home.iterdir()) == ["credentials.yaml"]


@LINUX
@pytest.mark.invariant("CR-13")
def test_on_linux_without_a_store_secret_set_says_it_saved_in_the_private_file_secrets_yaml(env, credential_store):
    env.env["PYTHON_KEYRING_BACKEND"] = NO_STORE
    done = secret_set(env, "UAT.password", "Uat-Pass-34")

    secrets = env.home.resolve() / "secrets.yaml"
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout.splitlines() == [f"saved secret 'UAT.password' in {secrets}"]
    assert yaml.safe_load(secrets.read_text(encoding="utf-8")) == {"UAT.password": "Uat-Pass-34"}
    assert secrets.stat().st_mode & 0o777 == 0o600
    assert entries(credential_store) == {}


@PC
@pytest.mark.invariant("CR-13")
def test_on_a_pc_without_a_store_secret_set_says_so_and_stores_nothing(env, credential_store):
    env.env["PYTHON_KEYRING_BACKEND"] = NO_STORE
    done = secret_set(env, "UAT.password", "Uat-Pass-34")

    refused(done, "error: The credential store cannot be used: no credential store was found")
    assert list(env.home.iterdir()) == [] and entries(credential_store) == {}


@pytest.mark.invariant("CR-9")
def test_secret_set_makes_a_home_folder_that_does_not_exist_yet_with_the_folders_above_it(env, credential_store):
    """TASKLOOM_HOME names a folder two levels below folders not made yet: set makes them and lists the key there."""
    home = env.root / "not-yet" / "made" / "home"
    env.env["TASKLOOM_HOME"] = str(home)

    done = secret_set(env, "UAT.password", "Uat-Pass-35")

    assert (done.returncode, done.stderr) == (0, "")
    assert (home / "credentials.yaml").read_text(encoding="utf-8") == "cards:\n  UAT:\n    rows:\n      password: null\n"
    assert card_entries(credential_store) == {"UAT.password": "Uat-Pass-35"}


@LINUX
@pytest.mark.invariant("CR-13")
def test_on_linux_without_a_store_secret_set_makes_the_home_folder_and_its_private_file(env, credential_store):
    home = env.root / "not-yet" / "made" / "home"
    env.env.update(TASKLOOM_HOME=str(home), PYTHON_KEYRING_BACKEND=NO_STORE)

    done = secret_set(env, "UAT.password", "Uat-Pass-35")

    assert (done.returncode, done.stderr) == (0, "")
    assert yaml.safe_load((home / "secrets.yaml").read_text(encoding="utf-8")) == {"UAT.password": "Uat-Pass-35"}
    assert (home / "credentials.yaml").read_text(encoding="utf-8") == "cards:\n  UAT:\n    rows:\n      password: null\n"


# --- names ---------------------------------------------------------------------------------------------------------

REFUSED_REFERENCES = {
    "UAT": f"'UAT' is not a credential: {NEEDS}",
    "U AT.password": f"U AT.password: the card 'U AT' holds a space: {RULE}",
    "UAT.pass word": f"UAT.pass word: the key 'pass word' holds a space: {RULE}",
    "UAT.pass.word": f"UAT.pass.word: the key 'pass.word' holds a dot, which separates the card and the key: {RULE}",
    ".password": ".password: the card has no name",
    "UAT.": "UAT.: the key has no name",
    "U$AT.password": f"U$AT.password: the card 'U$AT' holds a character a name cannot have: {RULE}",
    "UAT.pass$word": f"UAT.pass$word: the key 'pass$word' holds a character a name cannot have: {RULE}",
}


@pytest.mark.invariant("CR-14")
@pytest.mark.parametrize("reference", list(REFUSED_REFERENCES))
def test_secret_set_refuses_a_reference_that_is_not_card_dot_key_of_names_saying_exactly_why(env, credential_store,
                                                                                            reference):
    done = secret_set(env, reference, "Some-Value-36")

    refused(done, f"error: {REFUSED_REFERENCES[reference]}")
    assert list(env.home.iterdir()) == [] and entries(credential_store) == {}


@pytest.mark.invariant("CR-14")
def test_secret_delete_refuses_a_card_name_with_a_space_saying_why(env, credential_store):
    write_cards(env, {"UAT": {"rows": {"password": None}}})

    refused(taskloom(env, "secret", "delete", "U AT"), f"error: the card 'U AT' holds a space: {RULE}")
    refused(taskloom(env, "secret", "delete", "U$AT"),
            f"error: the card 'U$AT' holds a character a name cannot have: {RULE}")


DAMAGED_LISTS = {
    "a list of cards": ("cards: [UAT, PROD]\n", "cards: write each card as its name and a colon, on a line of its own"),
    "a card as a list": ("cards:\n  UAT: [password]\n",
                         "card UAT: write its rows under rows:, each key: null, or key: CARD.key"),
    "rows as a list": ("cards:\n  UAT:\n    rows: [password]\n",
                       "card UAT: write its rows under rows:, each key: null, or key: CARD.key"),
    "a card with a space": ("cards:\n  U AT:\n    rows: {password: null}\n", f"the card 'U AT' holds a space: {RULE}"),
    "a card twice": ("cards:\n  UAT:\n    rows: {password: null}\n  uat:\n    rows: {token: null}\n",
                     "the card uat is listed twice (upper and lower case are one name)"),
    "a number as a card": ("cards:\n  5:\n    rows: {password: null}\n", "card 5 is not a name"),
    "uses a card with a space": ("cards:\n  DEFAULT:\n    uses: U AT\n",
                                 f"the card it uses 'U AT' holds a space: {RULE}"),
    "a key with a dot": ("cards:\n  UAT:\n    rows: {pass.word: null}\n",
                         f"the key 'pass.word' holds a dot, which separates the card and the key: {RULE}"),
    "a key with a space": ("cards:\n  UAT:\n    rows: {pass word: null}\n", f"the key 'pass word' holds a space: {RULE}"),
    "an empty key": ("cards:\n  UAT:\n    rows: {'': null}\n", "the key has no name"),
    "a key twice": ("cards:\n  UAT:\n    rows: {password: null, PASSWORD: null}\n",
                    "card UAT: the key PASSWORD is listed twice (upper and lower case are one name)"),
    "a number as a pointer": ("cards:\n  UAT:\n    rows: {password: 5}\n", f"'5' is not a credential: {NEEDS}"),
}


@pytest.mark.invariant("CR-14")
@pytest.mark.invariant("CR-15")
@pytest.mark.parametrize("why", list(DAMAGED_LISTS))
def test_secret_list_names_a_damaged_list_file_and_says_exactly_what_is_wrong_in_it(env, credential_store, why):
    content, said = DAMAGED_LISTS[why]
    (env.home / "credentials.yaml").write_text(content, encoding="utf-8")

    done = taskloom(env, "secret", "list")

    refused(done, f"error: {(env.home / 'credentials.yaml').resolve()} is damaged: {said}")
    assert list_text(env) == content


# --- taskloom secret delete ----------------------------------------------------------------------------------------

USED = {"UAT": {"rows": {"host": None, "password": None, "alias": "UAT.host"}},
        "PROD": {"rows": {"password": None, "token": None}},
        "DEFAULT": {"uses": "UAT", "rows": {"password": "PROD.password", "archive": "UAT.password"}}}
USED_VALUES = {"UAT.host": "uat-db.example.test", "UAT.password": "Uat-Pass-37", "PROD.password": "Prod-Pass-37",
               "PROD.token": "Prod-Token-37"}


@pytest.mark.invariant("CR-3")
@pytest.mark.invariant("CR-9")
@pytest.mark.invariant("CR-12")
def test_secret_delete_refuses_exactly_what_others_use_naming_every_user_and_deletes_the_rest(env, credential_store):
    """A row another row points at (in its own card or another, by the same key name or another), a whole card that
    another takes its values from and whose row is pointed at: each refused naming exactly who uses it, and nothing
    changes; a row nobody points at is deleted, and then the row it pointed at can be too."""
    write_cards(env, USED)
    plant(credential_store, USED_VALUES)
    before = list_text(env), entries(credential_store)
    used = "is not deleted, it is used:"

    refused(taskloom(env, "secret", "delete", "UAT.host"), f"error: UAT.host {used} UAT.alias points at UAT.host")
    refused(taskloom(env, "secret", "delete", "PROD.password"),
            f"error: PROD.password {used} DEFAULT.password points at PROD.password")
    refused(taskloom(env, "secret", "delete", "UAT.password"),
            f"error: UAT.password {used} DEFAULT.archive points at UAT.password")
    refused(taskloom(env, "secret", "delete", "UAT"),
            f"error: UAT {used} DEFAULT takes its values from UAT; DEFAULT.archive points at UAT.password")
    refused(taskloom(env, "secret", "delete", "PROD"), f"error: PROD {used} DEFAULT.password points at PROD.password")
    assert (list_text(env), entries(credential_store)) == before

    for reference in ("PROD.token", "UAT.alias", "UAT.host"):
        done = taskloom(env, "secret", "delete", reference)
        assert (done.returncode, done.stdout.splitlines(), done.stderr) == (
            0, [f"deleted {reference} and its values"], "")
    assert yaml.safe_load(list_text(env)) == {"cards": {
        "UAT": {"rows": {"password": None}}, "PROD": {"rows": {"password": None}},
        "DEFAULT": {"uses": "UAT", "rows": {"password": "PROD.password", "archive": "UAT.password"}}}}
    assert card_entries(credential_store) == {"UAT.password": "Uat-Pass-37", "PROD.password": "Prod-Pass-37"}


@pytest.mark.invariant("CR-12")
@pytest.mark.invariant("CR-13")
def test_deleting_a_row_takes_its_value_out_of_secrets_yaml_on_linux_and_never_touches_that_file_on_a_pc(
        env, credential_store):
    write_cards(env, {"UAT": {"rows": {"password": None, "host": None}}})
    plant(credential_store, {"UAT.password": "Uat-Pass-38", "UAT.host": "uat-db.example.test"})
    secrets = env.home / "secrets.yaml"
    secrets.write_text("UAT.password: File-Pass-38\nOTHER.key: Other-Value-38\n", encoding="utf-8")
    secrets.chmod(0o600)
    before = secrets.read_bytes()

    done = taskloom(env, "secret", "delete", "UAT.password")

    assert (done.returncode, done.stderr) == (0, "")
    assert card_entries(credential_store) == {"UAT.host": "uat-db.example.test"}
    if ON_A_PC:
        assert secrets.read_bytes() == before
    else:
        assert yaml.safe_load(secrets.read_text(encoding="utf-8")) == {"OTHER.key": "Other-Value-38"}


@LINUX
@pytest.mark.invariant("CR-13")
def test_on_linux_a_secrets_yaml_others_can_read_is_refused_saying_chmod_600_and_read_once_it_is_private(
        env, credential_store):
    write_cards(env, {"UAT": {"rows": {"password": None}}})
    secrets = env.home / "secrets.yaml"
    secrets.write_text("UAT.password: File-Pass-39\n", encoding="utf-8")
    secrets.chmod(0o644)

    shown = taskloom(env, "secret", "list")

    refused(shown, f"error: {env.home.resolve() / 'secrets.yaml'} must be readable only by you (chmod 600)", code=2)
    secrets.chmod(0o600)
    assert taskloom(env, "secret", "list").stdout.splitlines() == ["UAT", "  password"]


# --- a run, Validate -----------------------------------------------------------------------------------------------

@pytest.mark.invariant("CR-8")
def test_a_run_names_each_value_not_set_and_where_a_card_it_takes_from_would_have_it(env, credential_store):
    """DEFAULT takes its values from UAT, and UAT.token has no value: a run needing DEFAULT.token and UAT.token stops
    before its first block, saying for each exactly which value to set."""
    write_cards(env, {"UAT": {"rows": {"token": None}}, "DEFAULT": {"uses": "UAT"}})
    path = write_flow(env, {"call": api("http://127.0.0.1:9/never", headers=[
        {"key": "X-A", "value": "secret:DEFAULT.token"}, {"key": "X-B", "value": "secret:UAT.token"}])})

    done = run(env, path)

    refused(done, "error: call: Headers: secret 'DEFAULT.token' is not set: it comes from UAT.token, which is not set "
                  "(run: taskloom secret set UAT.token)",
            "error: call: Headers: secret 'UAT.token' is not set (run: taskloom secret set UAT.token)", code=2)


@pytest.mark.invariant("CR-14")
@pytest.mark.invariant("CR-18")
def test_validate_takes_all_after_secret_and_the_first_colon_as_the_reference_and_names_what_is_wrong(
        env, credential_store):
    write_cards(env, {"UAT": {"rows": {"token": None}}})
    path = write_flow(env, {"look": {"type": "logic.inspect", "config": {"message": "hello {{secret:UAT.to:ken}}"}}})

    done = validate(env, path)

    refused(done, f"error: look: Message: UAT.to:ken: the key 'to:ken' holds a character a name cannot have: {RULE}",
            code=2)


# --- Save as the Credentials window calls it -----------------------------------------------------------------------

SAVED = {"UAT": {"uses": None, "rows": {"password": None, "token": None}}}
NEW_VALUES = {"UAT.password": "New-Pass-40", "UAT.token": "New-Token-40"}


@LOCKABLE
@pytest.mark.invariant("CR-1")
def test_save_whose_list_file_cannot_be_written_puts_every_value_back_in_the_store(env, credential_store):
    """The window's Save of two values with the home folder read-only: the list file cannot be written, so the error
    is raised and the store holds what it held before (UAT.password's old value, no UAT.token)."""
    write_cards(env, {"UAT": {"rows": {"password": None}}})
    plant(credential_store, {"UAT.password": "Old-Pass-40"})
    listed = list_text(env)
    env.home.chmod(0o500)
    try:
        with pytest.raises(OSError):
            credentials.save(env.home, SAVED, NEW_VALUES)
    finally:
        env.home.chmod(0o700)

    assert card_entries(credential_store) == {"UAT.password": "Old-Pass-40"}
    assert list_text(env) == listed


@LINUX
@LOCKABLE
@pytest.mark.invariant("CR-1")
@pytest.mark.invariant("CR-13")
def test_on_linux_without_a_store_save_whose_list_file_cannot_be_written_puts_secrets_yaml_back(
        env, credential_store, monkeypatch):
    monkeypatch.setattr(keyring.core, "_keyring_backend", keyring.backends.fail.Keyring())
    write_cards(env, {"UAT": {"rows": {"password": None}}})
    secrets = env.home / "secrets.yaml"
    secrets.write_text("UAT.password: Old-Pass-41\nOTHER.key: Other-Value-41\n", encoding="utf-8")
    secrets.chmod(0o600)
    env.home.chmod(0o500)
    try:
        with pytest.raises(OSError):
            credentials.save(env.home, SAVED, NEW_VALUES)
    finally:
        env.home.chmod(0o700)

    assert yaml.safe_load(secrets.read_text(encoding="utf-8")) == {"UAT.password": "Old-Pass-41",
                                                                    "OTHER.key": "Other-Value-41"}


class LockedAfterOneWrite(FileStore):
    """The tests' store, locked after its first value is written: the rest of a Save and putting that value back fail."""

    def __init__(self):
        super().__init__()
        self.writes = 0

    def set_password(self, service, username, password):
        self.writes += 1
        if self.writes > 1:
            raise KeyringError("the credential store was locked (made-up)")
        super().set_password(service, username, password)


@pytest.mark.invariant("CR-1")
def test_save_in_a_store_that_locks_midway_says_why_the_store_cannot_be_used_even_when_nothing_can_be_put_back(
        env, credential_store, monkeypatch):
    plant(credential_store, {"UAT.password": "Old-Pass-42"})
    monkeypatch.setattr(keyring.core, "_keyring_backend", LockedAfterOneWrite())

    with pytest.raises(credentials.CredentialError) as raised:
        credentials.save(env.home, SAVED, NEW_VALUES)

    assert str(raised.value) == "The credential store cannot be used: the credential store was locked (made-up)"
