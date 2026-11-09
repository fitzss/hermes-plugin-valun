"""Valun plugin for Hermes Agent.

Registers nine tools; a pre_tool_call hook that, while `confirm_commitments` is on, sends every Valun commitment (as
the shared core classifies it, failing closed) through Hermes's own approval gate with the text the core prepared from
one snapshot of what would be signed, and blocks the call when that snapshot cannot be read; and the operator command
`hermes valun join|enroll|status|doctor|consents|attempts`, run by a person in a terminal.
"""
import getpass
import logging
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone

from . import tools
from .valun_core import agent as va, client as vc, novation as nv, schemas

logger = logging.getLogger(__name__)

TOOLSET = 'valun'


def _gate(ctx, tool_name, args):
    """pre_tool_call. For a Valun tool the core classifies as a commitment (ValunAgent.is_commitment: every tool and
    argument it does not know counts as one), while confirm_commitments is on: the core's prepare() binds the call and
    writes its approval text from one snapshot of what it would sign; the binding is remembered for the handler, and
    Hermes's own approval gate asks the owner with that text (a random rule_key, so a saved "always" never matches a
    later commitment). When preparing fails, the call is blocked: nothing is asked and nothing is signed. Never
    auto-approves; under cron and other unattended runs Hermes decides (by default it denies)."""
    if not (isinstance(tool_name, str) and tool_name.startswith('valun_')):
        return None                                   # not a Valun tool
    if not tools.confirm_commitments(ctx):
        return None                                   # off: the agent acts within the owner's limits without asking
    a = dict(args) if isinstance(args, dict) else {}
    try:
        agent = tools.agent_for(ctx)
        if not agent.is_commitment(tool_name, a):
            return None
        if getattr(getattr(agent, 'store', None), 'unreadable', None) is not None:
            return {'action': 'block', 'message': tools.RECORDS_BLOCK_TEXT}
        prepared, refusal = agent.prepare_or_refusal(tool_name, a)
    except Exception:  # noqa: BLE001 - whatever went wrong, nothing is asked and nothing is signed
        if tool_name in va.NOT_COMMITMENT_TOOLS:
            return None                               # these never commit the owner, whatever their arguments
        return {'action': 'block', 'message': tools.BLOCK_TEXT}
    if refusal is not None or not isinstance(prepared, dict):
        return {'action': 'block', 'message': tools.block_text(refusal, tools.argument_refusal(tool_name, a))}
    tools.remember_binding(tool_name, a, prepared['args'])      # the handler signs exactly these
    if prepared['text'] is None:
        return None                                   # this snapshot shows the call commits nothing: nothing to ask
    return {'action': 'approve', 'message': prepared['text'], 'rule_key': f'valun:{tool_name}:{secrets.token_hex(8)}'}


def register(ctx):
    handlers = tools.make_handlers(ctx)

    def has_credentials():
        try:
            from agent.secret_scope import get_secret
            return bool(get_secret('VALUN_AGENT_TOKEN') and get_secret('VALUN_SIGNING_SECRET'))
        except Exception:  # noqa: BLE001 - unavailable means "hide the tools", never crash
            return False

    for schema in schemas.ALL:
        name = schema['name']
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handlers[name],
                          check_fn=(None if name == 'valun_status' else has_credentials), emoji='🤝')

    def gate(tool_name, args, **kwargs):
        return _gate(ctx, tool_name, args)

    ctx.register_hook('pre_tool_call', gate)
    ctx.register_cli_command(name='valun', help='Connect this Hermes profile to Valun (operator only)',
                             setup_fn=lambda sub: _setup_cli(sub, ctx), handler_fn=lambda args: _run_cli(args, ctx))


# ---------------------------------------------------------------------------------------------------------------
# Operator command. Run it yourself in a terminal; the agent should never run it.

def _setup_cli(subparser, ctx):
    subs = subparser.add_subparsers(dest='valun_command')
    e = subs.add_parser('enroll', help='generate this agent\'s key here and register it with your Valun participant')
    e.add_argument('--server', help='Valun server URL (default: configured server_url)')
    e.add_argument('--participant', help='your participant id (vp_...) from the owner console')
    e.add_argument('--code-from-stdin', action='store_true', help='read the enrollment code from stdin (for scripts) instead of a hidden prompt')
    subs.add_parser('status', help='show the Valun connection of this profile and its consent policy (never shows secrets)')
    subs.add_parser('doctor', help='check this profile for everything Valun needs, and say how to fix what is missing')
    j = subs.add_parser('join', help='join the Valun pilot with an invite (or --sandbox, no invite) and connect this agent, in one step')
    j.add_argument('--sandbox', action='store_true',
                   help='join without an invite as a sandbox-only participant: deals only with the always-on sandbox agents, '
                        'small limits, for a week; your client also signs its consents by itself (process_consents on)')
    j.add_argument('--handle', help='your public handle (3-24 letters, digits, _ or -); asked if omitted')
    j.add_argument('--operator', help='who controls this agent, shown publicly (your name or organisation); asked if omitted')
    j.add_argument('--display-name', default='', help='optional public display name')
    j.add_argument('--about', default='', help='optional public description of you or your agent')
    j.add_argument('--services', default='', help='optional public list of what your agent offers')
    j.add_argument('--server', help='Valun server URL (default: configured server_url)')
    j.add_argument('--invite-from-stdin', action='store_true', help='read the invite code from stdin instead of a hidden prompt')
    j.add_argument('--owner-token-file',
                   help='write your owner token to this NEW file (created private; an existing file is never overwritten) '
                        'instead of showing it once; when it cannot be written the token is shown and join exits 1')
    j.add_argument('--approvals', choices=['conversation', 'console', 'none'], default='conversation',
                   help="where commitments are approved: 'conversation' (Hermes asks you before each one; default), "
                        "'console' (they wait in the web owner console), or 'none' (autonomous within your limits)")
    j.add_argument('--direct-tools', action='store_true',
                   help='expose the Valun tools to the model directly (sets tools.tool_search.enabled=off in THIS profile); '
                        'recommended for a dedicated Valun profile and for small models')
    co = subs.add_parser('consents', help='circulating credit: the consents (set-offs, debt transfers) waiting for your '
                                          'Valun client; with --process, sign what your consent policy allows now')
    co.add_argument('--process', dest='valun_process', action='store_true',
                    help='sign the consents your consent policy and owner mandate allow, and refuse the others (you, the owner, run this)')
    at = subs.add_parser('attempts', help="the payment attempts your Valun client records, and the OWNER's resolution of "
                                          'one whose outcome is unknown')
    ats = at.add_subparsers(dest='valun_attempts')
    ats.add_parser('list', help='every payment attempt your client records, and how it judges each now')
    ar = ats.add_parser('resolve', help='OWNER only, at a terminal: record whether a payment attempt whose outcome is unknown '
                                        'applied (after asking the payee)')
    ar.add_argument('valun_deal', metavar='DEAL', help='the deal id (vd_...)')
    ar.add_argument('valun_attempt', metavar='ATTEMPT',
                    help="#n from `hermes valun attempts list`, or the server's id of a transfer (vt_...)")
    how = ar.add_mutually_exclusive_group(required=True)
    how.add_argument('--not-applied', dest='valun_not_applied', action='store_true', help='the payment did not arrive')
    how.add_argument('--applied', dest='valun_applied', action='store_true', help='the payment arrived: the deal is paid by it')
    ar.add_argument('--note', dest='valun_note', default=None, help='a short note for the server audit line (at most 300 characters)')
    ar.add_argument('--owner-token-file', dest='valun_owner_token_file',
                    help='the file holding your owner token (read once, never stored); otherwise it is asked for, hidden')


class _MemoryStore:
    """Holds credentials only in memory until they are handed to Hermes's secret store."""

    def __init__(self):
        self.state, self._secret, self._token = {}, None, None

    def secret(self):
        return self._secret

    def token(self):
        return self._token

    def save(self):
        pass

    def save_credentials(self, secret_hex, token):
        self._secret, self._token = secret_hex, token


def _hermes_config_set(key, value):
    # Hermes's documented route for storing a secret (`hermes config set`), run as an argument list: the value never
    # passes through a shell or its history and is never printed. The active profile home is pinned explicitly, so a
    # secret always lands in the profile this command was run for.
    env = dict(os.environ)
    try:
        from hermes_constants import get_hermes_home
        env['HERMES_HOME'] = str(get_hermes_home())
    except Exception:  # noqa: BLE001 - fall back to the inherited environment
        pass
    r = subprocess.run([sys.executable, '-m', 'hermes_cli.main', 'config', 'set', key, value],
                       capture_output=True, text=True, env=env)
    return r.returncode == 0


_PROFILE_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')


def _profile(ctx):
    """This Hermes profile's name ('default' or the name under profiles/), or None for a Hermes home that is no named
    profile (a HERMES_HOME set to another folder)."""
    try:
        name = getattr(ctx, 'profile_name', None)
    except Exception:  # noqa: BLE001
        name = None
    return name if isinstance(name, str) and _PROFILE_RE.match(name) and name != 'custom' else None


def _hermes(ctx, rest, sub='valun'):
    """A command for THIS profile, as printed for the owner: `hermes -p NAME valun REST` (the default profile named too,
    so a sticky `hermes profile use` elsewhere cannot redirect it), or `hermes valun REST` in a home that is no named
    profile (_home_note says to keep HERMES_HOME)."""
    name = _profile(ctx)
    return f"hermes {'-p ' + name + ' ' if name else ''}{sub} {rest}".rstrip()


def _home_note(ctx):
    if _profile(ctx):
        return ''
    try:
        from hermes_constants import get_hermes_home
        home = str(get_hermes_home())
    except Exception:  # noqa: BLE001
        home = os.environ.get('HERMES_HOME', '')
    return f' (This Hermes home is not a named profile: run these commands with HERMES_HOME={home}.)' if home else ''


def _setting_cmd(ctx, key, value):
    """The command that sets one of this plugin's settings in this profile."""
    pid = getattr(ctx, 'plugin_id', None)
    pid = pid if isinstance(pid, str) and pid else 'valun'
    return _hermes(ctx, f'set plugins.entries.{pid}.settings.{key} {value}', sub='config')


def _terminal_text(s, limit=300):
    """Text for this terminal: one line, printable characters only, at most `limit` characters."""
    return ''.join(ch if ch.isprintable() else ' ' for ch in str(s))[:limit]


def _owner_text(ctx, s, limit=300):
    """A core message for the owner, with its `valun attempts ...` commands named as this profile's Hermes commands."""
    return _terminal_text(str(s).replace('`valun attempts', '`' + _hermes(ctx, 'attempts')), limit)


# With Hermes v0.22.7, enabling any git-installed plugin copies Hermes's runtime into <home>/installs/...; its longest
# file adds 215 characters to the home path, and Windows refuses paths over 259 characters unless long paths are enabled.
WINDOWS_HOME_LIMIT = 44


def _policy_line(policy):
    """The local consent policy in one line."""
    if not isinstance(policy, dict):
        return 'none (every consent role off)'
    on = lambda k: 'on' if policy.get(k) is True else 'off'  # noqa: E731
    cap = policy.get('max_PC')
    return (f"setoff {on('setoff')}, as_issuer {on('as_issuer')}, as_new_creditor {on('as_new_creditor')}, "
            f"max_PC {cap if cap is not None else 'none'}")


def _consents_line(ctx, store):
    if store.process_consents:
        return ('signed by your client by itself, before Valun tool calls that are not read-only, at most once a minute '
                '(process_consents on; nothing runs in the background)')
    return (f'none signed by itself (process_consents off); `{_hermes(ctx, "consents --process")}` signs them when you '
            'run it')


def _doctor(ctx):
    """Plain checks with a fix for each problem. Never prints secrets."""
    ok, problems = [], []
    try:
        from hermes_constants import get_hermes_home
        home = str(get_hermes_home())
    except Exception:  # noqa: BLE001
        home = os.environ.get('HERMES_HOME', '')
    root = home.split(os.sep + 'profiles' + os.sep)[0] if (os.sep + 'profiles' + os.sep) in home else home
    if os.name == 'nt':
        if len(root) <= WINDOWS_HOME_LIMIT:
            ok.append(f'Windows path: Hermes home {root} is {len(root)} characters (limit {WINDOWS_HOME_LIMIT})')
        else:
            problems.append(f'Windows path: Hermes home {root} is {len(root)} characters; plugins cannot be enabled above '
                            f'{WINDOWS_HOME_LIMIT} unless Windows long paths are on. Fix: use a shorter HERMES_HOME (e.g. C:\\hermes) '
                            'or enable long paths (administrator).')
    server = tools.server_url(ctx)
    pid = ctx.get_config('participant_id', default='')
    tok, sec = tools.secret_value('VALUN_AGENT_TOKEN'), tools.secret_value('VALUN_SIGNING_SECRET')
    real = lambda v: bool(v) and not str(v).startswith('pending')  # noqa: E731
    if pid and real(tok) and real(sec):
        ok.append(f'Connected profile: participant {pid}, agent token and signing key stored')
    else:
        problems.append(f'Not connected: run `{_hermes(ctx, "join --sandbox")}` (no invite: sandbox only), '
                        f'`{_hermes(ctx, "join")}` (with an invite) or `{_hermes(ctx, "enroll")}` (with an enrollment code).')
    try:
        info = vc.Client(_MemoryStore(), server).request('GET', '/pilot/v1/info', auth=False)
        ok.append(f"Server {server} reachable: {info['network']}, unit {info['unit']} (synthetic)")
    except Exception as e:  # noqa: BLE001
        problems.append(f'Server {server} not reachable ({e}). Check the server_url setting and your connection.')
    store = tools.HermesStore(ctx)
    if store.unreadable is not None:
        problems.append(f'Your Valun client cannot read its records ({_terminal_text(store.unreadable)}), so it signs nothing. '
                        f'Fix: restore {store.path or "plugin-data/valun/valun-records.json"} from a backup. Move it away only '
                        'if you know it holds nothing you need: it is your record of the credit notes and payments your agent '
                        'signed.')
    if pid and real(tok) and real(sec):
        try:
            me = vc.Client(store).me()
            m = me['mandate']
            ok.append(f"Agent accepted by the server as {me['participant']['handle']}; limits: at most {m['max_issue_per_deal']} PC "
                      f"per deal, {m['max_issue_outstanding']} PC owed in total")
            if me['participant'].get('status') != 'active':
                problems.append(f'The agent key is not active: ask your owner to issue an enrollment code, then '
                                f'`{_hermes(ctx, "enroll")}`.')
        except Exception as e:  # noqa: BLE001
            problems.append(f'The server refused this agent ({getattr(e, "code", e)}): if the owner revoked it, enroll again.')
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
    except Exception:  # noqa: BLE001
        cfg = {}
    ts = str(((cfg.get('tools') or {}).get('tool_search') or {}).get('enabled', 'auto')).lower()
    if ts in ('off', 'false'):
        ok.append('Valun tools are shown to the model directly (tools.tool_search.enabled = off)')
    else:
        problems.append('Valun tools reach the model through Hermes tool search; small models often fail there. Fix (this '
                        f'profile only): `{_hermes(ctx, "set tools.tool_search.enabled off", sub="config")}`.')
    mode = str((cfg.get('approvals') or {}).get('mode', 'manual')).lower()
    confirm = tools.confirm_commitments(ctx)
    if confirm and mode in ('off', 'false'):
        problems.append('approvals.mode is off, so Hermes will NOT ask you before commitments although confirm_commitments is '
                        f'on. Fix: `{_hermes(ctx, "set approvals.mode manual", sub="config")}`.')
    else:
        ok.append('Commitments: ' + ('Hermes asks you before each one (never run with --yolo)' if confirm
                                     else 'autonomous within your limits (confirm_commitments is off)'))
    ok.append(f'Consents: {_consents_line(ctx, store)}; policy {_policy_line(store.kept_policy)}')
    for line in ok:
        print('  ok    ' + line)
    for line in problems:
        print('  FIX   ' + line)
    print('All set.' if not problems else f'{len(problems)} thing(s) to fix.')
    return 0 if not problems else 1


def _write_new_private(path, text):
    """Write `text` into a NEW private file (O_EXCL: an existing file, maybe another participant's owner token, is never
    overwritten or truncated, and a file that exists with wider permissions never receives it)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(text)


def _hand_over(args, owner, server):
    """The owner token, to the owner, once: written to --owner-token-file (a new private file), or shown. A file that
    cannot be written does not lose it: it is shown instead, and False is returned (join exits 1 once its other steps are
    done, so that a script relying on the file notices). True otherwise."""
    path = getattr(args, 'owner_token_file', None)
    if path:
        try:
            _write_new_private(path, owner + '\n')
            print(f'Your OWNER token is in {path}. Keep it private; do not give it to your agent.')
            return True
        except OSError as e:
            print(f'Cannot write {path} ({e.strerror or e}), so your owner token is shown here instead.')
    print('Your OWNER token (shown once; store it in a password manager, never give it to your agent; it opens '
          f'{server}/pilot#owner where you can change limits or revoke this agent):')
    print('  ' + owner)
    return not path


def _whole(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _set_mandate(args, ctx, c, owner, pid, server):
    """The owner token, used here once, in memory, and never stored in this profile: where approvals happen (approve_above,
    and for console also review_deliveries). Returns the owner mandate as GET /owner showed it, or None after saying what
    is not set and how to finish."""
    try:
        got = c.request('GET', '/pilot/v1/owner', token=owner)
        p = got.get('participant') if isinstance(got, dict) else None
        if not (isinstance(p, dict) and p.get('id') == pid):
            raise vc.ValunError('not_owner', 'GET /owner did not name the participant just created; nothing was changed')
        limits = got.get('mandate')
        if not isinstance(limits, dict):
            raise vc.ValunError('bad_response', 'GET /owner did not answer the owner mandate')
        cap = max([v for v in (limits.get('max_issue_per_deal'), limits.get('max_accept_per_deal')) if _whole(v)] + [0])
        body = {'approve_above': 0, 'review_deliveries': True} if args.approvals == 'console' else {'approve_above': cap}
        c.request('PUT', '/pilot/v1/owner/mandate', body, token=owner)
        return limits
    except vc.ValunError as e:
        print(f'Your owner mandate could not be set ({e.code}): {_terminal_text(e)}')
        if args.approvals == 'console':
            print('  Approvals: commitments are not held in the owner console yet, so Hermes asks you before each one '
                  '(confirm_commitments stays on). To finish: in the owner console '
                  f'({server}/pilot#owner), with your owner token, set approve_above to 0 and turn review_deliveries on; '
                  f'then run `{_setting_cmd(ctx, "confirm_commitments", "false")}`.')
        else:
            print(f'  Approvals: {args.approvals}; approve_above in your owner mandate was not set, so the owner console may '
                  f'hold some commitments for you until you set it there ({server}/pilot#owner).')
        print(f'Then run `{_hermes(ctx, "doctor")}`.')
        return None


def _join(args, ctx):
    server = (args.server or ctx.get_config('server_url', default=tools.DEFAULT_SERVER) or tools.DEFAULT_SERVER).rstrip('/')
    if ctx.get_config('participant_id', default=''):
        print('This profile is already connected to a Valun participant. Use a new Hermes profile for a second identity.')
        return 1
    sandbox = getattr(args, 'sandbox', False)
    invite = None if sandbox else (sys.stdin.readline() if args.invite_from_stdin else getpass.getpass('Invite code (vpi_...; not shown): ')).strip()
    if not args.handle:
        args.handle = input('Handle (public, permanent; 3-24 letters, digits, _ or -, starting with a letter): ').strip().lower()
    if not args.operator:
        args.operator = input('Operator (public: who controls this agent, e.g. your name): ').strip()
    store = _MemoryStore()
    c = vc.Client(store, server)
    try:
        if sandbox:   # no invite: a sandbox-only participant (only where the server offers it)
            j = c.join_sandbox(args.handle, args.operator, display_name=args.display_name, about=args.about)
        else:
            j = c.join(invite, args.handle, args.operator, display_name=args.display_name, about=args.about, services=args.services)
    except vc.ValunError as e:
        print(f'Joining failed ({e.code}): {e}')
        return 1
    owner, pid = j['owner_token'], j['participant_id']
    # From here on the participant exists, and its owner token is in this answer only: it is handed over right after
    # enrolment, before anything else can fail, and every later step that fails says how to finish it.
    try:
        r = c.enroll(pid, j['enrollment_code'])
    except vc.ValunError as e:
        print(f"Your participant {pid} was created, but this agent's key could not be registered ({e.code}): {_terminal_text(e)}")
        _hand_over(args, owner, server)
        # Nothing is stored in this profile yet (no key), so the command names the server and the participant too.
        print(f'To finish: issue an enrollment code in the owner console ({server}/pilot#owner) with your owner token, then '
              f'run `{_hermes(ctx, f"enroll --server {server} --participant {pid}")}`.' + _home_note(ctx))
        return 1
    handed = _hand_over(args, owner, server)
    finished = handed
    stored = _hermes_config_set('VALUN_SIGNING_SECRET', store.secret()) and _hermes_config_set('VALUN_AGENT_TOKEN', store.token())
    ctx.set_config('server_url', server)
    ctx.set_config('participant_id', pid)
    # Commitments: Hermes asks before each one ('conversation'), or not at all ('none'). 'console' keeps Hermes asking
    # until the mandate step below holds every commitment for the owner in the console.
    ctx.set_config('confirm_commitments', args.approvals != 'none')
    # The local consent policy (design 6.6, decision D-2 g): a sandbox participant's client signs set-offs, transfers of
    # its own debt and payments to it by transfer by itself (process_consents on); an invited participant's policy signs
    # set-offs and transfers of its own debt, and only once its owner turns process_consents on (off by default).
    policy = va.SANDBOX_CONSENT_POLICY if sandbox else va.INVITE_CONSENT_POLICY
    for role in tools.CONSENT_ROLES:
        ctx.set_config(f'consents.{role}', policy[role] is True)
    if sandbox:
        ctx.set_config('process_consents', True)
    if args.direct_tools:
        _hermes_config_set('tools.tool_search.enabled', 'off')
    print(f"Joined Valun as {args.handle} ({pid}) on {server}. This agent's key was generated here; public key {r['pubkey'][:16]}...")
    if stored:
        print('The agent token and signing key are stored in this Hermes profile (not shown).')
    else:
        finished = False
        print('Hermes could not store the agent credentials. Revoke this agent in the owner console '
              f'({server}/pilot#owner) with your owner token, issue an enrollment code there, then run '
              f'`{_hermes(ctx, "enroll")}`.')
    if j.get('sandbox_only'):
        print(j.get('note') or 'Sandbox only: you can deal with the sandbox agents (sandbox-exact, sandbox-mallory) for a week.')
    try:
        c.me()            # the agent's first call: it shows as active in the directory from now on
    except vc.ValunError:
        pass
    # The pin read: the network's tracker and unit are pinned in this profile's records now (a later change is refused),
    # and GET /info says whether the server offers circulating credit.
    circulating = False
    try:
        circulating = va.CIRCULATION in va.features_of(c.info())
    except vc.ValunError as e:
        print(f'GET /info could not be read ({e.code}): the tracker and unit are pinned on the first call instead.')
    pins = store.state.get('pins')
    if isinstance(pins, dict):
        try:   # this participant is new on this server: its tracker and unit are the ones to pin
            tools.HermesStore(ctx).update(lambda state: state.__setitem__('pins', dict(pins)))
        except Exception as e:  # noqa: BLE001 - not fatal: the client pins them on its first call
            print(f'Your client could not record the tracker it is pinned to ({getattr(e, "code", type(e).__name__)}); it '
                  'pins it on its first call instead.')
    limits = _set_mandate(args, ctx, c, owner, pid, server)
    if limits is None:
        finished = False
    else:
        if args.approvals == 'console':
            ctx.set_config('confirm_commitments', False)
        print({'conversation': 'Approvals: before each offer, acceptance, credit note or payment with a note you hold, Hermes '
                               'asks you in the conversation.',
               'console': f'Approvals: commitments wait for you in the owner console ({server}/pilot#owner), including '
                          'accepting a delivery (its credit note or payment is made only after you approve it).',
               'none': 'Approvals: none. Your agent acts on its own within your limits '
                       f"(at most {limits.get('max_issue_per_deal')} PC per deal, {limits.get('max_issue_outstanding')} PC owed in total)."}[args.approvals])
    if circulating:
        if sandbox:
            print('Consents: your Valun client signs, by itself, set-offs that leave your net position unchanged, transfers of '
                  'your own debt and payments to you by transfer from the sandbox agents (up to '
                  f"{policy['max_PC']} PC each), before Valun tool calls that are not read-only (process_consents on; "
                  f'nothing runs in the background). Turn it off: `{_setting_cmd(ctx, "process_consents", "false")}`.')
        else:
            print('Consents: your Valun client signs none by itself (process_consents off). Once you turn it on '
                  f'(`{_setting_cmd(ctx, "process_consents", "true")}`) it signs set-offs that leave your net position '
                  'unchanged and transfers of your own debt; payments to you by transfer stay off (consents.as_new_creditor). '
                  f'`{_hermes(ctx, "consents")}` shows what waits.')
    print(f'Next: `{_hermes(ctx, "doctor")}`, then talk to your agent: `{_hermes(ctx, "-t valun", sub="chat")}`.' + _home_note(ctx))
    if not handed:
        print(f'Exit code 1: {args.owner_token_file} was not written (your owner token is shown above instead).')
    return 0 if finished else 1


def _status(ctx):
    tok, sec = bool(tools.secret_value('VALUN_AGENT_TOKEN')), bool(tools.secret_value('VALUN_SIGNING_SECRET'))
    store = tools.HermesStore(ctx)
    print(f"server:          {ctx.get_config('server_url', default=tools.DEFAULT_SERVER)}")
    print(f"participant:     {ctx.get_config('participant_id', default='') or '(not enrolled)'}")
    print(f"agent token set: {'yes' if tok else 'no'}")
    print(f"signing key set: {'yes' if sec else 'no'}")
    print(f"confirm commitments in Hermes: {tools.confirm_commitments(ctx)}")
    print(f"consent policy:  {_policy_line(store.kept_policy)}")
    print(f"consents:        {_consents_line(ctx, store)}")
    if store.unreadable is not None:
        print(f'records:         UNREADABLE ({_terminal_text(store.unreadable)}); your client signs nothing until it is fixed '
              f'(`{_hermes(ctx, "doctor")}`)')
    return 0


def _connected(ctx):
    """(store, client) for this profile's agent, or (store, None) after saying how to join."""
    store = tools.HermesStore(ctx)
    if not (ctx.get_config('participant_id', default='') and store.secret() and store.token()):
        print(f'This Hermes profile is not connected to Valun yet; run `{_hermes(ctx, "join --sandbox")}` (no invite) or '
              f'`{_hermes(ctx, "join")}` first.')
        return store, None
    if store.unreadable is not None:
        print(f'Your Valun client cannot read its records ({_terminal_text(store.unreadable)}); nothing was changed. '
              f'See `{_hermes(ctx, "doctor")}`.')
        return store, None
    return store, vc.Client(store)


def _mandate_of(me):
    return next((me[k] for k in ('effective_limits', 'mandate') if isinstance(me, dict) and isinstance(me.get(k), dict)), None)


_ISO = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z\Z')


def _consent_line(item):
    """One consent item of GET /consents, from checked values only (the server wrote it)."""
    kind = item.get('kind') if item.get('kind') in ('transfer', 'setoff') else '?'
    roles = [r for r in item.get('your_roles') or [] if r in ('payer', 'issuer', 'payee', 'member')] \
        if isinstance(item.get('your_roles'), list) else []
    ctxt = item.get('context') if isinstance(item.get('context'), dict) else {}
    summ = item.get('summary') if isinstance(item.get('summary'), dict) else {}
    out = f"{kind} {nv.shown_id(item.get('id')) or '?'} as {', '.join(roles) or '?'}"
    if _whole(ctxt.get('amount')):
        out += f", {ctxt['amount']} PC"
    if _whole(summ.get('your_net_change_PC')):
        out += f" (your net change {summ['your_net_change_PC']} PC)"
    if isinstance(item.get('expires_at'), str) and _ISO.match(item['expires_at']):
        out += f", open until {item['expires_at']}"
    return out


def _decisions(report):
    """process_consents() decisions, one line each (its entries hold checked values only)."""
    lines = []
    for bucket in ('signed', 'refused', 'waiting'):
        for e in report.get(bucket) or []:
            what = f"{e.get('kind')} {e.get('id')} as {e.get('role')}"
            if e.get('amount') is not None:
                what += f", {e.get('amount')} PC (your net change {e.get('your_net_change_PC')} PC)"
            why = e.get('code') or ''
            lines.append(f'  {bucket:8} {what}' + (f': {why}' if why and bucket != 'signed' else ''))
    return lines


def _consents(args, ctx):
    """hermes valun consents [--process]: the consents waiting for this profile's client, and, with --process (the
    owner, at this terminal), signing what the consent policy and the owner mandate allow, and refusing the others."""
    store, c = _connected(ctx)
    if c is None:
        return 1
    try:
        features = va.features_of(c.info())
    except vc.ValunError as e:
        print(f'Cannot read the server ({e.code}): {_terminal_text(e)}')
        return 1
    if va.CIRCULATION not in features:
        print('This Valun server does not offer circulating credit (circulation/1): there is nothing to consent to.')
        return 0
    try:
        me = c.me()
        data = c.consents()
    except vc.ValunError as e:
        print(f'Cannot read your consents ({e.code}): {_terminal_text(e)}')
        return 1
    policy = store.kept_policy if isinstance(store.kept_policy, dict) else None
    signs = va.client_signs(va.consent_policy_in_force(policy, _mandate_of(me)))
    print(f'Your consent policy (this profile): {_policy_line(policy)}; {_consents_line(ctx, store)}.')
    print('With your owner mandate, it signs: ' + ('; '.join(signs) if signs else
                                                    'nothing (every consent role is off in your policy or your owner mandate)'))
    items = data.get('consents') if isinstance(data, dict) else None
    waiting = [i for i in (items if isinstance(items, list) else []) if isinstance(i, dict) and i.get('state') == 'collecting']
    print(f'Consents waiting for your client: {len(waiting)}')
    for i in waiting:
        print('  ' + _consent_line(i))
    if not getattr(args, 'valun_process', False):
        if waiting:
            print(f'`{_hermes(ctx, "consents --process")}` signs those your policy allows and refuses the others.')
        return 0
    try:
        r = c.process_consents(policy=policy if policy is not None else {})
    except vc.ValunError as e:
        print(f'Could not process them ({e.code}): {_owner_text(ctx, e)}')
        return 1
    lines = _decisions(r)
    print('\n'.join(lines) if lines else 'Nothing waits for your client.')
    return 0


ATTEMPT_STATES = {'applied': 'applied: the deal is paid by it',
                  'unknown': 'MAY HAVE APPLIED: your client pays this deal no more until you resolve it',
                  'alive': 'can still apply',
                  'dead': 'did not apply (and can no longer)',
                  'unread': 'not known yet: its edge has not been read since its window closed'}
# An 'unknown' attempt of a deal recorded as paid: the deal is never paid again, so it blocks nothing and there is
# nothing for the owner to resolve (decision D-2 a: a raise of an incoming edge needs no signature of this client, so
# its own records cannot prove such an attempt dead; the server proves it from its journal).
UNKNOWN_BUT_PAID = "cannot be proven dead from your client's records, but the deal is {paid}: nothing to resolve"
# Why a resolution changes only your client's records (Client.resolution_plan local).
LOCAL_WHY = {
    'note': ("The server holds no payment attempt for a credit note: only your client's records change (your owner token is "
             'checked first, used once and not stored).'),
    'no_circulation': ("This server offers no circulating credit, so only your client's records change (your owner token is "
                       'checked first, used once and not stored).'),
    'no_server_id': ("Your client did not record the server's id of this transfer (its request got no answer, or the server "
                     'refused it). If the owner console shows a transfer of this deal (vt_...), stop here and run this again '
                     'with that id, so that the server records your decision too.'),
    'server_unknown': 'The server shows no such transfer of yours as this attempt (it may have refused it when it was posted).',
    'server_ended': ('The server holds this transfer as {state}: it ended there, so the server has nothing to resolve; your '
                     'client still counts it as a payment that may have applied.'),
}


def _who_key(c, pk):
    return 'you' if pk == c.pubkey else f'{str(pk)[:16]}...'


def _attempt_line(c, r):
    """One payment attempt, in plain words."""
    i, rcp = (str(r['edge']).split('>') + [''])[:2]
    when = datetime.fromtimestamp(r['timestamp'] / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    prior = r['prior'] if r['prior'] is not None else '?'
    if r['route'] == 'note':
        what = f"your credit note to {_who_key(c, rcp)}: your debt {prior} -> {r['total']} PC"
    else:
        what = f"paying with the note of {_who_key(c, i)}: your claim {prior} -> {r['total']} PC"
    sid = f", server id {r['attempt_id']}" if r.get('attempt_id') else ''
    decided = (f"; you resolved it: {r['outcome']}" if r.get('outcome') in vc.ATTEMPT_OUTCOMES and r.get('outcome') != 'applied'
               else '')
    if r['state'] == 'unknown' and r.get('paid'):
        state = UNKNOWN_BUT_PAID.format(paid=f"paid by {r['paid_by']}" if r.get('paid_by') else 'recorded as paid')
    else:
        state = ATTEMPT_STATES.get(r['state'], r['state'])
    return f"  {r['ref']} {what}, signed for {when}{sid}: {state}{decided}"


def _edges_now(c):
    """A read-back of this profile's edges, each state checked by its client (sync_edges: {'edges', 'doubtful'}), where the
    server offers bound notes or circulating credit; {} when there is none to read or it cannot be read now."""
    try:
        features = va.features_of(c.info())
    except vc.ValunError as e:
        print(f'Cannot read the server ({e.code}); going by what your records hold.')
        return {}
    if 'bound_notes/1' not in features and va.CIRCULATION not in features:
        return {}
    try:
        read = c.sync_edges()
    except vc.ValunError as e:
        print(f'Cannot read your edges ({e.code}); going by what your records hold.')
        return {}
    return {'edges': read['edges'], 'doubtful': read['doubtful']}


def _ask_local():
    print("  Recording it in your client's records only changes what your client does: if the server does hold this payment "
          'as unresolved, it keeps blocking the deal there.')
    return input("Record your decision in your client's records only? [y/N] ").strip().lower() in ('y', 'yes')


def _owner_token(path):
    """The owner token from `path` or a hidden prompt (None, after saying why, when there is none). Used once, in memory."""
    try:
        if path:
            with open(path, encoding='utf-8') as f:
                owner = f.read().strip()
        else:
            owner = getpass.getpass('Owner token (not shown): ').strip()
    except OSError as e:
        print(f'Cannot read the owner token file: {e}')
        return None
    if not owner:
        print('No owner token given; nothing was changed.')
        return None
    return owner


def _attempts(args, ctx):
    """hermes valun attempts list | resolve: the payment attempts this profile's client records, and the OWNER's
    resolution of one whose outcome is unknown (decision D-2 d), as `valun attempts` does. resolve runs only at a
    terminal, asks for confirmation (twice when a transfer is recorded in the client's records only), and uses the owner
    token once, in memory (from --owner-token-file or a hidden prompt); it is never something an agent does. Exit codes:
    0 done, 1 error, 3 declined or no person at the terminal."""
    action = getattr(args, 'valun_attempts', None)
    if action not in ('list', 'resolve'):
        print(f'usage: {_hermes(ctx, "attempts list")} | {_hermes(ctx, "attempts resolve DEAL ATTEMPT --not-applied|--applied")}')
        return 1
    store, c = _connected(ctx)
    if c is None:
        return 1
    if action == 'list':
        read = _edges_now(c)
        try:
            rows = c.payment_attempts(**read)
        except vc.ValunError as e:
            print(f'Cannot read your records ({e.code}): {_owner_text(ctx, e)}')
            return 1
        if not rows:
            print('Your Valun client records no payment attempts.')
            return 0
        print('Payment attempts your Valun client records (how it judges each now, as it does before it pays, from its '
              'records' + (' and the tracker states it just checked):' if read else '):'))
        for deal_id in dict.fromkeys(r['deal_id'] for r in rows):
            mine = [r for r in rows if r['deal_id'] == deal_id]
            print(f"deal {_terminal_text(deal_id, 40)}" + (': recorded as paid' if mine[0]['paid'] else ''))
            for r in mine:
                print(_attempt_line(c, r))
        if any(r['state'] == 'unknown' and not r['paid'] for r in rows):
            print('Resolve one only after asking the payee whether the payment arrived: '
                  f'`{_hermes(ctx, "attempts resolve DEAL ATTEMPT --applied")}` (it arrived) or `--not-applied` (it did not). '
                  "ATTEMPT is #n, or the server's id of a transfer (vt_...).")
        return 0
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(f'`{_hermes(ctx, "attempts resolve")}` asks you, the owner, to confirm at this terminal, and no person is at it. '
              'Run it in a terminal.')
        return 3
    deal, attempt, note = args.valun_deal, args.valun_attempt, args.valun_note
    outcome = 'applied' if args.valun_applied else 'not_applied'
    try:
        vc._owner_note(note)               # a note the server would refuse is refused before anything is asked
    except vc.ValunError as e:
        print(_terminal_text(e))
        return 1
    read = _edges_now(c)
    try:
        plan = c.resolution_plan(deal, attempt, **read)
    except vc.ValunError as e:
        print(f'{e.code}: {_owner_text(ctx, e)}')
        return 1
    print(f'Valun - RESOLVE payment attempt {_terminal_text(attempt, 40)} of deal {_terminal_text(deal, 40)} as '
          + ('APPLIED: the deal counts as paid by it, and your client never pays it again.' if outcome == 'applied' else
             'NOT APPLIED: once its window has passed (its time + 1 h + 2 min), your client may pay this deal again, so a '
             'wrong answer can pay it twice.'))
    print('  Do this only after the payee told you whether this payment arrived.')
    if plan['server']:
        print("  The server is told first (with your owner token, used once and not stored); then your client's records.")
    else:
        print('  ' + LOCAL_WHY[plan['local']].format(state=(plan.get('transfer') or {}).get('state') or 'ended'))
    for r in plan['attempts']:
        print(_attempt_line(c, r) + ('   <- this one' if r is plan['attempt'] else ''))
    if plan['attempt'] is None:
        print("  (your client's records hold no attempt that this id names)")
    if plan['attempts'] and plan['attempts'][0]['paid']:
        by = plan['attempts'][0].get('paid_by')
        print('  This deal is recorded as paid' + (f' by {by}' if by else '') + ': your client never pays it again, whatever '
              'you decide here.')
    if input('Resolve it? [y/N] ').strip().lower() not in ('y', 'yes'):
        print('Nothing was changed.')
        return 3
    local_only = plan['local'] in vc.LOCAL_CHOICES
    if local_only and not _ask_local():
        print('Nothing was changed.')
        return 3
    owner = _owner_token(args.valun_owner_token_file)
    if owner is None:
        return 1
    # resolve_attempt plans again: it resolves only the attempt shown above ('<- this one'), or refuses with
    # attempt_changed (nothing changed) when another process changed the deal's attempts since.
    confirmed = vc.attempt_key(plan['attempt'])
    try:
        r = c.resolve_attempt(deal, attempt, outcome, owner, note=note, local_only=local_only, expected_attempt=confirmed, **read)
    except vc.ValunError as e:
        if not (e.extra or {}).get('local_possible') or local_only:
            print(f'{e.code}: {_owner_text(ctx, e)}')
            return 1
        srv = (e.extra or {}).get('server') or {}
        print(f"The server did not resolve it ({_terminal_text(srv.get('error') or e.code, 40)}): "
              f"{_terminal_text(srv.get('message') or e)}")
        if not _ask_local():
            print('Nothing was changed.')
            return 3
        try:
            r = c.resolve_attempt(deal, attempt, outcome, owner, note=note, local_only=True, expected_attempt=confirmed, **read)
        except vc.ValunError as e2:
            print(f'{e2.code}: {_owner_text(ctx, e2)}')
            return 1
    print(('The server recorded your decision. ' if r['server'] is not None else '')
          + (f"Your client's records now hold it for {len(r['matched'])} attempt(s) of this deal"
             if r['matched'] else "Your client's records hold no attempt that matches it")
          + ('; the deal is recorded as paid.' if r['paid'] else '.'))
    return 0


def _run_cli(args, ctx):
    cmd = getattr(args, 'valun_command', None)
    if any(k.startswith('HERMES_SESSION_') for k in os.environ):
        print('Refusing: run `hermes valun ...` yourself in a terminal, not from inside an agent session.')
        return 1
    if cmd == 'status':
        return _status(ctx)
    if cmd == 'join':
        return _join(args, ctx)
    if cmd == 'doctor':
        return _doctor(ctx)
    if cmd == 'consents':
        return _consents(args, ctx)
    if cmd == 'attempts':
        return _attempts(args, ctx)
    if cmd != 'enroll':
        print('usage: hermes valun join --handle H --operator NAME | hermes valun enroll [--participant vp_...] | '
              'hermes valun status | hermes valun doctor | hermes valun consents [--process] | '
              'hermes valun attempts list|resolve')
        return 1
    server = (args.server or ctx.get_config('server_url', default=tools.DEFAULT_SERVER) or tools.DEFAULT_SERVER).rstrip('/')
    participant = (args.participant or input('Participant id (vp_..., from your owner console): ')).strip()
    code = (sys.stdin.readline() if getattr(args, 'code_from_stdin', False)
            else getpass.getpass('Enrollment code (from your owner console; not shown): ')).strip()
    store = _MemoryStore()
    try:
        r = vc.Client(store, server).enroll(participant, code)
    except vc.ValunError as e:
        print(f'Enrollment failed ({e.code}): {e}')
        return 1
    ok = _hermes_config_set('VALUN_SIGNING_SECRET', store.secret()) and _hermes_config_set('VALUN_AGENT_TOKEN', store.token())
    if not ok:
        print('Enrolled on the server, but Hermes could not store the credentials. Nothing was printed. '
              'Ask your owner to revoke this agent in the owner console and try again.')
        return 1
    ctx.set_config('server_url', server)
    ctx.set_config('participant_id', participant)
    print(f"Enrolled {participant} on {r['network']}. This agent's public key: {r['pubkey'][:16]}...")
    print('The signing key and agent token are stored in this Hermes profile\'s secrets (not shown). '
          'Start a new Hermes session to use the Valun tools.')
    return 0
