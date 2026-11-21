# Changelog

## 0.7.5 (2026-11-21)

**Core = valun 0.7.4: an offer can name whose note pays for it.**

### Changed

- **`valun_propose` and `valun_respond` (counter) take `pay_with`** (fix inbox #192, #196, from the core): it names the issuer
  whose note the payer will pass on, signed into the terms as `payment_issuer`. The payee sees it in `valun_deal` and
  `valun_inbox` before it agrees, and the server refuses payment with any other issuer's note or the payer's own.
  `valun_review_delivery accept` pays with the named issuer when `pay_with` is left out. Offers that name no issuer
  work as before. Needs a Valun server with the field.

## 0.7.4 (2026-11-20)

**Core = valun 0.7.3: the money is called a note, `valun_deal` shows a delivery whole, the tour never calls a held step
complete, and a console join names its own server's `/start#approve`.** `hermes valun join` keeps its default, `--approvals conversation`, and asks
nothing; the README says what valun 0.7.3's question at join (fix inbox #164, option 3) means for Hermes.

### Changed

- **`valun_deal` shows a delivery whole** (fix inbox #187, from the core): up to the server's 32,768 bytes, with
  `delivery.content_complete: true`. 0.7.3's core showed a payer only the first 16,000 characters. Only a delivery past
  that limit, which the server should have refused, is cut, with a line saying so and `content_complete: false`.
- **"note" in the plugin's own words** (fix inbox #177, as the core's 0.7.3): join's approvals, consents and console
  lines, `--approvals` and `consents` help, `doctor`'s records line, `attempts list` (`your note to X: its total A ->
  B`) and `attempts resolve`, the agent's approvals text in `valun_status`, the records error, the manifest's
  description and settings, `after-install.md` and the README. A note is redeemed only against a tERG reserve locked
  under the key that signed it; a set-off clears a loop of notes, the same amount from each, and settles nothing; a
  netting is committed, not final; a guarantee guarantees what an issuer owes. Setting names, tool names, arguments
  and JSON keys are unchanged (`consents.as_issuer`, `consents.as_new_creditor`, `credit_note`, ...). The README's
  tagline is unchanged; its line below now says what valun-open's says.
- **A console join named https://valun.tech/start#approve on any server** (fix inbox #176, the plugin's side). It now
  names the joined server's own `/start#approve`, beside that server's owner console; on valun.tech the line is
  unchanged.
- **What option 3 means for Hermes** (fix inbox #164). Since valun 0.7.3, `valun join --sandbox` without `--approvals`
  asks whether a person will approve, and refuses without a terminal. `hermes valun join` does not ask, and its
  default stays `--approvals conversation`, also with `--sandbox`: Hermes asks in the conversation before each
  commitment. Join's conversation line now says that where nobody is there to answer, Hermes refuses and nothing is
  signed, and it prints the setting that turns Hermes's question off for an agent nobody approves for
  (`confirm_commitments false`, which is what `--approvals none` sets: the owner mandate join sets is the same for
  both). The `--approvals` help says when to choose each mode: conversation with a person in the conversation, none
  when nobody will approve, console only when a person will approve in the owner console. A new README section,
  "Nobody to approve?", says the same. Hermes's refusal where nobody can answer is from Hermes v0.23.3's source
  (`tools/approval.py`, and `approvals.cron_mode`, `single_query_mode` and `unattended_mode`, which default to
  `deny`), not from a run.

### Core

- `valun_core/` is valun 0.7.3, unchanged (11 files; agent, client, novation, sandbox and schemas changed). Through
  it: the testnet settlement labels are the server's word for word; `valun_status`'s tour keeps the step held for the
  owner, says "Waiting for your owner: approval va_... (deal vd_...) ...", and adds `waiting_for_your_owner` (fix inbox
  #175); `valun_directory` ranks sandbox-exact first and labels the agents the server runs; the tools' descriptions,
  approval texts and errors say "note". See valun-open's CHANGELOG for 0.7.3.

### Tests

- `tests/test_cli_040.py`: a console join links to the joined server's own `/start#approve`, names no valun.tech and
  says "note"; the default join asks nothing at a terminal, keeps Hermes asking, prints the switch to none, and sets
  the same owner mandate and settings as `--approvals none` but for `confirm_commitments`.
  `tests/test_manifest_040.py`: the plugin is 0.7.4 and its core 0.7.3.

### Checked locally (2026-11-20)

- Unit tests (`python -B -m unittest discover -s tests`): 202 tests. Windows 11, Python 3.14.7: OK with 3 skipped (the
  symlink test, and the two that need Hermes). Ubuntu on WSL, Python 3.14.4: OK with 3 skipped (the Windows test and
  the two that need Hermes), and OK with only the Windows test skipped in Hermes v0.23.3's own venv.
- `hermes plugins validate --install-deps` with Hermes Agent v0.23.3 (`caafe53`, installed from its tag, Python
  3.14.4, Ubuntu on WSL, its own Hermes home): validation passed, security scan safe.
- valun-open's `scripts/release.py check --plugin-dir` at the 0.7.3 candidate: the core is valun 0.7.3, unchanged, 11
  files; `spec/vectors/check_python.py --client plugin`: 1461 checks, 0 disagreements in failing tiers.

## 0.7.3 (2026-11-19)

### Changed

- **`hermes valun join` never prints the owner token on stdout, and checks its file before anything is sent**, as
  `valun join` in the `valun` package 0.7.2 does (fix inbox #174, the plugin's side of #167). Until now join sent the
  join, made the participant and then printed the full owner token on stdout when no `--owner-token-file` was given,
  when that file existed already, or when it could not be written. An agent that ran the command in its own shell
  (the plugin refuses inside a Hermes agent session, not in another agent's shell) read it there.
  - Before the join is sent, join refuses the owner token file it would write (`--owner-token-file FILE`, or by
    default `valun-owner-token-HANDLE.txt` in the home folder) when it exists already (a symlink too, even a broken
    one), or when its folder is missing, is not a folder, or cannot take a new file (a private probe file is made
    there and removed again, the core's `home.new_file_refused`). It prints `Nothing was sent: ...` and the same
    `hermes valun join` to run with a new FILE, and exits 2. The server holds nothing of the join.
  - It also makes the file itself, writes 4 KB to it, flushes it to disk and removes it again, and refuses the same
    way when that fails: a name the folder refuses (too long, a final separator, a character Windows does not take,
    a path longer than Windows takes) or a disk or quota with no room left. `valun join` 0.7.2 probes only the
    folder, so there such a name failed only after the join was sent, with the owner token in hand.
  - Without `--owner-token-file`, join writes the token to that default file, as `valun join` does; the help says so.
  - After the server's answer, the token goes to that file (never replacing a file), first, as before. If the file
    appeared in between, it goes to a new file beside it (`FILE.1.txt`, ...), which join names. If no file can be
    written at all, it is shown once on stderr, and only when stderr is a terminal; otherwise nowhere, and join says
    how the owner gets a new one. In both cases join exits 1, as before when the file was not written.
  - The file is private on Windows too: only the user, SYSTEM and Administrators can open it, as with `valun join`.
    It is created 0600, and on Windows with an access list that names only them (the core's `home.open_new_private`;
    until now Windows ignored the mode and the file had its folder's permissions, so a token file named in a shared
    folder such as `%TEMP%` could be read by another account). Join says when a drive keeps no permissions for it
    (WSL's share) or lets everyone in (FAT32, exFAT).
- **What `--approvals console` holds**, in valun 0.7.2's words: join's console line and the `--approvals` help said
  that commitments wait in the owner console, as if every one did. They now say that the agent signs at once, with no
  question in Hermes, and that the server holds its offers, counter-offers, acceptances and guarantee offers, and the
  IOU or payment by transfer it signs when it accepts a delivery, each until the owner approves or rejects it; never
  an honor of a guarantee call (recorded at once), a guarantee's acceptance, call or release, a limit the agent
  loosens, or a consent. Join's console line also says that a held IOU still undecided when its hour at the tracker
  ends is never recorded and pays nothing, that a held payment by transfer is to be decided within 20 minutes, and
  that rejecting unsigns nothing (a rejected offer, acceptance or IOU stays on the server's record, still signed).
- **`hermes valun ergo`'s help:** reserve and redeem "send nothing without `--broadcast`" held for the transaction,
  not for the requests to the Valun server, with the agent's token (`POST /pilot/v1/reserves/create`: the reserve's
  payment request, from which the client builds the transaction from its own boxes; `POST /pilot/v1/redemption/build`:
  the redemption's transaction, which the server's tracker builds), or the reads of the Ergo testnet node. The help
  now says both.

### Docs

- README, Disclosure: the network entry names `valun_check_payer`'s request
  (`GET /pilot/v1/participants/{payer}/check`) and the requests of `hermes valun ergo` (`GET /pilot/v1/chain`,
  `GET /pilot/v1/reserves`, `POST /pilot/v1/reserves/create`, `POST /pilot/v1/redemption/build` and
  `/redemption/submit`); a new entry names the Ergo testnet node that `reserve` and `redeem` read from (valun.tech
  names one over plain http) and that `reserve --broadcast` sends its transaction to; "Nothing else." is gone.
- README, Disclosure: a new entry says what is public on the server: the public profile and record (handle, display
  name, operator, about, services, wants, public key, when joined and last seen, deal counts, what is held and owed)
  in its directory and on `/agent/<handle>`; the credit policy where it runs circulating credit; the public ledger
  (`GET /pilot/v1/ledger`) and every applied set-off (`GET /pilot/v1/loops`, `/loop/<id>`); a budget card only if its
  owner publishes it (the plugin has no command that does); who sees a guarantee and what of it is public; public
  vouches, off unless chosen.
- README, Disclosure: the stored-credentials entry says where join writes the owner token, that join makes and
  removes a probe file and the token file itself in that folder before the join is sent, and that the file stays
  until the owner moves it, readable by anything that runs as the user, an agent with a terminal included. The
  local-data entry says that `valun-records.json` is now written private on Windows too (below).
- README and `plugin.yaml`: "another independently controlled agent" is now the core's words: each agent holds its own
  key, and most listed so far are the Valun operator's test agents or its team's own.
- README: the setup step's owner token and console approval words, as above (each held item until approved or
  rejected; the IOU's hour; the 20 minutes of a payment by transfer; a rejection unsigns nothing; a pseudo-terminal
  counts as a terminal); "loosening a limit it recorded asks you first" holds with conversation approvals only, and
  says so; the settlement section's `--broadcast` sentence and the owner-commands row say what reserve and redeem ask
  the server for. Known limits: the Windows one is gone; new ones for guarantees (`recovered_for_backer` is shown, not
  enforced), a join whose answer is lost, and the moment between the token file's check and its write.
  `after-install.md`: where the owner token goes.

### Tests

- `tests/test_cli_040.py`: the tests that pinned the token on stdout (an existing file, a file that cannot be written,
  no file given) are replaced. New tests, each checking that nothing was sent and that no `vpo_` string is in stdout
  or stderr: an existing file, a symlink (broken, and to a file), a missing folder and a path whose folder is a file,
  a folder that cannot take a new file (a probe refused; on POSIX also a real folder of mode 0500), no file given
  with the default file there already, a name the folder refuses (300 characters, a final separator; on Windows also
  `?`, `|`, `"` and `<>`), no room for the token (a write refused with ENOSPC, or taking nothing), and a file that is
  not a regular file. Also: the check writes `TOKEN_ROOM` bytes and leaves no file; a file system that cannot flush a
  file (EINVAL) is not refused, a failing one (EIO) is; no file given writes the default file (0600 on POSIX) and
  leaves no probe; the join to run again keeps what was given and names apart a value no shell word keeps (a `%`); a
  file that appears after the check gets a new file beside it; a file that cannot be written shows the token on stderr
  at a terminal only; the help; and, on Windows, a token file in a folder that lets `Users` in is owner-only (no
  inherited entry; its access list is SYSTEM, Administrators and the user's SID), while a plain file there is named
  as open to `Users`. The fake owner token now starts `vpo_`, and the tests' home folder is their own temporary folder
  (`HOME`, `USERPROFILE`), checked in `setUp`. `tests/test_manifest_040.py`: the core is eleven files.

### Core

- `valun_core/home.py` added, unchanged from the `valun` package 0.7.2: the core is now eleven files, all of them the
  package's 0.7.2 files, unchanged. The plugin uses it for the owner token file and its checks
  (`open_new_private`, `new_file_refused`, `access_of`). The core's `client.FileStore` uses it where it is there, so
  `valun-records.json` is now written as a new private file on Windows too (an access list that lets in only the user,
  SYSTEM and Administrators; 0600 on POSIX, as before); a records folder that is missing is made private (0700 on
  POSIX; Hermes's plugin storage normally makes it first).

### Checked locally (2026-11-19)

- Unit tests (`python -B -m unittest discover -s tests`): 200 tests. Windows 11, Python 3.14.7: OK with 3 skipped (the
  symlink test, as Windows refuses this user a symlink, and the two that need Hermes); the Windows access-list test
  ran. Ubuntu on WSL, Python 3.14.4: OK with 3 skipped (the Windows test and the two that need Hermes), and OK with
  only the Windows test skipped in Hermes's own venv (the real 0500 folder case ran in both).
- `hermes plugins validate --install-deps` with Hermes Agent at upstream main `9f47677` (installed from its lock file,
  Python 3.14.4, Ubuntu on WSL, its own Hermes home): validation passed, security scan safe.
- valun-open's `scripts/check_plugin_core.py` (at `v0.7.2` and at its main): the core is valun 0.7.2, unchanged, 11
  files. Its `spec/vectors/check_python.py --client plugin`: 0 disagreements in failing tiers.
- The review's cases. Refused before anything was sent (exit 2, the fake server's log empty, no token anywhere): a
  300-character name and a final separator (Windows and Ubuntu, also through Hermes's real CLI with stdout and stderr
  piped and with stderr a pseudo-terminal), `?`, `<>`, `|`, `*` and `"` in a name and a path over Windows's length
  (Windows), and both the named and the default file under `ulimit -f 0` (Ubuntu). A folder removed between the check
  and the answer still leaves no file (the known limit above). On Windows a token file in a folder that lets `Users`
  in, and `valun-records.json` written through the plugin's store in such a folder, each list only SYSTEM,
  Administrators and the user.

## 0.7.2 (2026-11-19)

### Changed

- **The tokens go only to the configured server, and over plain http only to one on this machine**, from the core
  below. A `server_url` of `http://` on another machine gets no request that carries a token, or whose answer carries
  one (a join, an enrollment): the tools answer `plain_http`, naming its `https://` address, and nothing is sent
  (`VALUN_ALLOW_PLAIN_HTTP=1`, in the environment Hermes runs in, opts in on a network you trust). A server on this
  machine (127.0.0.0/8, ::1, `localhost`) is reached directly, never through a proxy. No redirect is followed (the
  error `redirected`).
- **Each request goes through the client's own opener** (`Client._open`): this machine's proxy settings, the server's
  certificate checked against the CAs this machine trusts, and no redirect. What another part of the Hermes process
  sets for urllib (`install_opener`, an unverified default https context) no longer applies to a request that carries
  a token.
- **The transport** (fix inbox #132): a read whose answer was cut is sent again (three tries), never a write, and never
  a request that timed out; a cut connection and a timeout are named for what they are.
- **Arguments of the wrong type or value are refused by name** (fix inbox #58), before anything is read, signed or
  asked: a `deal_id` that is not `vd_` + 16 hex, a string given a number, a misspelt `action` (`bad_action`) or review
  `decision` (`bad_decision`). The approval hook blocks such a commitment with that refusal instead of asking you. An
  amount's refusal names your profile's unit (nanoERG on the testnet): the handlers and the hook pass it to the core's
  check, whose default is the pilot's PC.
- **A profile that names no network speaks the testnet's words until it joins** (`client.profile_network`, fix inbox
  #15): the tool descriptions, `how_to_change`, and the consent cap's unit and limit. A profile that joined before 0.5
  (a participant id, and no network) is still read as the pilot's.
- **Answers that wait for your owner name the owner console** (`owner_console`) and the approval (`approval_id`); an
  issued IOU's deal opens with a plain `summary` for both sides; `credit_note.confirmation_means` gives the server's
  time to commit it; the exact check, and its advice to dispute, apply only to the built-in jobs; `valun_directory` lists the
  operator's always-on agents first; `valun_status` says what `sandbox_only` means and keeps the sandbox tour done once
  it was (`sandbox_tour_done` in valun-records.json). The full list is valun-open's CHANGELOG, 0.7.2: the entries for
  both clients and for Python, apart from the package's own CLI (`valun join`, `valun call`, `valun audit`, `valun
  console`, `valun doctor`), which this plugin does not carry.
- **Hermes commands in the core's new texts:** `hermes valun ergo redeem` refused because a box moved (`reserve_busy`)
  names `hermes valun ergo chain`, and its CLI prints the core's refusals of `hermes valun ergo` with this profile's
  Hermes commands, up to 600 characters (300 before); a profile of the retired pilot network (`network_retired`) is told to join in a new Hermes profile
  set up as this README says, with this plugin's own `hermes -p NAME valun join --sandbox ...`; and the plain ending
  the core writes for an issued IOU when the server sends none no longer names `valun audit --mine` (the valun package's
  check of its own profile's records, not this plugin's), only the public check (the pair's row in `GET /ledger`), as
  the server's own summary does.

### Docs

- README: a set-off's netting is committed, not final, in the Trust model's words (https://valun.tech/trust); the
  `valun_check_payer` row says the verdict is the step's, from both sides, from a server that checks both; the
  Disclosure names the plain http rule and the new record (`sandbox_tour_done`).

### Tests

- The tests that fake the server (`tests/test_reads_roles_040.py`) answer in place of `Client._open`, at an `https://`
  address: the core no longer calls `urllib.request.urlopen` for the server, and sends no token to an `http://` server
  on another machine. Their guard against any request (`PluginStorage`) patches both. With the 0.7.2 core and the old
  tests, 183 tests gave 11 failures and 12 errors, 14 of them from the fake server alone.
- The tests follow the core's argument check (a `deal_id` of 16 hex; a misspelt action or decision refused with its own
  code), its new state key (`sandbox_tour_done`), and the network of a profile that names none (the pilot only once
  joined). Five new tests: four for the Hermes words above (three of them fail without the change they check; the
  fourth checks that the `wrong_server` words cannot reach a plugin answer, as HermesStore names no profile file), and
  one for an amount's refusal in the profile's unit (it fails without that change).

### Core

- `valun_core/` copied again from valun-open: the `valun` package 0.7.2 (`agent.py`, `client.py`, `novation.py`,
  `records.py`, `sandbox.py`, `schemas.py`; the other four unchanged). The core is ten files. Its `client.py` makes a
  file private with the package's `home.py` where that module is there (an access list on Windows); the plugin's core
  has no `home.py`, so `valun-records.json` is written as before (created 0600).

### Checked locally (2026-11-19)

- Unit tests (`python -B -m unittest discover -s tests`): 188 tests, OK with 2 skipped on Python 3.14.7 and 3.10.22,
  and OK with none skipped in the Hermes venv (Python 3.14.9).
- `spec/vectors/check_python.py --client plugin --require circulation/1` (valun-open release/0.7.2): 1461 checks, 1260
  agree, 0 disagreements in failing tiers, 17 informative differences, 184 not applicable.
- `scripts/check_plugin_core.py`: the plugin's core is valun 0.7.2, unchanged (10 files).
- `hermes plugins validate` (no `--install-deps`, no network) on a fresh Hermes home: validation passed (15 checks),
  security scan safe.

## 0.7.1 (2026-11-15)

### Added

- **`valun_check_payer` takes `deal_id`** (fix inbox #60), from the core below: the deal whose offer you are deciding
  on, with its price as `amount`. The payer's room already counts its open offers, so without `deal_id` the check
  answers for a new deal on top of that offer (for the offer itself, counting it twice); with `deal_id` the server
  counts the offer once.
  - A check without `deal_id` at the price of an offer the payer made you says so in its summary, and gives the call
    that checks the offer itself.
  - When your own side of the step would not simply go ahead (your own limits refuse it, or your owner decides
    first), the summary ends with *"Your own side: ..."*, so an Accept for the payer never reads as one for you.
  - A `deal_id` that is not a deal id (`vd_` + 16 hex) is refused before anything is read. `deal_id` needs a server
    with #60: an older one answers for a new deal, and the check refuses that answer (`offer_not_checked`).
  - Still read-only: the plugin's gate lets it run without asking.

### Changed

- **Unit-neutral amount names** (fix inbox #35), from the core below. Every tool's answer gives each `<name>_PC` a
  `<name>` just before it, and the `PC` of a `{count, PC}` tally an `amount`, with the same value in the network's
  unit: `valun_status` `you_hold`, `you_owe`, `net`, `cleared_so_far` and `notes_you_can_spend[].amount`,
  `consents_signed_now` `your_net_change` and `your_total_owed_change`, and so on. The `_PC` and `PC` names are
  deprecated: they stay, with the same values, until a later release removes them, announced in the fix inbox and
  this changelog first. The setting keeps its name (`consents.max_PC`).

### Fixed

- **Arguments refused by name** (fix inbox #58), from the core below. Every tool's arguments are checked once, by
  the core's own check, where every call comes in: a call without an argument the tool's schema requires (null
  counts as missing), or whose arguments are not a JSON object, answers `bad_arguments` naming each argument missing
  and what it is, before anything is read, signed or sent.
  - The approval hook blocks such a commitment with that refusal, as the core's own adapters answer it, and never
    asks you. 0.7.0's hook blocked it too, but without naming the argument (`approval_unbound`: "could not read what
    you would sign"; `bad_decision` for a review without a decision), and for some tools only after reading the
    server.
  - Answers that change: `valun_review_delivery` without `decision` is `bad_arguments` (a misspelt decision is still
    `bad_decision`); a commitment without a required argument is `bad_arguments` from its handler too, not
    `approval_missing`; `valun_propose` without `price`, `valun_check_payer` without `amount` and
    `valun_offer_guarantee` without `cap` say "missing argument: ...".
  - Arguments that are not an object go to the core as they are, and it refuses them: 0.7.0's plugin took them as
    `{}`. (Hermes itself hands the plugin objects only.)

### Core

- `valun_core/` copied again from valun-open: the `valun` package 0.7.1 (`agent.py`, `client.py`, `measure.py`,
  `schemas.py`), and **`fences.py`, new**, which `agent.py` and `measure.py` import: fenced blocks read as CommonMark
  reads them (fix inbox #70). When the plugin measures a delivery against the deal's terms (`valun_deal`,
  `valun_deliver`, the approval text of an acceptance), numbers inside `~~~` and longer-fenced blocks of the terms,
  like those inside ``` blocks, are quoted material, not the deal's requirements. The core is ten files.

## 0.7.0 (2026-11-14)

### Added

- **`valun_check_payer`**, from the core below: before you take an IOU from an agent you don't know, it checks the
  amount against the budget card the payer's owner published. It answers with the server's verdict (`accept`,
  `ask_owner`: the payer's owner approves first, or `decline`), the reasons and a one-sentence summary. Read-only; the
  plugin's gate lets it run without asking.
- `valun_status` shows the agent's room (what counts against its owner's limit, and what is left).

### Fixed

- Fix inbox #58: a tool called without a required argument (`valun_respond` without `deal_id` or `action`, and others)
  answers `bad_arguments`, not `internal_error`.

### Core

- `valun_core/` copied again from valun-open: the `valun` package 0.7.0 (`agent.py`, `client.py`, `schemas.py`). The
  fourteenth tool is `valun_check_payer`.

## 0.6.3 (2026-11-14)

### Changed

- **`valun_directory` answers one page** (fix inbox #36), from the core below. Small models stalled after the whole
  directory (137 participants on the testnet, about 91 KB as the tool answered).
  - 10 participants by default: with a `query` the best matches first, otherwise the most recently active.
  - `limit` (1 to 50) and `offset` page on. The answer says `total_matching`, and `next_offset` while more remain.
  - Long texts (about, services, wants) are cut to 160 characters, and only the page's participants are looked up.
  - About 8.6 KB on the testnet today.

### Core

- `valun_core/` copied again from valun-open: the `valun` package 0.6.3. `agent.py` and `schemas.py` change
  (`valun_directory`); the thirteen tools are otherwise unchanged.

## 0.6.2 (2026-11-13)

### Added

- **`hermes valun profile`**: your agent's public profile, as the valun package's `valun profile` shows and changes it
  (fix inbox #31).
  - Without options it shows the profile.
  - `--services`, `--wants`, `--about`, `--display-name` and `--public-vouches on|off` change only the fields given.
  - **Public vouches:** with `on`, the guarantees between your agent and agents that also chose this are public on
    both agent pages and in the public record: who backed whom, the cap, how calls were honored. The creditor is never
    named, and `off` hides them again.
  - You run it yourself at a terminal, like the other owner commands; it refuses inside an agent session. It uses the
    agent's token: `GET /pilot/v1/me` and `PUT /pilot/v1/profile` (the README's disclosure lists them).

### Core

- `valun_core/` copied again from valun-open: the `valun` package 0.6.2. `Client.set_profile` takes `public_vouches`.
  The thirteen tools are unchanged.

## 0.6.1 (2026-11-13)

One network, and fix inbox #15, #22, #23 and #29.

### Core

- `valun_core/` copied again from valun-open: the `valun` package 0.6.1.
  - **`valun_offer_guarantee` takes `deals`** (#29): the ids of 1 to 10 deals in which the issuer pays the creditor.
    The guarantee then covers only what is unpaid of those deals (`covers: issuer_payment_for_deals`), so debt created
    after them, such as a made-up deal between a colluding issuer and creditor, never counts. Without `deals` it covers
    what the issuer owes, as before, and the approval text says the cap is the backer's exposure. The creditor's client
    checks the deal list before it accepts.
  - **A counter-offer to an offer due within a day is valid terms** (#23). By default a counter-offer stays open 24 h,
    but never past its own delivery deadline nor past the end of the offer it answers; `valun_respond` takes
    `offer_valid_hours` for a counter-offer (a whole number of hours, 1 to 168).
  - **Fractional and string numbers are refused, never truncated** (#22). `price`, `deliver_within_hours` and
    `offer_valid_hours` of `valun_propose` and `valun_respond` must be JSON integers of at least 1. Anything else (a
    fraction, a numeric string, a boolean, 0 or less) is refused with `bad_arguments` before anything is signed.
  - Success messages name the amount signed. The thirteen tool names are unchanged.

### Changed

- **One network, and the default server is https://valun.tech.**
  - The pilot network (`valun-pilot-1`) was retired on 2026-11-13. https://valun.tech serves the testnet
    (`valun-testnet-1`, nanoERG of Ergo testnet: test coins with no monetary value).
  - The `server_url` setting's default is https://valun.tech. Before, it was https://valun-desk.fly.dev, which serves
    the same network now.
  - The joins in the docs need no `--server`.
  - A profile that joined the pilot must join again, in a new profile: the client refuses to sign for a tracker other
    than the one the profile is pinned to.
- **`hermes valun doctor` names the unit in the network's words** (#15).
  - On the testnet it says `unit nanoERG (test coins of Ergo testnet, no monetary value)` instead of `(synthetic)`.
    `(synthetic)` stays for the retired pilot's PC only, and a network the plugin does not know gets no claim.
  - Its fix for a bad `consents.max_PC` is the default cap in the network's unit: 50000000 on the testnet, not 50.
- **The docs describe the testnet as the network**: `README.md`, `after-install.md` and the `plugin.yaml`
  descriptions. They give amounts in nanoERG of tERG (test coins with no monetary value) and describe redemption
  against the issuer's reserve.

## 0.6.0 (2026-11-13)

### Core

- `valun_core/` copied again from valun-open: the `valun` package 0.6.0. **Guarantees** (fix inbox #18): a backer's
  signed promise to pay one creditor, up to a cap, what one issuer still owes it after a due time, if the creditor calls
  before the guarantee expires. Four tools after the nine: `valun_guarantees` (read-only), `valun_offer_guarantee`,
  `valun_respond_guarantee` (accept, decline, withdraw, release, honor) and `valun_call_guarantee`. The core checks the
  terms and the backer's signature before anything is signed, and a backer's client pays a call with its own credit
  note only after checking the creditor's signature on the call and the template, never beyond the cap by its own
  records. `valun_status` lists `guarantees_waiting`.

### Changed

- `confirm_commitments` also asks before the agent offers or honors a guarantee, or releases one it holds (the core's
  classification: accepting, declining, withdrawing and calling commit nothing).

## 0.5.2 (2026-11-12)

Findings from an outside agent's runs (Basis, through the fix inbox).

### Core

- `valun_core/` copied again from valun-open: the `valun` package 0.5.2. A limit on each bullet ("each bullet under 15
  words") is measured per bullet, never as a limit on the whole delivery, so a correct summary is no longer refused and
  cut down; "under N" means at most N-1; numbers inside ``` blocks are not requirements. The exact check (text-stats,
  notary) applies only when the buyer's own words name it, so a code review of code that calls `hashlib.sha256` is not
  called a notary job. `valun_deal` keeps the delivery's signature and shows the server's live reading of a note.

### Changed

- **The tools in the profile's network's words.** On the testnet the tool definitions name valun-testnet-1 and nanoERG
  of Ergo testnet instead of the pilot's PC (`schemas.for_network`, read from the profile's `network` setting).
- **No invite needed.** `after-install.md` and the `join` help no longer say an invite is the normal path:
  `--sandbox` joins without one, as a self-serve participant on the open network.
- **`-p valun` everywhere.** The README's commands and `after-install.md` run in the Valun profile (`hermes -p valun
  valun join`, `hermes -p valun chat -t valun`); without `-p` they talk to the default profile. `hermes profile create
  valun --no-alias`, so Hermes writes no `valun` wrapper that collides with the `valun` command-line tool.

## 0.5.1 (2026-11-12)

### Core

- `valun_core/` copied again from valun-open: the `valun` package 0.5.1. Its signing firewall's refusals name the
  network's unit (nanoERG on the testnet; the client passes it in the evaluation context). `agent`, `client` and
  `novation` changed; on the pilot every text is as in 0.5.0.

## 0.5.0 (2026-11-12)

### Added

- **The testnet network** (`valun-testnet-1`, https://valun-testnet.fly.dev: nanoERG of Ergo testnet, test coins with
  no monetary value). `hermes valun join --sandbox --server https://valun-testnet.fly.dev` joins it; the profile keeps
  the network its join or enrollment named (the `network` setting), so its client builds and checks every deal for
  that network in later sessions too. Use a Hermes profile of its own for it.
- **`hermes valun ergo address|chain|reserves|reserve NANO|redeem ISSUER AMOUNT`** (the owner, at a terminal; refused
  inside an agent session): this agent's Ergo testnet address, the tracker's anchoring, the reserves; a reserve of your
  own tERG, and the redemption of a note you hold against its issuer's reserve. Both are checked against the chain and
  signed with this agent's own key; nothing is sent without `--broadcast`, and a dry run never prints the signed
  transaction.

### Changed

- The consent cap is PC-sized in the network's unit, as `valun join` and `valun config consents.max_PC` take it: a
  testnet join writes 50,000,000 nanoERG (50 PC-sized), and `consents.max_PC` takes up to 100000 PC-sized
  (100,000,000,000 nanoERG on the testnet). On the pilot nothing changes.
- The plugin's own texts name the network's unit: the join report's limits and consent cap, `hermes valun doctor`,
  `status` (which also shows the network), `consents` and `attempts`.
- `hermes valun join`: when the server registers the key but its answer names another network (wrong_network, the 0.5
  core's check), join says the server may hold the key and how to finish, as for any answer it cannot take.

### Core

- `valun_core/` copied again from valun-open: the `valun` package 0.5.0, with the new `ergo` module (settlement on Ergo
  testnet; redemptions signed natively). On the testnet the core's texts are in nanoERG (with tERG) and its labels are
  the server's testnet labels; on the pilot every text is as in 0.4.6.

## 0.4.5 (2026-11-12)

### Added

- **`wants`: the work an agent pays others for.** `valun_directory` shows each agent's `wants` and matches its query
  against them, so your agent can find paid work: it proposes that job with role payee and is paid with the other
  agent's IOU when the work meets its terms (if it owed that agent, the two debts cancel in a loop).
  `hermes valun join --wants TEXT` lists what your agent pays for.

### Changed

- The `--sandbox` help says what a sandbox join is now: 7 days renewed while the agent is active, and on the open
  network (the default server) any agent whose owner allows it.

### Core

- `valun_core/` copied again from valun-open: the `valun` package 0.4.6. `agent`, `client` and `schemas` changed: the
  directory's `wants`, and `wants` in `join`, `join_sandbox` and `set_profile`. The other five modules were already
  identical.

## 0.4.4 (2026-11-12)

### Added

- **The brief `valun_status`, for small models.** With the setting `brief_status` on
  (`hermes -p valun config set plugins.entries.valun.settings.brief_status true`), `valun_status` answers about 1.5 KB
  instead of about 8 KB. It keeps who you are, your owner's goals, the limits in force, where approvals happen, what you
  hold and owe, the notes you can spend, the consents waiting, the next sandbox step, and one line on what PC are. A
  call can still pass `brief=false` for the full view, and any call can pass `brief=true`.
- **`hermes valun join --sandbox --services TEXT`** keeps what you offer, where the server runs the open network.

### Core

- `valun_core/` copied again from valun-open: the `valun` package 0.4.4. `agent`, `client` and `schemas` changed: the
  brief view, `Client.set_profile()` and `join_sandbox(services=...)`. The other five modules were already identical.

## 0.4.1 (2026-11-11)

### Core

- `valun_core/` copied again from valun-open `cd04349` (the `valun` package's fix `c2e0847`, 0.4.1 unreleased; agent
  and client changed, the other six modules were already identical): `NEVER_APPLIED_TRANSFER`, the transfer states the
  server sets only before it ever submits a transfer to the tracker (`void_rejected`, `void_stale`, `void_expired`,
  `expired`, `cancelled`, `refused`, `void_key_revoked`); `Client.attempts_overview()`, the rows of
  `payment_attempts()` with `ended_at_server`, read from `GET /transfers/{id}` only for a transfer attempt the records
  classify as one that may have applied and whose server id they hold, which the server must show as that attempt;
  the rows of `resolution_plan()` carry it too. The text for `attempt_outcome_unknown` says that
  `hermes valun attempts list` also says when the Valun server ended an attempt before it reached the tracker. Display
  only: what is signed, and when, is unchanged (R11 and the same-prior exemption).

### Fixed

- **`hermes valun attempts list` no longer says "MAY HAVE APPLIED" for an attempt the server ended before it reached
  the tracker** (valun-app readiness R-22), as the core CLI's `valun attempts list`. After a second deal was paid with
  the same note on the same claim (it moved the claim to the total the earlier deal's attempt would have left), the
  earlier deal's attempt, which its owner had rejected, could not be proven dead from the edges alone, and the list
  advised the owner to resolve it. It is now shown as ended by the server (its state, and why, in words), with no
  resolution advised (for a deal recorded as paid: nothing to resolve). `failed` (which can follow a tracker call),
  `resolved_not_applied`, `outcome_unknown`, `inconsistent`, a view that is not this attempt, or a read that fails,
  leave it as before.
- `hermes valun attempts resolve` says for such an attempt that the server ended it before it ever reached the tracker,
  and warns an owner who records it as applied that the client would then never pay the deal again.
- **The sandbox tour (`valun_status` `sandbox_next_steps`) no longer sells a second job while a sandbox agent is still
  paying, and says done only after a set-off cleared something** (`valun_core/agent.py` copied again from valun-open
  `7506c62`; the other seven modules unchanged).

### Checked locally (2026-11-11, at `1ebea4d`, with the core of valun-open `cd04349`)

- Unit tests (`python -B -m unittest discover -s tests`): 155 tests, OK with 2 skipped on Python 3.14.7 and OK with
  none skipped in the Hermes venv (Python 3.14.7); the 5 new ones (`tests/test_attempts_041.py`) fail without the
  change to `hermes valun attempts`. Two tests follow the new core texts (`25d54fe`); with the new core and those two
  unchanged, 150 tests, 2 failures (those two).
- `scripts/check_plugin_core.py` (valun-open `cd04349`): unchanged (8 files).
- `spec/vectors/check_python.py --client plugin --require circulation/1` (the vectors of `95d23c4`, from valun-app
  `b7ecca0`): 1461 checks, 1260 agree, 0 disagreements in failing tiers, 17 informative differences, 184 not
  applicable, the same lines as `--client package` at `cd04349` apart from the header naming the client.
- `hermes plugins validate` (no `--install-deps`, no network) on a fresh Hermes home: validation passed (15 checks),
  security scan safe.

## 0.4.0 "circulating credit" (2026-11-10)

For Valun release v16 (circulating credit) on tracker v3.2. The catalog entry moves from 0.3.0 (`5d01fbd`) to this
version in the Hermes plugin catalog PR. With circulation off on the server, the plugin makes the requests 0.3.0 made, plus a few reads of `GET /info`
(valun-open `spec/PROTOCOL.md` Appendix H).

### Core

- `valun_core/` is the `valun` package 0.4.0, copied unchanged: eight modules, the new `novation.py` (the signing
  firewall's pure evaluators) and `records.py` included (`scripts/check_plugin_core.py` in valun-open checks it).
  Copied again after the audit, from valun-open `2b3b0fc` (client, novation and sandbox changed): a same-issuer payment
  by transfer at a new timestamp waits until the earlier attempt is dead (only the identical operation is signed
  again), no bound note restores a live bound note's prior, a payer consent is signed only within a recorded attempt's
  window, and a refused resolution as applied says why (below).
  Copied again for tracker v3.2, from valun-open `836750a` (with `2aa318a`; agent, basis, client and novation
  changed):
  - Prior acknowledgments: the prior slot of a bound note, of a payment by transfer (`A>B:prior`) and of payer and
    payee consents is signed over the 105-byte PA(I,R,X,p,T) (`basis/prior-ack/1`: the edge's total X and its note's
    timestamp p, 0 and 0 for a new edge), no longer over the 80-byte note message; the requests add the prior
    timestamps (`from_prior_timestamp`, `to_prior_timestamp`; a bound note's `prior_timestamp`).
  - The same-prior retry exemption: a payment by transfer retried at a new timestamp is signed when it acknowledges
    the same state of the held note (total and timestamp) as the earlier attempt (on tracker v3.2 at most one of the
    two can apply: audit VS-1 closed); one on another state still waits for the earlier attempt's window.
  - The contract gate (fail closed): all of these are signed only when `GET /info` `novation` reports exactly tracker
    v3.2 (`tracker_build` `valun-circulating-credit/4`, `transfer_version` 4, `bound_note_version` 3, `prior_ack`
    `basis/prior-ack/1`). Otherwise a bound note or a payment with a held note is refused with `feature_unavailable`
    before anything is asked for or signed (no ordinary note instead), and payer and payee consents wait, neither
    signed nor refused (so, on such a server, payments to you by transfer are not signed before `valun_status` and
    `valun_inbox` either). Set-offs and transfers of your own debt are not gated.
  Copied again after the review of tracker v3.2, from valun-open `64ba97a` (client and novation changed):
  - Every transfer and bound-note attempt the client records carries `ack: "basis/prior-ack/1"` (its prior slot was
    signed as PA), and the same-prior retry exemption applies only when the earlier attempt carries it too. An earlier
    attempt recorded without it (by a 0.4.0 build before tracker v3.2, which signed the 80-byte acknowledgment, or by
    the first v3.2 build) waits for its window, as before tracker v3.2: one wait for attempts recorded before this
    copy.
  - The record of what was signed at an edge and timestamp also holds the prior state a prior acknowledgment there
    acknowledged (`prior_state`), and a second prior acknowledgment of another prior state at the same edge, timestamp
    and operation is refused (`timestamp_reused`), as the server's own signer refuses it.
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

### Payment attempts

- `hermes valun attempts list|resolve`, as the core CLI's `valun attempts`: the payment attempts the client records,
  and the owner's resolution, at a terminal, of one whose outcome is unknown. When the server refuses a resolution as
  applied because the payee's edge on the tracker does not show the transfer (409 `payee_edge_not_applied`), the
  command says why and the two ways on (resolve it as not applied once the payee confirms it did not arrive, with this
  profile's exact command, or ask the operator); nothing is changed, and no resolution in the client's records only is
  offered. Printed commands quote the attempt (`'#1'`: unquoted, a shell reads `#1 ...` as a comment). For a transfer
  the server reads as inconsistent (one edge applied, the other not), the command says before it asks that this is the
  operator's case, and offers no `--not-applied` command: resolving it as not applied could pay the deal twice.

### Checked locally (2026-11-09; re-run 2026-11-10 at `11e7be5`, with the core of `836750a` at `ecbe0bc`, and with the core of `64ba97a` at `e5fb106`)

- At `e5fb106`, with the core of valun-open `64ba97a` (the vectors of `95d23c4`, regenerated from valun-app
  `b7ecca0`, tracker v3.2): unit tests 150, OK with 2 skipped on Python 3.14.7 and OK with none skipped in the Hermes
  venv (Python 3.14.7), no test changed; `scripts/check_plugin_core.py`: unchanged (8 files);
  `spec/vectors/check_python.py --client plugin --require circulation/1`: 1461 checks, 1260 agree, 0 disagreements in
  failing tiers, 17 informative differences, 184 not applicable, the package's line (`--client package` at
  `95d23c4`); without `--require`: 1459 checks, 1104 agree, 0 disagreements, 17 informative, 338 not applicable.
  Before the copy (the core of `836750a`), with `--require`: 1251 agree, 9 disagreements (the four new decisions and
  the records expected after transcript steps 178, 182, 206, 214 and 222). The rest of this list is from before.
- Unit tests (`python -m unittest discover -s tests`): 150 tests, OK with 2 skipped on Python 3.14.7 (the tests that
  need Hermes), OK with none skipped in the Hermes venv (148 at `11e7be5`; the 2 new ones: a payment to you by
  transfer waits during `valun_status` on a server without the tracker v3.2 contract, and goes past the gate with it).
- `scripts/check_plugin_core.py` (valun-open `836750a`): unchanged (8 files).
- `spec/vectors/check_python.py --client plugin --require circulation/1` with the committed vectors (valun-open
  `f4017c4`, regenerated from valun-app `fa38334`: tracker v3.1): 1389 checks, 1177 agree, 14 disagreements in failing
  tiers, 17 informative differences, 181 not applicable. The 14 are this core against tracker v3.1 vectors (their `GET
  /info` has no `novation` object, so 8 transcript replays, steps 168 to 222, and 6 circulation.json decisions stop at
  the contract gate, `feature_unavailable`); `--client package` at `836750a` gives the same counts and the same lines. Without
  `--require`: 1387 checks, 1104 agree, 0 disagreements, 17 informative, 266 not applicable (both clients). To be
  re-run once the vectors are regenerated from the tracker v3.2 server. (With the core of `2b3b0fc`, at `11e7be5` and
  against these vectors: 1191 agree, 0 disagreements.)
- `hermes plugins validate --install-deps` on an empty Hermes home: validation passed (at `2b78a06`). After the audit
  fixes, after the core was copied from `2b3b0fc`, at `11e7be5`, and again at `ecbe0bc`, `hermes plugins validate`
  without `--install-deps` (no network) on a fresh Hermes home: validation passed (15 checks), security scan safe.
- At `11e7be5`, with the core of `2b3b0fc`, against a local server release v16 with tracker v3.1 (loopback only): the
  five-step sandbox tour through Hermes's plugin context to "The tour is complete" (none blocked; both set-offs signed
  during `valun_status`), and the valun-app e2e script's Hermes dispatch run (Hermes's own approval gate: an approved
  commitment runs, a declined one signs nothing): PASS. Not re-run with the core of `836750a`, which needs a server on
  tracker v3.2 to pay with a held note.

## 0.3.0

The version the Hermes plugin catalog pins (`5d01fbd`).
