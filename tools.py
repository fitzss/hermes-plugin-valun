"""Hermes adapter over the framework-neutral Valun core (valun_core, vendored from valun-open).

Each handler returns a JSON string and never raises (Hermes plugin contract). The agent's signing key and token come
only from Hermes's secret store (agent.secret_scope.get_secret); they are never returned, logged or written by these
handlers. The plugin's own data folder holds only non-secret state: plugin-data/valun/valun-records.json, the client's
records (every key of valun_core.client.CORE_STATE_KEYS: its credit notes and payment attempts, what it signed for
debt transfers and set-offs, the tracker and unit it is pinned to, its consent policy and decisions), changed under a
lock file.

Approvals bound to what the owner saw (confirm_commitments on): the approval hook (__init__.py) prepares every Valun
commitment with the core's prepare(), from one snapshot of what it would sign, and remembers the arguments that binds
(remember_binding); the handler signs with exactly those once Hermes's approval has passed (take_binding), and refuses
a commitment it holds no binding for. Consents (process_consents on): before a Valun tool that is not read-only, the
core signs the consents the owner's policy allows, at most once a minute (ValunAgent.consents_if_due); before
valun_status and valun_inbox it signs the value-safe ones only (set-offs, and payments to this participant by transfer
as the policy allows; never a transfer of its own debt or a payment of its own), at most once a minute too, and the
result says what it signed (consents_signed_now; decision D-3 a, ValunAgent consents_on_reads). Nothing runs in the
background.

The results the core writes for an agent name its own CLI's commands (`valun config ...`, `valun attempts ...`); the
handlers name this profile's Hermes commands instead (hermes_result).
"""
import copy
import json
import os
import re
import threading
import time

try:
    from .valun_core import agent as va, client as vc  # inside the Hermes plugin package
except ImportError:  # standalone (tests)
    from valun_core import agent as va, client as vc

DEFAULT_SERVER = 'https://valun-desk.fly.dev'
SETUP_HINT = 'run `hermes valun join` (or `hermes valun enroll`) in a terminal'   # setup_hint(ctx) names the profile
RECORDS_FILE = 'valun-records.json'        # plugin-data/valun/: every CORE_STATE_KEY, no secret
LEGACY_FILE = 'issued-notes.json'           # 0.3's record of credit notes issued: valun-records.json starts from it
CONSENT_ROLES = ('setoff', 'as_issuer', 'as_new_creditor')    # the plugin config's consents.<role> (true or false)
CONSENT_MAX_PC = va.SANDBOX_CONSENT_POLICY['max_PC']          # one consent's cap, for a policy the config starts
CONSENT_CAP_LIMIT = 100000                  # consents.max_PC: a whole number of PC up to this, or none (as `valun config`)
_UNSET = object()
BLOCK_TEXT = 'Valun could not read what you would sign; nothing was signed.'
RECORDS_BLOCK_TEXT = ("Valun cannot read its own records (plugin-data/valun/valun-records.json), so it signs nothing; "
                      'nothing was signed. Tell your owner (`hermes valun doctor` says how to fix it).')
APPROVAL_MISSING = {
    'error': 'approval_missing',
    'message': ('Not signed: Hermes asks your owner before each Valun commitment, and no approval is bound to exactly this '
                'call (it did not pass Hermes\'s approval step, or its arguments changed after that step).'),
    'what_this_means': ('Nothing was signed or sent. Call the tool again with the same arguments: Hermes then asks your '
                        'owner, and the call signs exactly what your owner approves.')}
# consent_policy_in_force.how_to_change in valun_status, for Hermes (the core's names `valun config consents.*`).
HOW_TO_CHANGE = ('your owner changes the local policy in a terminal with `{roles}` (<role>: setoff, as_issuer or '
                 'as_new_creditor) and `{cap}` (N: whole PC up to 100000, or none for no cap); your Valun client signs '
                 'consents by itself only while process_consents is on (`{on}`), and `{status}` shows the policy in '
                 'force; your owner changes the mandate in the owner console; the stricter of the two applies')
ROLES_NOTE = ' (<role>: setoff, as_issuer or as_new_creditor)'
# The core CLI's commands that core texts for the agent or the owner name (hermes_words): (core command, 'setting' or
# 'valun', the rest of this profile's Hermes command, text after it).
CORE_COMMANDS = (
    ('`valun config consents.setoff|consents.as_issuer|consents.as_new_creditor on|off`', 'setting',
     'consents.<role> true|false', ROLES_NOTE),
    ('`valun config consents.max_PC N`', 'setting', 'consents.max_PC N', ''),
    ('`valun config consents.*`', 'setting', 'consents.<role> true|false', ROLES_NOTE),
    ('`valun attempts list`', 'valun', 'attempts list', ''),
    ('`valun attempts resolve`', 'valun', 'attempts resolve', ''),
    ('`valun join --sandbox`', 'valun', 'join --sandbox', ''),
    ('`valun join`', 'valun', 'join', ''),
    ('`valun enroll`', 'valun', 'enroll', ''),
    ('`valun doctor`', 'valun', 'doctor', ''),
    ('`valun policy show`', 'valun', 'status', ''),
    ('`valun clearing run`', 'valun', 'consents --process', ''),
)
_WORD = re.compile(r'[a-z_]{1,40}\Z')
_CORE_AGENT = va.ValunAgent             # argument_refusal() always asks the real core, whatever stands in for it
# Re-exported for the plugin's CLI, hook and tests.
limits_body, loosens, recently_active, terms_text = va.limits_body, va.loosens, va.recently_active, va.terms_text
LIMIT_FIELDS, HONEST_LABELS, SETTLEMENT_NOTE, NOT_SETTLED = va.LIMIT_FIELDS, va.HONEST_LABELS, va.SETTLEMENT_NOTE, va.NOT_SETTLED


# ---------------------------------------------------------------------------------------------------------------
# Settings (the plugin's Hermes config)

def confirm_commitments(ctx):
    """Hermes asks the owner before each Valun commitment (config confirm_commitments, default on). Only false turns it
    off; any other value keeps it on."""
    return ctx.get_config('confirm_commitments', default=True) is not False


def consents_on(ctx):
    """The client signs consents by itself (config process_consents, default off; `hermes valun join --sandbox` turns it
    on). Only true turns it on."""
    return ctx.get_config('process_consents', default=False) is True


def server_url(ctx):
    return (ctx.get_config('server_url', default=DEFAULT_SERVER) or DEFAULT_SERVER).rstrip('/')


def consent_cap(ctx):
    """The plugin config's consents.max_PC, as `valun config consents.max_PC` takes it: (cap, problem). cap is a whole
    number of PC from 0 to CONSENT_CAP_LIMIT (a digit string counts), None for no cap (`none`, which `hermes config
    set` stores as null, or the string 'none'), or _UNSET when the setting is absent. A value that is none of these
    (negative, too large, a fraction, true/false, other text) fails closed: cap 0 (no consent above 0 PC is signed),
    and problem says why (None otherwise)."""
    value = ctx.get_config('consents.max_PC', default=_UNSET)
    if value is _UNSET:
        return _UNSET, None
    if value is None or (isinstance(value, str) and value.strip().lower() == 'none'):
        return None, None
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,7}', value.strip()):
        value = int(value.strip())
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= CONSENT_CAP_LIMIT:
        return value, None
    shown = ''.join(ch if ch.isprintable() else ' ' for ch in repr(value))[:40]
    return 0, (f'consents.max_PC is {shown}, not a whole number of PC from 0 to {CONSENT_CAP_LIMIT} or none, so your '
               'client signs no consent above 0 PC until it is fixed')


def configured_policy(ctx, kept=None):
    """The local consent policy this profile's owner configured: the one kept in valun-records.json (`kept`), with each
    consents.<role> the plugin config sets (true or false) in place of the kept flag, and the config's consents.max_PC
    (consent_cap: none removes the cap) in place of the kept cap. A config setting with no kept policy starts one,
    capped at CONSENT_MAX_PC per consent unless consents.max_PC says otherwise. None when neither holds one (every role
    off)."""
    flags = {}
    for role in CONSENT_ROLES:
        value = ctx.get_config(f'consents.{role}', default=None)
        if isinstance(value, bool):
            flags[role] = value
    cap, _problem = consent_cap(ctx)
    if not flags and cap is _UNSET:
        return kept if isinstance(kept, dict) else None
    policy = dict(kept) if isinstance(kept, dict) else {'max_PC': CONSENT_MAX_PC}
    policy.update(flags)
    if cap is None:
        policy.pop('max_PC', None)
    elif cap is not _UNSET:
        policy['max_PC'] = cap
    return policy


def hermes_present():
    """Does this process run inside Hermes (its secret store, agent.secret_scope, can be imported)?"""
    try:
        from agent.secret_scope import get_secret  # noqa: F401
    except ImportError:
        return False
    return True


def plugin_data_folder():
    """plugin-data/valun in the active Hermes profile (created when missing). None only outside Hermes (neither its
    plugin storage nor its secret store can be imported: the tests), where the records live in memory. Inside a Hermes
    whose plugin storage (plugins.plugin_storage) cannot be imported, this raises, so the store is records_unreadable
    and signs nothing: its records (the signing firewall, the payment attempts) never fall back to memory there."""
    try:
        from plugins.plugin_storage import plugin_data_dir
    except ImportError as e:
        if hermes_present():
            raise RuntimeError("Hermes is running, but its plugin storage (plugins.plugin_storage) cannot be imported "
                               f'({type(e).__name__}), so this plugin has no folder for its records') from None
        return None
    return str(plugin_data_dir('valun'))


data_dir = plugin_data_folder       # the store's folder (the tests stand in for it)


# ---------------------------------------------------------------------------------------------------------------
# This profile's Hermes commands, as printed for the owner (by the operator command, and in results for the agent)

_PROFILE_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')


def _home_is_profile(name):
    """True when this process's Hermes home is the named profile NAME, <Hermes root>/profiles/NAME, compared the way
    Hermes's get_active_profile_name compares it; False when it is not, or when Hermes cannot say."""
    try:
        from hermes_constants import get_default_hermes_root, get_hermes_home
        return get_hermes_home().resolve() == (get_default_hermes_root() / 'profiles' / name).resolve()
    except Exception:  # noqa: BLE001
        return False


def profile_name(ctx):
    """This Hermes profile's name ('default' or the name under profiles/), or None for a Hermes home that is no named
    profile. Hermes calls such a home 'custom' (a folder under profiles/ whose name is no profile id), and also calls
    a profile really named custom so: that one, <Hermes root>/profiles/custom, keeps its name, since `hermes -p custom`
    is the command that reaches it."""
    try:
        name = getattr(ctx, 'profile_name', None)
    except Exception:  # noqa: BLE001
        name = None
    if not (isinstance(name, str) and _PROFILE_RE.match(name)):
        return None
    return None if name == 'custom' and not _home_is_profile('custom') else name


def hermes_cmd(ctx, rest, sub='valun'):
    """A command for THIS profile: `hermes -p NAME valun REST` (the default profile named too, so a sticky `hermes
    profile use` elsewhere cannot redirect it), or `hermes valun REST` in a home that is no named profile."""
    name = profile_name(ctx)
    return f"hermes {'-p ' + name + ' ' if name else ''}{sub} {rest}".rstrip()


def setting_cmd(ctx, key, value):
    """The command that sets one of this plugin's settings in this profile."""
    return hermes_cmd(ctx, f'set {setting_path(ctx, key)} {value}', sub='config')


def setting_path(ctx, key):
    pid = getattr(ctx, 'plugin_id', None)
    pid = pid if isinstance(pid, str) and pid else 'valun'
    return f'plugins.entries.{pid}.settings.{key}'


def setup_hint(ctx):
    return f'run `{hermes_cmd(ctx, "join")}` (or `{hermes_cmd(ctx, "enroll")}`) in a terminal'


def how_to_change(ctx):
    return HOW_TO_CHANGE.format(roles=setting_cmd(ctx, 'consents.<role>', 'true|false'),
                                cap=setting_cmd(ctx, 'consents.max_PC', 'N'),
                                on=setting_cmd(ctx, 'process_consents', 'true'), status=hermes_cmd(ctx, 'status'))


def hermes_words(ctx, text):
    """`text` (a core message for the agent or the owner) with each core-CLI command it names (CORE_COMMANDS) replaced
    by this profile's Hermes command; any other text is returned as it is."""
    if not isinstance(text, str) or '`valun ' not in text:
        return text
    for core, sub, rest, after in CORE_COMMANDS:
        if core in text:
            cmd = setting_cmd(ctx, *rest.split(' ', 1)) if sub == 'setting' else hermes_cmd(ctx, rest)
            text = text.replace(core, f'`{cmd}`{after}')
    return text


def hermes_result(ctx, out):
    """A core tool result with this profile's Hermes commands where the core names its CLI's: valun_status's
    consent_policy_in_force.how_to_change (HOW_TO_CHANGE), and any what_this_means, how_to_change or message that
    names a core-CLI command (hermes_words: attempt_outcome_unknown's `valun attempts list` and `valun attempts
    resolve`, say). In place; returns `out`."""
    if not isinstance(out, dict):
        return out
    policy = out.get('consent_policy_in_force')
    if isinstance(policy, dict) and isinstance(policy.get('how_to_change'), str):
        policy['how_to_change'] = how_to_change(ctx)
    for k in ('what_this_means', 'how_to_change', 'message'):
        if isinstance(out.get(k), str):
            out[k] = hermes_words(ctx, out[k])
    return out


def secret_value(name):
    """A credential from Hermes's secret store (the active profile's); None outside Hermes."""
    try:
        from agent.secret_scope import get_secret
    except ImportError:
        return None
    return get_secret(name) or None


# ---------------------------------------------------------------------------------------------------------------
# The store

class _RecordsFile(vc.FileStore):
    """valun-records.json, read and written with the core FileStore's own protocol: changes under the lock file
    '<file>.lock' (O_CREAT|O_EXCL; waits up to LOCK_WAIT_S; a lock left by a process that died is taken over safely),
    every write atomic and private, and a file that cannot be read is records_unreadable. Only HermesStore writes it,
    and only CORE_STATE_KEYS (never a secret)."""

    def __init__(self, path):       # nothing is read here: HermesStore reads when and how it needs to
        self.path, self.state = path, {}


class HermesStore:
    """The core's store inside Hermes. Settings come from the plugin's Hermes config (server_url, participant_id); the
    agent's key and token from Hermes's secret store, never written here; the client's records (every CORE_STATE_KEY) from
    plugin-data/valun/valun-records.json.

    update(fn) changes the records under the file's lock, on the file as it is now (another Hermes process may have
    changed it), so concurrent processes never lose each other's records; save() writes the keys this store holds over
    the file as it is now, under the same lock; reload() reads it again. Before valun-records.json exists, the 0.3 record
    plugin-data/valun/issued-notes.json is its starting point: it is copied into valun-records.json on first load (and
    left in place, no longer read). When a records file exists but cannot be read, the store is `unreadable`: update,
    save and reload raise records_unreadable, and the hook and the handlers refuse every call that could sign, until the
    owner fixes the file (it is never replaced by empty records).

    The local consent policy (consent_policy, see configured_policy) is given to the core only while process_consents is
    on: with it off, the client declares no consent role to the server (X-Valun-Features) and signs no consent by itself.
    It is kept in the records file either way (kept_policy)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.process_consents = consents_on(ctx)
        self.state = {}
        self.unreadable = None          # the records_unreadable error, when a records file exists but cannot be read
        self.kept_policy = None         # the configured consent policy (in force only while process_consents is on)
        self._file = self._legacy = None
        try:
            folder = data_dir()
        except Exception as e:  # noqa: BLE001 - without its folder the client can keep no record: it signs nothing
            folder = None
            self.unreadable = vc.ValunError('records_unreadable', f'the plugin data folder cannot be used ({type(e).__name__}: '
                                                                  f'{e}); nothing was changed or signed')
        if folder:
            self._file = _RecordsFile(os.path.join(folder, RECORDS_FILE))
            self._legacy = os.path.join(folder, LEGACY_FILE)
        data = {}
        if self.unreadable is None:
            try:
                data = self._first_load()
            except vc.ValunError as e:
                self.unreadable = e
        self._compose(data, self.state)

    @property
    def path(self):
        """valun-records.json (None outside Hermes, where the records live in memory)."""
        return self._file.path if self._file is not None else None

    # ---------- the core's store interface ----------
    def secret(self):
        return secret_value('VALUN_SIGNING_SECRET')

    def token(self):
        return secret_value('VALUN_AGENT_TOKEN')

    def save_credentials(self, secret_hex, token):
        raise vc.ValunError('not_here', 'credentials are stored only by `hermes valun join` / `enroll`, run by the operator')

    def update(self, fn):
        """Under the records file's lock: read it as it is now, apply fn(state) and write it. self.state is updated in
        place to what was written; if fn raises, nothing is written or changed. Returns fn's result."""
        self._check()
        if self._file is None:
            work = copy.deepcopy(self.state)
            result = fn(work)
            self.state.clear()
            self.state.update(work)
            return result
        with self._file._locked():
            data = self._current_checked()
            work = {}
            self._compose(data, work)
            result = fn(work)
            self._file._write(self._merged(data, self._stored(work)))
        self.state.clear()
        self.state.update(work)
        return result

    def save(self):
        """Write the records this store holds over the file as it is now, under its lock: the keys this store holds
        replace the stored ones, the others stay as another process left them (removing a key is update()'s work)."""
        self._check()
        if self._file is None:
            return
        with self._file._locked():
            data = self._current_checked()
            data.update({k: self.state[k] for k in vc.CORE_STATE_KEYS if k in self.state})
            self._file._write(data)

    def reload(self):
        """Read the records again (another process may have changed them since this store read them)."""
        self._check()
        if self._file is not None:
            self._compose(self._current_checked(), self.state)

    def refusal(self):
        """A fresh copy of the records_unreadable error (None when the records can be read)."""
        return vc.ValunError(self.unreadable.code, str(self.unreadable)) if self.unreadable is not None else None

    # ---------- inside ----------
    def _check(self):
        if self.unreadable is not None:
            raise self.refusal()

    def _current(self):
        """The records as stored now: valun-records.json; before it exists, the 0.3 record (issued-notes.json); {} when
        neither exists. records_unreadable when the one in use exists but cannot be read."""
        if os.path.exists(self._file.path):
            return self._file._read()
        if not os.path.exists(self._legacy):
            return {}
        try:
            with open(self._legacy, encoding='utf-8') as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = None
        issued = old.get('issued') if isinstance(old, dict) else None
        if not isinstance(issued, dict):
            raise vc.ValunError('records_unreadable', f'cannot read the record of credit notes issued, {self._legacy}; '
                                                      'nothing was changed or signed')
        return {'issued': issued}

    def _current_checked(self):
        try:
            return self._current()
        except vc.ValunError as e:
            if e.code == 'records_unreadable':
                self.unreadable = e
            raise

    def _first_load(self):
        """The records at construction. The 0.3 record is copied into valun-records.json here, under the lock; when the
        lock is busy or the folder refuses the write, the next update() writes it instead (it starts from the same
        record)."""
        if self._file is None:
            return {}
        data = self._current()
        if data and not os.path.exists(self._file.path):
            try:
                with self._file._locked():
                    data = self._current()
                    if not os.path.exists(self._file.path):
                        self._file._write(data)
            except vc.ValunError as e:
                if e.code != 'profile_busy':
                    raise
            except OSError:
                pass
        return data

    def _compose(self, data, into):
        """The state the core works on, in place: the plugin's settings and the stored CORE_STATE_KEYS, with the
        configured consent policy only while process_consents is on."""
        state = {'server': server_url(self.ctx), 'participant_id': self.ctx.get_config('participant_id', default='') or ''}
        for k in vc.CORE_STATE_KEYS:
            if k in data and k != 'consent_policy':
                state[k] = data[k]
        stored = data.get('consent_policy')
        policy = configured_policy(self.ctx, stored)
        self.kept_policy = policy if policy is not None else stored     # a stored value that is no policy: kept as is
        if self.process_consents and isinstance(policy, dict):
            state['consent_policy'] = policy
        into.clear()
        into.update(state)

    def _stored(self, state):
        """What a state writes: its CORE_STATE_KEYS (the consent policy kept aside while it is not in force)."""
        out = {k: state[k] for k in vc.CORE_STATE_KEYS if k in state and k != 'consent_policy'}
        policy = state['consent_policy'] if 'consent_policy' in state else self.kept_policy
        if policy is not None:
            out['consent_policy'] = policy
        return out

    @staticmethod
    def _merged(data, stored):
        """The file to write: `stored` for every CORE_STATE_KEY, and any other key the file holds as it is (written by
        another version of the plugin: never dropped)."""
        out = {k: v for k, v in data.items() if k not in vc.CORE_STATE_KEYS}
        out.update(stored)
        return out


class _Keyless:
    """A store holding no key, token or server: an agent over it can sign nothing and sends nothing."""

    def __init__(self):
        self.state = {}

    def secret(self):
        return None

    def token(self):
        return None

    def save(self):
        pass

    def save_credentials(self, *a):
        raise vc.ValunError('not_here', 'a keyless store holds no credentials')


# ---------------------------------------------------------------------------------------------------------------
# The agent

def _approvals_text(ctx):
    if confirm_commitments(ctx):
        text = ('Before you sign an offer, counter-offer, acceptance, credit note or payment with a note you hold, Hermes asks '
                'your owner in this conversation and shows exactly what you would sign; wait for the answer.')
    else:
        text = 'Hermes does not ask your owner before you sign (confirm_commitments is off).'
    if consents_on(ctx):
        text += (" Your Valun client also signs, by itself, the consents your owner's policy allows (set-offs that leave "
                 'your net position unchanged, transfers of your own debt and, where allowed, payments to you by transfer), '
                 'before Valun tool calls that are not read-only; set-offs and payments to you also before valun_status '
                 'and valun_inbox (consents_signed_now in their result lists what it signed then): never ask your owner '
                 'about those.')
    return text


def agent_for(ctx):
    """The core's agent over this profile (a HermesStore), consenting by itself only with process_consents on: before
    Valun tools that are not read-only (consents_if_due), and, as Hermes runs no consent loop, the value-safe consents
    before valun_status and valun_inbox too (consents_on_reads, decision D-3 a; not while its records cannot be read).
    declare_roles is left to the core (as process_consents): the plugin signs no consent by itself with it off, so its
    client then declares no consent role and withdraws the ones it declared before (decision D-3 b)."""
    store = HermesStore(ctx)
    return va.ValunAgent(store, approvals=_approvals_text(ctx), setup_hint=setup_hint(ctx),
                         check_link_hosts=bool(ctx.get_config('check_link_hosts', default=True)),
                         process_consents=store.process_consents,
                         consents_on_reads=store.process_consents and store.unreadable is None)


def argument_refusal(name, args):
    """The core's own refusal of these arguments, where it refuses them before it needs its key (a misspelt review
    decision: bad_decision), from an agent over a store that holds no key, token or server, so it can sign and send
    nothing. None when the core would need its key to go further."""
    try:
        out = _CORE_AGENT(_Keyless(), setup_hint=SETUP_HINT, process_consents=False).call_safe(name, dict(args or {}))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(out, dict) or out.get('error') in (None, 'not_connected', 'not_configured', 'internal_error'):
        return None
    return out


def block_text(refusal, argument=None):
    """The approval hook's message when prepare() refused (nothing is asked or signed): BLOCK_TEXT, then the code (and
    cause) of the refusal and what it means, or, when the core refuses the arguments themselves, that refusal. All of it
    is this client's own text, never the server's."""
    parts = [BLOCK_TEXT]
    if isinstance(argument, dict) and isinstance(argument.get('error'), str) and _WORD.match(argument['error']):
        parts.append(f"({argument['error']}) {argument.get('message') or ''}".strip())
        return ' '.join(parts)
    refusal = refusal if isinstance(refusal, dict) else {}
    words = [w for w in (refusal.get('error'), refusal.get('cause')) if isinstance(w, str) and _WORD.match(w)]
    if words:
        parts.append('(' + ': '.join(dict.fromkeys(words)) + ')')
    if isinstance(refusal.get('what_this_means'), str):
        parts.append(refusal['what_this_means'])
    ms = refusal.get('retry_after_ms')
    if isinstance(ms, int) and not isinstance(ms, bool) and ms > 0:
        parts.append(f'Retry in {ms // 1000 + 1} s.')
    return ' '.join(parts)


# ---------------------------------------------------------------------------------------------------------------
# Approvals bound to what the owner saw. The approval hook remembers the arguments the core's prepare() bound for every
# commitment it prepares (pinned to the terms hash, the delivery's sha256, a payment's quote and claim: whatever the
# approval text shows); the handler signs with exactly those arguments once Hermes's approval has passed, so an offer
# or delivery that changed in between is refused, not signed. Kept in memory only (no secrets in it), one binding per
# exact call: the hook's newest replaces an older one (which then belonged to an approval the owner declined, or that
# timed out: Hermes runs the hook and its approval prompt one call at a time, and the handler takes its binding as soon
# as the approval passes), and the handler uses it once. Bindings older than a day are dropped; at most _BOUND_MAX kept.
_BOUND, _BOUND_LOCK, _BOUND_KEEP_S, _BOUND_MAX = {}, threading.Lock(), 24 * 3600, 256


def _call_key(name, args):
    return json.dumps([name, args or {}], sort_keys=True, ensure_ascii=False, default=str)


def remember_binding(name, args, bound):
    """For the approval hook: the arguments a prepared commitment signs with, for the handler of exactly this call."""
    with _BOUND_LOCK:
        now = time.monotonic()
        for k in [k for k, (t, _b) in _BOUND.items() if now - t > _BOUND_KEEP_S]:
            del _BOUND[k]
        key = _call_key(name, args)
        _BOUND.pop(key, None)
        while len(_BOUND) >= _BOUND_MAX:
            del _BOUND[min(_BOUND, key=lambda k: _BOUND[k][0])]
        _BOUND[key] = (now, copy.deepcopy(bound))


def take_binding(name, args):
    """For the handler: the arguments the approval hook bound for exactly this call (used once), or None."""
    with _BOUND_LOCK:
        hit = _BOUND.pop(_call_key(name, args), None)
    if hit is None or time.monotonic() - hit[0] > _BOUND_KEEP_S:
        return None
    return hit[1]


def _run(ctx, name, args):
    """One tool call (see make_handlers)."""
    bound = take_binding(name, args)
    agent = agent_for(ctx)
    store = agent.store
    if getattr(store, 'unreadable', None) is not None and name not in va.READ_ONLY_TOOLS:
        return va.error_payload(store.refusal(), setup_hint(ctx))
    if bound is not None:                      # prepared by the hook, and Hermes's approval passed: sign exactly that
        out = agent.call_safe(name, bound)
    elif confirm_commitments(ctx) and agent.is_commitment(name, args):
        out = argument_refusal(name, args) or dict(APPROVAL_MISSING)   # never signed without the owner's approval
    else:
        out = agent.call_safe(name, args)
    return hermes_result(ctx, out)


def make_handlers(ctx):
    """The nine tools' handlers. Each returns a JSON string and never raises. With confirm_commitments on, a commitment
    signs only the arguments the approval hook bound for exactly that call (and is refused without them). With
    process_consents on, the core signs the consents the owner's policy allows before a tool that is not read-only, and
    the value-safe ones before valun_status and valun_inbox (each at most once a minute). Results name this profile's
    Hermes commands where the core names its CLI's (hermes_result)."""
    def wrap(name):
        def handler(args, **kwargs):
            try:
                out = _run(ctx, name, dict(args) if isinstance(args, dict) else {})
            except Exception as e:  # noqa: BLE001 - a handler never raises (Hermes plugin contract)
                try:
                    hint = setup_hint(ctx)
                except Exception:  # noqa: BLE001
                    hint = SETUP_HINT
                out = va.error_payload(e, hint)
            return json.dumps(out, ensure_ascii=False, default=str)
        return handler
    return {name: wrap(name) for name in va.TOOL_NAMES}
