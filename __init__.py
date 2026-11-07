"""Valun plugin for Hermes Agent.

Registers nine tools, a pre_tool_call hook that routes commitments (and loosening a recorded limit) through Hermes's
own approval gate (when `confirm_commitments` is on), and the operator command `hermes valun enroll|status`.
"""
import getpass
import logging
import os
import secrets
import subprocess
import sys

from . import schemas, tools, valun_client as vc

logger = logging.getLogger(__name__)

TOOLSET = 'valun'
COMMITMENT_TOOLS = ('valun_propose', 'valun_respond', 'valun_review_delivery', 'valun_set_limits')


def _is_commitment(tool_name, args):
    if tool_name == 'valun_propose':
        return True
    # Fail closed: only the explicitly harmless actions skip the owner (a misspelt "Accept" must never skip them).
    if tool_name == 'valun_respond':
        return (args or {}).get('action') not in ('decline', 'withdraw')
    if tool_name == 'valun_review_delivery':
        return (args or {}).get('decision') != 'dispute'
    return False


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
        # Escalate commitments to Hermes's human-approval gate. Never auto-approves; under cron or other unattended
        # runs Hermes decides (by default it denies, so nothing is signed without a person).
        if tool_name not in COMMITMENT_TOOLS:
            return None
        if tool_name == 'valun_set_limits':
            if not tools.limits_loosen(ctx, args):   # recording or tightening limits needs no approval; loosening does
                return None
        elif not _is_commitment(tool_name, args):
            return None
        if not ctx.get_config('confirm_commitments', default=True):
            return None
        return {'action': 'approve', 'message': tools.describe_commitment(ctx, tool_name, args),
                'rule_key': f'valun:{tool_name}:{secrets.token_hex(8)}'}  # unique: a saved "always" never matches a later commitment

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
    subs.add_parser('status', help='show the Valun connection of this profile (never shows secrets)')
    subs.add_parser('doctor', help='check this profile for everything Valun needs, and say how to fix what is missing')
    j = subs.add_parser('join', help='join the Valun pilot with an invite and connect this agent, in one step')
    j.add_argument('--handle', help='your public handle (3-24 letters, digits, _ or -); asked if omitted')
    j.add_argument('--operator', help='who controls this agent, shown publicly (your name or organisation); asked if omitted')
    j.add_argument('--display-name', default='', help='optional public display name')
    j.add_argument('--about', default='', help='optional public description of you or your agent')
    j.add_argument('--services', default='', help='optional public list of what your agent offers')
    j.add_argument('--server', help='Valun server URL (default: configured server_url)')
    j.add_argument('--invite-from-stdin', action='store_true', help='read the invite code from stdin instead of a hidden prompt')
    j.add_argument('--owner-token-file', help='write your owner token to this file (created private) instead of showing it once')
    j.add_argument('--approvals', choices=['conversation', 'console', 'none'], default='conversation',
                   help="where commitments are approved: 'conversation' (Hermes asks you before each one; default), "
                        "'console' (they wait in the web owner console), or 'none' (autonomous within your limits)")
    j.add_argument('--direct-tools', action='store_true',
                   help='expose the Valun tools to the model directly (sets tools.tool_search.enabled=off in THIS profile); '
                        'recommended for a dedicated Valun profile and for small models')


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


# With Hermes v0.22.7, enabling any git-installed plugin copies Hermes's runtime into <home>/installs/...; its longest
# file adds 215 characters to the home path, and Windows refuses paths over 259 characters unless long paths are enabled.
WINDOWS_HOME_LIMIT = 44


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
    server = (ctx.get_config('server_url', default=tools.DEFAULT_SERVER) or tools.DEFAULT_SERVER).rstrip('/')
    pid = ctx.get_config('participant_id', default='')
    try:
        from agent.secret_scope import get_secret
        tok, sec = get_secret('VALUN_AGENT_TOKEN'), get_secret('VALUN_SIGNING_SECRET')
    except Exception:  # noqa: BLE001
        tok = sec = None
    real = lambda v: bool(v) and not str(v).startswith('pending')
    if pid and real(tok) and real(sec):
        ok.append(f'Connected profile: participant {pid}, agent token and signing key stored')
    else:
        problems.append('Not connected: run `hermes valun join` (with an invite) or `hermes valun enroll` (with an enrollment code).')
    try:
        info = vc.Client(_MemoryStore(), server).request('GET', '/pilot/v1/info', auth=False)
        ok.append(f"Server {server} reachable: {info['network']}, unit {info['unit']} (synthetic)")
    except Exception as e:  # noqa: BLE001
        problems.append(f'Server {server} not reachable ({e}). Check the server_url setting and your connection.')
    if pid and real(tok) and real(sec):
        try:
            me = vc.Client(tools.HermesStore(ctx)).me()
            m = me['mandate']
            ok.append(f"Agent accepted by the server as {me['participant']['handle']}; limits: at most {m['max_issue_per_deal']} PC "
                      f"per deal, {m['max_issue_outstanding']} PC owed in total")
            if me['participant'].get('status') != 'active':
                problems.append('The agent key is not active: ask your owner to issue an enrollment code, then `hermes valun enroll`.')
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
                        'profile only): `hermes config set tools.tool_search.enabled off`.')
    mode = str((cfg.get('approvals') or {}).get('mode', 'manual')).lower()
    confirm = ctx.get_config('confirm_commitments', default=True)
    if confirm and mode in ('off', 'false'):
        problems.append('approvals.mode is off, so Hermes will NOT ask you before commitments although confirm_commitments is '
                        'on. Fix: `hermes config set approvals.mode manual`.')
    else:
        ok.append('Commitments: ' + ('Hermes asks you before each one (never run with --yolo)' if confirm
                                     else 'autonomous within your limits (confirm_commitments is off)'))
    for line in ok:
        print('  ok    ' + line)
    for line in problems:
        print('  FIX   ' + line)
    print('All set.' if not problems else f'{len(problems)} thing(s) to fix.')
    return 0 if not problems else 1


def _join(args, ctx):
    server = (args.server or ctx.get_config('server_url', default=tools.DEFAULT_SERVER) or tools.DEFAULT_SERVER).rstrip('/')
    if ctx.get_config('participant_id', default=''):
        print('This profile is already connected to a Valun participant. Use a new Hermes profile for a second identity.')
        return 1
    invite = (sys.stdin.readline() if args.invite_from_stdin else getpass.getpass('Invite code (vpi_...; not shown): ')).strip()
    if not args.handle:
        args.handle = input('Handle (public, permanent; 3-24 letters, digits, _ or -, starting with a letter): ').strip().lower()
    if not args.operator:
        args.operator = input('Operator (public: who controls this agent, e.g. your name): ').strip()
    store = _MemoryStore()
    c = vc.Client(store, server)
    try:
        j = c.join(invite, args.handle, args.operator, display_name=args.display_name, about=args.about, services=args.services)
        r = c.enroll(j['participant_id'], j['enrollment_code'])
    except vc.ValunError as e:
        print(f'Joining failed ({e.code}): {e}')
        return 1
    owner = j['owner_token']
    if not (_hermes_config_set('VALUN_SIGNING_SECRET', store.secret()) and _hermes_config_set('VALUN_AGENT_TOKEN', store.token())):
        print('Joined and enrolled, but Hermes could not store the agent credentials. Revoke this agent in the owner console '
              f'({server}/pilot#owner) with your owner token, then run `hermes valun enroll`.')
    ctx.set_config('server_url', server)
    ctx.set_config('participant_id', j['participant_id'])
    try:
        c.me()            # the agent's first call: it shows as active in the directory from now on
    except vc.ValunError:
        pass
    # Approval mode: the owner token is used here once, in memory, and never stored in this profile.
    limits = c.request('GET', '/pilot/v1/owner', token=owner)['mandate']
    cap = max(limits.get('max_issue_per_deal', 0), limits.get('max_accept_per_deal', 0))
    c.request('PUT', '/pilot/v1/owner/mandate', {'approve_above': 0 if args.approvals == 'console' else cap}, token=owner)
    ctx.set_config('confirm_commitments', args.approvals == 'conversation')
    if args.direct_tools:
        _hermes_config_set('tools.tool_search.enabled', 'off')
    print(f"Joined Valun as {args.handle} ({j['participant_id']}). This agent's key was generated here; public key {r['pubkey'][:16]}...")
    print('The agent token and signing key are stored in this Hermes profile (not shown).')
    print({'conversation': 'Approvals: before each offer, acceptance or credit note, Hermes asks you in the conversation.',
           'console': f'Approvals: commitments wait for you in the owner console ({server}/pilot#owner).',
           'none': 'Approvals: none. Your agent acts on its own within your limits '
                   f"(at most {limits.get('max_issue_per_deal')} PC per deal, {limits.get('max_issue_outstanding')} PC owed in total)."}[args.approvals])
    if args.owner_token_file:
        fd = os.open(args.owner_token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(owner + '\n')
        print(f'Your OWNER token is in {args.owner_token_file}. Keep it private; do not give it to your agent.')
    else:
        print('Your OWNER token (shown once; store it in a password manager, never give it to your agent; it opens '
              f'{server}/pilot#owner where you can change limits or revoke this agent):')
        print('  ' + owner)
    print('Start talking to your agent: hermes chat -t valun   (add -p <profile> if you use a named profile)')
    return 0


def _run_cli(args, ctx):
    cmd = getattr(args, 'valun_command', None)
    if any(k.startswith('HERMES_SESSION_') for k in os.environ):
        print('Refusing: run `hermes valun ...` yourself in a terminal, not from inside an agent session.')
        return 1
    if cmd == 'status':
        try:
            from agent.secret_scope import get_secret
            tok, sec = bool(get_secret('VALUN_AGENT_TOKEN')), bool(get_secret('VALUN_SIGNING_SECRET'))
        except Exception:  # noqa: BLE001
            tok = sec = False
        print(f"server:          {ctx.get_config('server_url', default=tools.DEFAULT_SERVER)}")
        print(f"participant:     {ctx.get_config('participant_id', default='') or '(not enrolled)'}")
        print(f"agent token set: {'yes' if tok else 'no'}")
        print(f"signing key set: {'yes' if sec else 'no'}")
        print(f"confirm commitments in Hermes: {ctx.get_config('confirm_commitments', default=True)}")
        return 0
    if cmd == 'join':
        return _join(args, ctx)
    if cmd == 'doctor':
        return _doctor(ctx)
    if cmd != 'enroll':
        print('usage: hermes valun join --handle H --operator NAME | hermes valun enroll [--participant vp_...] | hermes valun status')
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
