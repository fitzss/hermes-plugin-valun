# Changelog

## 0.4.0 "circulating credit" (unreleased draft)

**Not released.** Built and tested locally only: this version is not pushed, the Hermes plugin catalog still pins
0.3.0 (`5d01fbd`), and the server release it is built for (Valun release v16, circulating credit) is not deployed.
With circulation off on the server, the plugin makes the requests 0.3.0 made, plus a few reads of `GET /info`
(valun-open `spec/PROTOCOL.md` Appendix H).

### Core

- `valun_core/` is the `valun` package 0.4.0, copied unchanged: eight modules, the new `novation.py` (the signing
  firewall's pure evaluators) and `records.py` included (`scripts/check_plugin_core.py` in valun-open checks it).
- Circulating credit through the core: bound credit notes, paying with a note you hold
  (`valun_review_delivery` `pay_with`), consents to set-offs and transfers, one live payment attempt per deal, and the
  new fields of `valun_status`, `valun_deal` and `valun_inbox`. Still nine tools.

### Approvals

- The approval hook asks the core whether a call is a commitment (fail closed by name: an unknown `valun_*` tool asks)
  and shows the core's prepared text, built from the same snapshot as the arguments that are signed; when that text
  cannot be prepared, the call is blocked and nothing is signed.
- A commitment that reaches its handler without the hook's binding is refused (`approval_missing`).

### Consents

- New setting `process_consents`, **off** by default; `hermes valun join --sandbox` turns it on with all three roles.
  An invited join leaves it off, with set-offs and transfers of your own debt in the policy and payments to you by
  transfer off.
- Roles `consents.setoff`, `consents.as_issuer`, `consents.as_new_creditor` and the cap `consents.max_PC` (50 PC by
  default, `none` for no cap; a value that is not a whole number means 0).
- While `process_consents` is on, consents allowed by the policy are signed before a Valun tool call that is not
  read-only, at most once a minute; and, because Hermes runs no consent loop, the value-safe ones (set-offs, and
  payments to you as your policy allows) also before `valun_status` and `valun_inbox` (decision D-3 a), whose results
  then list `consents_signed_now`. Transfers of your own debt and anything that pays from your side are never signed
  from those two read-only tools. Nothing runs in the background.
- Turning `process_consents` off, or every role off, withdraws the declared roles on the next call (the empty
  `X-Valun-Features` roles list, decision D-3 b); with `process_consents` off, `valun_status` and `hermes valun doctor`
  say that no consent role is declared.
- New command `hermes valun consents [--process]`.

### Storage and join

- Records live in `plugin-data/valun/valun-records.json` (started from 0.3's `issued-notes.json`), changed under a lock
  file; when the file exists but cannot be read, nothing is signed.
- `hermes valun join` hands the owner token to the owner before any local write that can fail (decision D-3 c).
- Every command the plugin prints names its Hermes profile (`hermes -p <profile> ...`), the default profile and a
  profile really named `custom` included; only in a Hermes home that is no named profile (or when Hermes cannot say
  which profile it is) are the commands the plain `hermes valun ...`.
- `hermes valun join` and `enroll` still store the signing key and agent token with `hermes config set`, but each value
  now reaches that child Python through its environment, never its command line, which other users of the machine may
  read (in 0.3.0 the key and token were arguments, visible in `/proc/<pid>/cmdline` on Linux while the child ran).

### Checked locally (2026-11-09; unit tests, vectors and validate re-run 2026-11-10)

- Unit tests (`python -m unittest discover -s tests`): 145 tests, OK with 2 skipped on Python 3.14.7 (the tests that
  need Hermes), OK with none skipped in the Hermes venv.
- valun-open `spec/vectors/check_python.py --client plugin --require circulation/1`: 1386 checks, 1188 agree,
  0 disagreements in failing tiers, 17 informative differences, 181 not applicable; `scripts/check_plugin_core.py`:
  unchanged (8 files).
- `hermes plugins validate --install-deps` on an empty Hermes home: validation passed (at `2b78a06`). After the audit
  fixes, `hermes plugins validate` without `--install-deps` (no network) on a fresh Hermes home: validation passed,
  security scan safe.

## 0.3.0

The version the Hermes plugin catalog pins (`5d01fbd`).
