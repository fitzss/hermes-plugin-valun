# Changelog

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
