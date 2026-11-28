# Release notes

## 0.42.1

On Windows, the scheduler service no longer tells you that the list of scheduled flows cannot be
read when it reads the list just as the editor saves it: it reads the list again, as it already
did when the list was held open for an instant. 0.42.0 was not published (its last check found
this); what it brings is below, and 0.42.1 has all of it.

## 0.42.0

Credentials are cards of keys. A password, token or key that a flow needs is a row of a card,
named CARD.key (e.g. `UAT.password`): `secret:UAT.password` as a whole setting,
`{{secret:UAT.password}}` inside a text that takes placeholders, `secret("UAT.password")` in
Python Code. `credentials.yaml` in the Taskloom folder lists the cards and keys; the values are
in the system's credential store (Windows Credential Manager, macOS Keychain). Validate names a
card or key that does not exist, with its block and setting, and a run stops before its first
block naming every card, key or value it needs and does not find. A secret's value shows as
`***` everywhere Taskloom shows or keeps what a run did. The guide's section "Credentials: cards
and their keys" tells it all.

### Secrets stored before this version

They are not read any more. Enter each again as a card's row, with
`taskloom secret set WAREHOUSE.password` (it asks for the value) or in the editor's
Tools → Credentials window, and write the new CARD.key where your flows and `connections.yaml`
named the old secret. Then remove the old entries, which nothing reads now:

- On Windows: open Credential Manager (Control Panel → Credential Manager → Windows
  Credentials) and, under Generic Credentials, remove the entries named `taskloom` and
  `NAME@taskloom` (NAME is an old secret's name, e.g. `warehouse_password@taskloom`).
- On macOS: open Keychain Access and delete the items named `taskloom` (their Account is an
  old secret's name), or in Terminal run `security delete-generic-password -s taskloom` until
  it finds none.
- On Linux: delete the old secrets' lines (names without a card) from `secrets.yaml` in the
  Taskloom folder.
