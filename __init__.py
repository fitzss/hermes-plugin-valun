"""Valun plugin for Hermes Agent.

Registers nine tools; a pre_tool_call hook that, while `confirm_commitments` is on, sends every Valun commitment (as
the shared core classifies it, failing closed) through Hermes's own approval gate with the text the core prepared from
one snapshot of what would be signed, and blocks the call when that snapshot cannot be read; and the operator command
`hermes valun join|enroll|status|doctor|consents|attempts`, run by a person in a terminal.
"""
import errno
import getpass
import json
import logging
import os
import re
import secrets
import stat
import subprocess
import sys
from datetime import datetime, timezone

from . import tools
from .valun_core import agent as va, client as vc, home as vh, novation as nv, schemas

logger = logging.getLogger(__name__)

TOOLSET = 'valun'


def _gate(ctx, tool_name, args):
    """pre_tool_call. For a Valun tool the core classifies as a commitment (ValunAgent.is_commitment: every tool and
    argument it does not know counts as one), while confirm_commitments is on: the core's prepare() binds the call and
    writes its approval text from one snapshot of what it would sign; the binding is remembered for the handler, and
    Hermes's own approval gate asks the owner with that text (a random rule_key, so a saved "always" never matches a
    later commitment). When preparing fails, the call is blocked: nothing is asked and nothing is signed. That includes
    a commitment whose arguments the core refuses (fix inbox #58: not a JSON object, or without one the tool requires):
    prepare_or_refusal checks them first, with the core's own check, and the block gives its bad_arguments, as the
    core's adapters answer it instead of asking. Never auto-approves; under cron and other unattended runs Hermes
    decides (by default it denies)."""
    if not (isinstance(tool_name, str) and tool_name.startswith('valun_')):
        return None                                   # not a Valun tool
    if not tools.confirm_commitments(ctx):
        return None                                   # off: the agent acts within the owner's limits without asking
    a = tools.call_arguments(args)                    # as given: the core refuses what is not an object (#58)
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
        return {'action': 'block', 'message': tools.block_text(refusal, tools.argument_refusal(tool_name, a, tools.unit(ctx)))}
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

    # In the words of this profile's network (the testnet's nanoERG, not the pilot's PC; valun 0.5.2 schemas.for_network).
    for schema in schemas.for_network(tools.network(ctx)):
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
    pf = subs.add_parser('profile', help='show or change your public profile: what your agent offers (--services), what it '
                                         'pays others for (--wants), --about, --display-name, and --public-vouches on|off')
    pf.add_argument('--services', dest='valun_services', help='what your agent offers, public')
    pf.add_argument('--wants', dest='valun_wants', help='the work your agent pays other agents for, public ("" clears it)')
    pf.add_argument('--about', dest='valun_about', help='a public description of you or your agent')
    pf.add_argument('--display-name', dest='valun_display_name', help='your public display name')
    pf.add_argument('--public-vouches', dest='valun_public_vouches', choices=('on', 'off'),
                    help='on: the guarantees between your agent and agents that also chose this are public on both agent '
                         'pages (who guaranteed whom, the cap, how calls were honored; never the creditor); off hides them again')
    j = subs.add_parser('join', help='join Valun (--sandbox: no invite needed) and connect this agent, in one step')
    j.add_argument('--sandbox', action='store_true',
                   help='join without an invite as a self-serve participant: small limits, 7 days renewed while your agent is '
                        'active; where the server runs the open network (the default server does) it deals with any agent '
                        'whose owner allows it, otherwise only with the always-on sandbox agents; your client also signs its '
                        'consents by itself (process_consents on)')
    j.add_argument('--handle', help='your public handle (3-24 letters, digits, _ or -); asked if omitted')
    j.add_argument('--operator', help='who controls this agent, shown publicly (your name or organisation); asked if omitted')
    j.add_argument('--display-name', default='', help='optional public display name')
    j.add_argument('--about', default='', help='optional public description of you or your agent')
    j.add_argument('--services', default='', help='optional public list of what your agent offers')
    j.add_argument('--wants', default='', help='optional public list of the work your agent pays others for')
    j.add_argument('--server', help='Valun server URL (default: configured server_url)')
    j.add_argument('--invite-from-stdin', action='store_true', help='read the invite code from stdin instead of a hidden prompt')
    j.add_argument('--owner-token-file', metavar='FILE',
                   help='the NEW file join writes your owner token to (only you, SYSTEM and Administrators can open it: '
                        'created 0600, and on Windows with an access list); without it, valun-owner-token-HANDLE.txt in '
                        'your home folder. Join refuses before anything is sent (exit 2) when that file exists already, '
                        'its folder is missing or cannot take a new file, or the file cannot be written there (join makes '
                        'it, writes it and removes it again to check). The token is never printed on stdout')
    j.add_argument('--approvals', choices=['conversation', 'console', 'none'], default='conversation',
                   help="where commitments are approved: 'conversation' (Hermes asks you before each one; the default, "
                        'also with --sandbox: this join does not ask which you want, as `valun join --sandbox` does since '
                        "valun 0.7.3; it needs a person in the conversation to answer), 'console' (your agent signs at "
                        'once and Hermes asks nobody; the Valun server holds its offers, counter-offers, acceptances and '
                        'guarantee offers, and the note or payment by transfer it signs when it accepts a delivery, for '
                        'you in the web owner console; never an honor of a guarantee call, a '
                        "guarantee's acceptance, call or release, a limit your agent loosens, or a consent; choose it only "
                        "when a person will approve there), or 'none' (autonomous within your limits: for an agent whose "
                        'commitments nobody will approve)')
    j.add_argument('--direct-tools', action='store_true',
                   help='expose the Valun tools to the model directly (sets tools.tool_search.enabled=off in THIS profile); '
                        'recommended for a dedicated Valun profile and for small models')
    co = subs.add_parser('consents', help='circulating credit: the consents (set-offs, transfers) waiting for your '
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

    eg = subs.add_parser('ergo', help="the testnet network: settlement on Ergo testnet (OWNER, at a terminal): this agent's "
                                      'Ergo address, the chain, the reserves, a reserve of your own tERG and the redemption of '
                                      "a note you hold (reserve and redeem ask the Valun server, with your agent's token, for "
                                      "what they sign: a reserve's payment request, a redemption's transaction; and they "
                                      'read the Ergo testnet node that GET /chain names, also without --broadcast; only with '
                                      '--broadcast is the transaction sent)')
    egs = eg.add_subparsers(dest='valun_ergo')
    egs.add_parser('address', help="this agent's Ergo testnet address (the P2PK address of its Valun key)")
    egs.add_parser('chain', help="the tracker's anchoring on Ergo testnet")
    egs.add_parser('reserves', help='the Basis reserves the network sees')
    er = egs.add_parser('reserve', help='lock NANO nanoERG of your own tERG in a Basis reserve (needs ergo-lib-python '
                                        "0.28.0 in Hermes's Python; the Valun server answers the reserve's payment request "
                                        "either way, POST /reserves/create with your agent's token, and the transaction is "
                                        'built and signed here from your own boxes; it goes to the Ergo testnet node only '
                                        'with --broadcast)')
    er.add_argument('valun_nano', metavar='NANO', type=int)
    er.add_argument('--broadcast', dest='valun_broadcast', action='store_true')
    ed = egs.add_parser('redeem', help='redeem AMOUNT nanoERG of the note ISSUER issued to you, against its reserve (the '
                                       'Valun server builds the transaction either way, with its tracker: POST '
                                       "/redemption/build, with your agent's token; checked and signed here with your own "
                                       'key; submitted only with --broadcast)')
    ed.add_argument('valun_issuer', metavar='ISSUER', help="the issuer's handle or participant id")
    ed.add_argument('valun_amount', metavar='AMOUNT', type=int)
    ed.add_argument('--broadcast', dest='valun_broadcast', action='store_true')


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


# The child Python that runs `hermes config set KEY VALUE` exactly as `python -m hermes_cli.main` would, but takes VALUE
# from its environment: a process's command line is readable by every local user (Linux /proc/<pid>/cmdline without
# hidepid), its environment only by the same user and root. The child removes VALUE from its environment before Hermes
# starts, and setting sys.argv inside it never changes the command line the system shows.
CONFIG_VALUE_ENV = 'VALUN_PLUGIN_CONFIG_VALUE'
_CONFIG_SET_CHILD = ('import os, runpy, sys; '
                     f'value = os.environ.pop({CONFIG_VALUE_ENV!r}); '
                     "sys.argv = ['hermes', 'config', 'set', sys.argv[1], value]; "
                     "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)")


def _hermes_config_set(key, value):
    # Hermes's documented route for storing a secret (`hermes config set`), with no shell: the value never passes
    # through a shell, its history or any command line (it reaches the child through the child's environment,
    # _CONFIG_SET_CHILD) and is never printed. The active profile home is pinned explicitly, so a secret always lands in
    # the profile this command was run for.
    env = dict(os.environ)
    try:
        from hermes_constants import get_hermes_home
        env['HERMES_HOME'] = str(get_hermes_home())
    except Exception:  # noqa: BLE001 - fall back to the inherited environment
        pass
    env[CONFIG_VALUE_ENV] = value
    r = subprocess.run([sys.executable, '-c', _CONFIG_SET_CHILD, key], capture_output=True, text=True, env=env)
    return r.returncode == 0


def _profile(ctx):
    """This Hermes profile's name, or None for a Hermes home that is no named profile (tools.profile_name)."""
    return tools.profile_name(ctx)


def _hermes(ctx, rest, sub='valun'):
    """A command for THIS profile, as printed for the owner (tools.hermes_cmd): `hermes -p NAME valun REST`, or `hermes
    valun REST` in a home that is no named profile (_home_note says to keep HERMES_HOME)."""
    return tools.hermes_cmd(ctx, rest, sub)


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
    return tools.setting_cmd(ctx, key, value)


def _terminal_text(s, limit=300):
    """Text for this terminal: one line, printable characters only, at most `limit` characters."""
    return ''.join(ch if ch.isprintable() else ' ' for ch in str(s))[:limit]


def _owner_text(ctx, s, limit=300):
    """A core message for the owner, with the core-CLI commands it names (`valun attempts ...`, `valun config ...`)
    named as this profile's Hermes commands (tools.hermes_words)."""
    s = tools.hermes_words(ctx, str(s))
    return _terminal_text(s.replace('`valun attempts', '`' + _hermes(ctx, 'attempts')), limit)


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
        return ('signed by your client by itself, before Valun tool calls that are not read-only, at most once a minute; '
                'set-offs and payments to you by transfer also before valun_status and valun_inbox (process_consents on; '
                'nothing runs in the background)')
    return (f'none signed by itself (process_consents off), and your client declares no consent role, so the server '
            f'proposes none that would wait for it; `{_hermes(ctx, "consents --process")}` signs any that still wait '
            'when you run it')


def _cap_problem(ctx):
    """What is wrong with the consents.max_PC setting, with the fix (the default cap, PC-sized: in the network's unit);
    None when nothing is."""
    _cap, problem = tools.consent_cap(ctx)
    if problem is None:
        return None
    cap = tools.CONSENT_MAX_PC * tools.scale(ctx)
    return f'{problem}. Fix: `{_setting_cmd(ctx, "consents.max_PC", cap)}` (or none for no cap).'


# What a network's unit is, in doctor's words (as `valun doctor` says it; fix inbox #15): the testnet's are test coins;
# "synthetic" only for the retired pilot's PC. A network this plugin does not know gets no claim.
UNIT_KINDS = {schemas.TESTNET: 'test coins of Ergo testnet, no monetary value', 'valun-pilot-1': 'synthetic'}


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
        problems.append(f'Not connected: run `{_hermes(ctx, "join --sandbox")}` (no invite: a self-serve participant), '
                        f'`{_hermes(ctx, "join")}` (with an invite) or `{_hermes(ctx, "enroll")}` (with an enrollment code).')
    try:
        info = vc.Client(_MemoryStore(), server).request('GET', '/pilot/v1/info', auth=False)
        kind = UNIT_KINDS.get(info['network'])
        ok.append(f"Server {server} reachable: {info['network']}, unit {info['unit']}" + (f' ({kind})' if kind else ''))
    except Exception as e:  # noqa: BLE001
        problems.append(f'Server {server} not reachable ({e}). Check the server_url setting and your connection.')
    store = tools.HermesStore(ctx)
    if store.unreadable is not None:
        problems.append(f'Your Valun client cannot read its records ({_terminal_text(store.unreadable)}), so it signs nothing. '
                        f'Fix: restore {store.path or "plugin-data/valun/valun-records.json"} from a backup. Move it away only '
                        'if you know it holds nothing you need: it is your record of the notes and payments your agent '
                        'signed.')
    if pid and real(tok) and real(sec):
        try:
            cl = vc.Client(store)
            me = cl.me()
            m = me['mandate']
            ok.append(f"Agent accepted by the server as {me['participant']['handle']} on {cl.network}; limits: at most "
                      f"{vc.amount_text(m['max_issue_per_deal'], cl.unit)} per deal, "
                      f"{vc.amount_text(m['max_issue_outstanding'], cl.unit)} owed in total")
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
    if _cap_problem(ctx):
        problems.append(_cap_problem(ctx))
    for line in ok:
        print('  ok    ' + line)
    for line in problems:
        print('  FIX   ' + line)
    print('All set.' if not problems else f'{len(problems)} thing(s) to fix.')
    return 0 if not problems else 1


def _open_new_private(path):
    """A NEW file at `path`, open for writing, that only this user (SYSTEM and Administrators aside) can open, made as
    valun 0.7.2's join makes the owner token file (the core's home.open_new_private): never an existing file, maybe
    another participant's owner token, which is not overwritten or truncated, nor a symlink followed (FileExistsError);
    mode 0600 on POSIX; on Windows, which ignores a file mode, an access list that lets in only this user (named by its
    SID), SYSTEM and Administrators, set as the file is created (until 0.7.3 the plugin's core had no home.py, and the
    file had its folder's permissions there). _not_private_note names a drive where that does not hold."""
    return vh.open_new_private(path)


def _write_new_private(path, text):
    """Write `text` into a NEW private file (_open_new_private). If writing fails, the file this call created is removed
    (as the core CLI's _write_private does)."""
    fd = _open_new_private(path)
    try:
        try:
            f = os.fdopen(fd, 'w', encoding='utf-8')
        except BaseException:
            os.close(fd)
            raise
        with f:
            f.write(text)
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def default_owner_token_file(handle):
    """Where join writes the owner token when no --owner-token-file is given, as valun 0.7.2's `valun join` does:
    valun-owner-token-HANDLE.txt in the home folder Python finds ($HOME; USERPROFILE on Windows), one file per handle.
    Never an existing file: join refuses one before anything is sent (_token_file_refusal)."""
    h = str(handle or '').strip().lower()
    name = f'valun-owner-token-{h}.txt' if re.fullmatch(r'[a-z0-9_-]{1,40}', h) else 'valun-owner-token.txt'
    return os.path.join(os.path.expanduser('~'), name)


TOKEN_ROOM = 4096           # bytes the check of the token file writes: far more than an owner token (vpo_ and 43 more)


def _token_path_refused(path):
    """Why the owner token file `path` itself cannot be written, or None when it can: `path` is made as a new private
    file (_open_new_private), checked to be a regular file, TOKEN_ROOM bytes are written to it and flushed to disk, and
    it is removed again. The probe of its folder (home.new_file_refused) misses a name the folder refuses (too long, a
    character Windows does not take, a final separator, a path longer than Windows takes) and a disk or quota with no
    room left, each of which would otherwise fail only after the join was sent, the participant made and its owner token
    in hand (the review of 0.7.3; valun 0.7.2's join probes only the folder). A file that appeared since the first check
    is left to _hand_over (None), which writes the token beside it. A file system that cannot flush a file to disk
    (fsync: EINVAL, ENOSYS, ENOTSUP) is not refused for that: what was written is enough."""
    try:
        fd = _open_new_private(path)
    except FileExistsError:
        return None
    except (OSError, ValueError) as e:              # ValueError: a name with a NUL character
        return getattr(e, 'strerror', None) or str(e) or type(e).__name__
    regular, why = True, None                       # made just now, never an existing file: removed below
    try:
        regular = stat.S_ISREG(os.fstat(fd).st_mode)
        if not regular:
            why = 'it is not a regular file'
        room = b'\n' * TOKEN_ROOM if regular else b''
        while room:
            n = os.write(fd, room)
            if n <= 0:
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
            room = room[n:]
        if regular:
            try:
                os.fsync(fd)
            except OSError as e:
                if e.errno not in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
                    raise
    except OSError as e:
        why = e.strerror or type(e).__name__
    finally:
        try:
            os.close(fd)
        except OSError as e:                        # a network file system may say only now that there is no room
            why = why or e.strerror or type(e).__name__
        if regular:
            try:
                os.unlink(path)
            except OSError:
                pass
    return why


def _faithful(s, limit):
    """`s` as one shell word that is exactly `s` in bash, zsh, PowerShell and Windows cmd: as it is when it is only
    letters, digits and _.:/=+-, else in double quotes; None when no quoting keeps it in all of them (more than `limit`
    characters, a character the terminal cannot show, a " $ ` ! or %, a curly double quote, a doubled or final
    backslash) or for an empty value, as valun 0.7.2's join writes the join to run again (cli._faithful)."""
    if not isinstance(s, str) or not s or len(s) > limit or not s.isprintable():
        return None
    if re.fullmatch(r'[A-Za-z0-9_.:/=+-]+', s):
        return s
    if any(ch in s for ch in '"$`!%“”„') or '\\\\' in s or s.endswith('\\'):
        return None
    return f'"{s}"'


def _join_again(args, ctx, server, handle, operator):
    """This join as a command to run again, with --owner-token-file FILE (valun 0.7.2's _join_command): this profile,
    --sandbox or --invite-from-stdin as given, the handle, the operator, the public texts, --approvals (not the
    default), --direct-tools and --server, each as given. A value that cannot be written as one shell word exactly is
    named apart ("..., with the same --about as before")."""
    words, apart = ['join'], []
    if getattr(args, 'sandbox', False):
        words.append('--sandbox')
    elif args.invite_from_stdin:
        words.append('--invite-from-stdin')
    for flag, value, limit, stand_in in (('--handle', handle, 40, 'YOUR-HANDLE'), ('--operator', operator, 60, '"YOUR NAME"')):
        shown = _faithful(value, limit)
        words.append(f'{flag} {shown or stand_in}')
        if not shown:
            apart.append(flag)
    for flag, value in (('--display-name', args.display_name), ('--about', args.about), ('--services', args.services),
                        ('--wants', args.wants)):
        if value:
            shown = _faithful(value, 300)
            if shown:
                words.append(f'{flag} {shown}')
            else:
                apart.append(flag)
    if args.approvals != 'conversation':
        words.append(f'--approvals {args.approvals}')
    if args.direct_tools:
        words.append('--direct-tools')
    words.append('--owner-token-file FILE')
    if args.server:
        shown = _faithful(server, 300)
        if shown:
            words.append(f'--server {shown}')
        else:
            apart.append('--server')
    cmd = '`' + _hermes(ctx, ' '.join(words)) + '`'
    return cmd + (' (with the same ' + ', '.join(apart) + ' as before)' if apart else '')


def _token_file_refusal(args, ctx, server, handle, operator):
    """Before anything is sent: why the owner token file this join would write (--owner-token-file, else
    default_owner_token_file) cannot be made, as join's whole answer ("Nothing was sent: ..."; exit 2), or None when it
    can, as valun 0.7.2's join does (fix inbox #174, as #167 for the client: until 0.7.2 the plugin sent the join, made
    the participant and then printed its owner token on stdout, in an agent's context where an agent ran it). Refused:
    a file that exists already (a symlink too, even a broken one: it may hold another identity's owner token, and join
    never replaces a file), a folder that is missing, is not a folder, or cannot take a new file (home.new_file_refused:
    a private probe file made there and removed again), and, since the review of 0.7.3, a file that cannot be made at
    that very path or has no room there (_token_path_refused: made, written and removed again)."""
    chosen = bool(args.owner_token_file)
    path = args.owner_token_file if chosen else default_owner_token_file(handle)
    folder = os.path.dirname(os.path.abspath(path))
    default = '' if chosen else ' (join writes your owner token there when no --owner-token-file is given)'
    if os.path.lexists(path):
        why = (f'{path} exists already{default}, and join never replaces a file: it may hold another owner token. Name a '
               'new file, one that does not exist yet')
    elif not os.path.isdir(folder):
        why = (f'the folder of {path}{default}, {folder}, does not exist, so your owner token could not be written '
               'there. Name a new file in a folder that exists')
    else:
        denied = vh.new_file_refused(folder)
        if denied is not None:
            why = (f'this user may not create a file in {folder} ({denied.rstrip(".")}), where {path}{default} would '
                   'go. Name a new file in a folder you can write')
        else:
            bad = _token_path_refused(path)
            if bad is None:
                return None
            why = (f'{path}{default} cannot be written as a new file ({bad.rstrip(".")}), so your owner token could not '
                   'be written there. Name another new file')
    return (f'Nothing was sent: {why}, and run this join again: {_join_again(args, ctx, server, handle, operator)} '
            "(FILE: a new file for this join's owner token).")


def _token_sibling(path, n):
    """The n-th new file beside `path` for an owner token, when `path` appeared after join checked it: FILE.1.txt,
    FILE.2.txt, ... for FILE.txt (FILE.1 for a FILE with no extension)."""
    base, ext = os.path.splitext(path)
    return f'{base}.{n}{ext}'


TOKEN_SIBLINGS = 20                     # how many new files beside the one named join tries (_token_sibling)


def _not_private_note(path):
    """On Windows, ' ...' when the owner token file at `path` is not owner-only after all (a drive that keeps no
    permissions, such as WSL's share, or one that lets everyone in, as FAT32 and exFAT do, or an access list that lets
    others in): move it, as valun 0.7.2's join says (cli._not_private_note). '' on POSIX, where it was created 0600, and
    for a file only its owner can open. Never raises: the token is in that file already."""
    if os.name != 'nt':
        return ''
    try:
        a = vh.access_of(path)
        if a['state'] == 'ok' and not (a['others'] or a['owner']):
            return ''
        who = (f'it can also be opened by {vh.who_else(a)}' if a['state'] == 'ok' else
               'this drive keeps no permissions for it' if a['state'] == 'no_acl' else 'its permissions could not be read')
    except Exception:  # noqa: BLE001 - what the file allows could not be read: say so, never fail here
        who = 'its permissions could not be read'
    return f' Windows could not make that file private ({who}), so move it somewhere only you can open.'


def _pid_word(pid):
    """A participant id the server sent, as a sentence names it: the id when it has a participant id's shape (vp_ + 12
    hex), else PARTICIPANT_ID (the owner console shows it), never the server's text as it is."""
    return pid if isinstance(pid, str) and re.fullmatch(r'vp_[0-9a-f]{12}', pid) else 'PARTICIPANT_ID'


def _stderr_at_terminal():
    """Is stderr a terminal, where a person reads it (not an agent's pipe or a file)? The only place the owner token is
    ever shown, when no file can be written at all (_hand_over)."""
    try:
        return bool(sys.stderr.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _hand_over(args, owner, server, handle=None, pid=None):
    """The owner token, to the owner, once (decision D-3 c: right after the join answer, before anything else can fail),
    as valun 0.7.2's join hands it over: written to a new private file (_write_new_private), --owner-token-file or, when
    it is not given, default_owner_token_file, and never printed on stdout, where an agent that ran the join reads it
    (fix inbox #174). Join refused an existing file, or a folder that could not take one, before anything was sent
    (_token_file_refusal), so what is left is a file that appeared in between (another join, say): it is never replaced,
    and the token goes to a new file beside it (_token_sibling), which join names. When no file can be written at all
    (the folder stopped taking files, the disk filled up), the token is shown on stderr only when stderr is a terminal (a
    person's, not an agent's pipe), and otherwise nowhere: join says how the owner gets a new one. False is returned
    whenever the token is not in the file named (join then exits 1 once its other steps are done, so that a script that
    relies on the file notices); True otherwise. args.owner_token_path: the file it was meant for;
    args.owner_token_written: the absolute path of the file it went to, else None."""
    chosen = bool(args.owner_token_file)
    path = args.owner_token_file if chosen else default_owner_token_file(handle)
    args.owner_token_path, args.owner_token_written = path, None
    keep = ('Keep it private; never give it to your agent. Move it into your password manager once you have signed in '
            f'to the owner console ({server}/pilot#owner) with it.')
    try:
        _write_new_private(path, owner + '\n')
        args.owner_token_written = os.path.abspath(path)
        print(f'Your OWNER token is in {path}'
              + ('' if chosen else ' (no --owner-token-file was given: this is where join writes it)')
              + '. ' + keep + _not_private_note(path))
        return True
    except FileExistsError:
        failed = None
        for n in range(1, TOKEN_SIBLINGS + 1):
            other = _token_sibling(path, n)
            try:
                _write_new_private(other, owner + '\n')
            except FileExistsError:
                continue
            except OSError as e:
                failed = e
                break
            args.owner_token_written = os.path.abspath(other)
            print(f'{path} appeared after join checked it, and join never replaces a file (it may hold another owner '
                  f'token), so your OWNER token is in a new file beside it: {other}. ' + keep + _not_private_note(other))
            return False
        why = (f'{failed.strerror or failed}' if failed is not None else
               f'{path} and the next {TOKEN_SIBLINGS} names beside it exist already')
    except OSError as e:
        why = f'{e.strerror or e}'
    print(f'Cannot write {path} ({why}).')
    if _stderr_at_terminal():
        sys.stderr.write('Your OWNER token, shown once on this terminal only (never on stdout); keep it in a password '
                         f'manager, never give it to your agent; it opens {server}/pilot#owner where you can change limits '
                         f'or revoke this agent:\n  {owner}\n')
        sys.stderr.flush()
        print('Your owner token was shown once on this terminal (stderr), never on stdout: copy it into your password '
              'manager now.')
        return False
    who = _pid_word(pid) if pid else 'your participant'
    print('Your owner token was not shown either: stderr is not a terminal here, and an agent may be reading it. No copy '
          f'of it is kept, so nobody can sign in to the owner console ({server}/pilot#owner) for {who}, to approve what it '
          'holds for you, change limits or revoke the agent, until the operator of this server issues a new owner token '
          'for it, at your request' + (' (on valun.tech: https://valun.tech/start#report).' if server == tools.DEFAULT_SERVER
                                       else '.'))
    return False


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
    refused = _token_file_refusal(args, ctx, server, args.handle, args.operator)
    if refused:                         # the owner token could not be written: it would end up on screen, or nowhere
        print(refused)
        return 2
    store = _MemoryStore()
    c = vc.Client(store, server)
    try:
        if sandbox:   # no invite: a sandbox-only participant (only where the server offers it)
            j = c.join_sandbox(args.handle, args.operator, display_name=args.display_name, about=args.about, services=args.services,
                               wants=args.wants)
        else:
            j = c.join(invite, args.handle, args.operator, display_name=args.display_name, about=args.about, services=args.services,
                       wants=args.wants)
    except vc.ValunError as e:
        print(f'Joining failed ({e.code}): {e}')
        return 1
    owner = j.get('owner_token') if isinstance(j, dict) else None
    if not (isinstance(owner, str) and owner):
        print('Joining failed (bad_response): the server did not answer an owner token.')
        return 1
    # Decision D-3 c: from here on the participant exists, and its owner token is in this answer only. It is handed over
    # first (_hand_over: written to --owner-token-file, or the default file, and never to stdout), before enrolment and
    # before any local write that can fail (Hermes's secret store, this profile's settings, the records); whatever fails
    # after this, join says what is not done and how to finish, and exits 1.
    handed = _hand_over(args, owner, server, args.handle, j.get('participant_id'))
    step = ['enroll']
    try:
        finished = _join_steps(args, ctx, c, store, j, server, owner, sandbox, step)
    except Exception as e:  # noqa: BLE001 - the owner token is handed over already: say what stopped, never a traceback
        pid = _terminal_text(j.get('participant_id') or 'PARTICIPANT_ID', 40)
        print(f'Joining stopped ({_failure(e)}): {_terminal_text(e)}')
        print(f'  Your participant {pid} exists, and '
              + ('your owner token was handed over above' if getattr(args, 'owner_token_written', None) else
                 'join said above what became of your owner token') + '. Not done: '
              + _not_done(ctx, step[0], server, pid) + '.')
        print(f'Then run `{_hermes(ctx, "doctor")}`.' + _home_note(ctx))
        finished = False
    if not handed:
        written = getattr(args, 'owner_token_written', None)
        print(f'Exit code 1: {args.owner_token_path} was not written'
              + (f': your owner token is in {written}.' if written else '.'))
    return 0 if finished and handed else 1


# What a join that stopped on an unexpected error (a config file that cannot be written, say) had not done yet, by the
# step it was in (_join; decision D-3 c). Each leaves the profile on the safe side (until its settings are written it
# is not connected, Hermes asks before each commitment and its client signs no consent by itself) and says how to
# finish, with commands that name this profile, and the server and the participant where the profile does not hold them.
JOIN_NOT_DONE = {
    'enroll': ("registering this agent's key: issue an enrollment code in the owner console ({server}/pilot#owner) with "
               'your owner token, then run `{enroll}`'),
    'credentials': ("storing this agent's key and token in this profile, although the server may hold the key: in the "
                    'owner console ({server}/pilot#owner), with your owner token, revoke this agent, which gives a new '
                    'enrollment code (a new code is refused while a key is registered; if the console shows no agent key, '
                    'issue an enrollment code instead), then run `{enroll}`'),
    'settings': ("this profile's settings: run `{server_cmd}` and `{pid_cmd}` (until then its Valun tools are not "
                 'connected; the consent settings, process_consents and consents.*, stay off until you set them), and '
                 'set where approvals happen in the owner console ({server}/pilot#owner)'),
    'mandate': ("your owner mandate's approve_above (with --approvals console also review_deliveries): set them in the owner "
                'console ({server}/pilot#owner); until then Hermes asks before each commitment'),
    'approvals': ('console approvals in this profile: Hermes asks before each commitment until you run `{console_cmd}`'),
    'report': 'nothing that join sets (only the rest of its report)',
}


# What console approvals hold for the owner, and what they never hold, in valun 0.7.3's words (cli.CONSOLE_HOLDS and
# CONSOLE_NEVER, which say "note" since fix inbox #177: the approval semantics of 2026-11-17, verified on staging).
# Join sets the owner mandate to approve_above 0 and review_deliveries on, as `valun join --approvals console` does, and turns confirm_commitments off,
# so Hermes asks about none of them (fix inbox #174: 0.7.2 said every commitment waited in the console).
CONSOLE_HOLDS = ("your agent's offers and counter-offers, its acceptances (taking a job included), its guarantee offers, "
                 'and the note or payment by transfer it signs when it accepts a delivery')
CONSOLE_NEVER = ("honoring a guarantee call (your agent's own note, recorded at once), accepting, calling and releasing "
                 'guarantees, deliveries, declines, withdrawals and disputes, your agent loosening or clearing a limit it '
                 'recorded (never past your mandate), and the consents its client signs by itself')
# What a decision in the console leaves, and its deadlines, from valun 0.7.2's CONSOLE_DECIDES (the review of 0.7.3:
# "each until you approve it" read as if a held item waited for ever and rejecting undid it).
CONSOLE_DECIDE = ('Decide a held note by the deadline its card shows: one still held when its hour at the tracker ends is '
                  'never recorded and pays nothing; decide a held payment by transfer within 20 minutes of your agent '
                  'signing it. Rejecting does not unsign: a rejected offer, acceptance or note stays on the server\'s '
                  'record, still signed (a rejected payment by transfer is deleted).')


def _not_done(ctx, step, server, pid):
    return JOIN_NOT_DONE[step].format(
        server=server, enroll=_hermes(ctx, f'enroll --server {server} --participant {pid}'),
        server_cmd=_setting_cmd(ctx, 'server_url', server), pid_cmd=_setting_cmd(ctx, 'participant_id', pid),
        console_cmd=_setting_cmd(ctx, 'confirm_commitments', 'false'))


def _failure(e):
    """What failed, as one word for this terminal: a ValunError's code, else the exception's class (OSError, say)."""
    return e.code if isinstance(e, vc.ValunError) else type(e).__name__


def _join_steps(args, ctx, c, store, j, server, owner, sandbox, step):
    """`hermes valun join` once its owner token was handed over: register this agent's key, store it in Hermes's secret
    store, write this profile's settings, pin the network, set the owner mandate, and say what is in force. step[0]
    names the step it is in, for _join's message when an unexpected error stops it. Returns True when every step was
    done; False after saying what is not done and how to finish it (with commands that name this profile)."""
    pid = j['participant_id']
    enroll = f'`{_hermes(ctx, f"enroll --server {server} --participant {pid}")}`'
    try:
        r = c.enroll(pid, j['enrollment_code'])
    except Exception as e:  # noqa: BLE001 - a request refused, or an answer this client could not take
        # Nothing is stored in this profile yet (no key, no settings), so the command names the server and the
        # participant too.
        # refused, or never reached the server: no key is registered. wrong_network is the core's check of the
        # server's answer (0.5): the server registered the key, so it is the case below.
        if isinstance(e, vc.ValunError) and e.code != 'wrong_network':
            print(f"Your participant {pid} was created, but this agent's key could not be registered ({_failure(e)}): "
                  f'{_terminal_text(e)}')
            print(f'To finish: issue an enrollment code in the owner console ({server}/pilot#owner) with your owner token, '
                  f'then run {enroll}.' + _home_note(ctx))
            return False
        # Not the request: the server registered the key (its answer reached this client, which then holds the
        # participant id) or may have (no answer was read), and the key was not kept here. While a key is registered
        # the server refuses a new enrollment code (already_enrolled); revoking the agent gives one.
        held = 'holds' if c.state.get('participant_id') == pid else 'may hold'
        print(f"Your participant {pid} was created, and the server {held} this agent's key, but the key could not be "
              f'stored here ({_failure(e)}): {_terminal_text(e)}')
        print(f'To finish: in the owner console ({server}/pilot#owner), with your owner token, revoke this agent, which '
              'gives a new enrollment code (a new code is refused while a key is registered)'
              + ('' if held == 'holds' else '; if the console shows no agent key, issue an enrollment code instead')
              + f', then run {enroll}.' + _home_note(ctx))
        return False
    step[0] = 'credentials'
    stored = _hermes_config_set('VALUN_SIGNING_SECRET', store.secret()) and _hermes_config_set('VALUN_AGENT_TOKEN', store.token())
    step[0] = 'settings'
    ctx.set_config('server_url', server)
    ctx.set_config('participant_id', pid)
    ctx.set_config('network', c.network)      # 0.5: the network the join named (the testnet too), kept with the server
    # Commitments: Hermes asks before each one ('conversation'), or not at all ('none'). 'console' keeps Hermes asking
    # until the mandate step below holds every commitment for the owner in the console.
    ctx.set_config('confirm_commitments', args.approvals != 'none')
    # The local consent policy (design 6.6, decision D-2 g): a sandbox participant's client signs set-offs, transfers of
    # its own debt and payments to it by transfer by itself (process_consents on); an invited participant's policy signs
    # set-offs and transfers of its own debt, and only once its owner turns process_consents on (off by default). Each
    # consent is capped at the policy's max_PC (consents.max_PC). process_consents is written last, so a join stopped
    # before it leaves the client signing no consent by itself.
    policy = va.SANDBOX_CONSENT_POLICY if sandbox else va.INVITE_CONSENT_POLICY
    for role in tools.CONSENT_ROLES:
        ctx.set_config(f'consents.{role}', policy[role] is True)
    cap = policy['max_PC'] * vc.network_of(c.network)['scale']      # PC-sized: in the network's unit
    ctx.set_config('consents.max_PC', cap)
    if sandbox:
        ctx.set_config('process_consents', True)
    if args.direct_tools:
        _hermes_config_set('tools.tool_search.enabled', 'off')
    finished = True
    print(f"Joined Valun as {args.handle} ({pid}) on {server}. This agent's key was generated here; public key {r['pubkey'][:16]}...")
    if stored:
        print('The agent token and signing key are stored in this Hermes profile (not shown).')
    else:
        finished = False
        print('Hermes could not store the agent credentials, so this profile is not connected. In the owner console '
              f'({server}/pilot#owner), with your owner token, revoke this agent, which gives a new enrollment code (a new '
              f'code is refused while a key is registered), then run {enroll}.' + _home_note(ctx))
    if j.get('sandbox_only'):
        print(j.get('note') or 'Sandbox only: you can deal with the sandbox agents (sandbox-exact, sandbox-mallory) for a week.')
    step[0] = 'mandate'
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
            step[0] = 'approvals'
            ctx.set_config('confirm_commitments', False)
        step[0] = 'report'
        # Fix inbox #164 option 3, for Hermes: this join keeps its default (conversation) and asks nothing; the line says
        # what an agent nobody answers for needs. Conversation and none set the same owner mandate (approve_above: the
        # per-deal cap, _set_mandate) and differ only in confirm_commitments, so turning it off is --approvals none.
        print({'conversation': 'Approvals: before each offer, acceptance, note you issue or payment with a note you hold, '
                               'Hermes asks you in the conversation. Where nobody is there to answer, Hermes refuses and '
                               'nothing is signed: for an agent nobody approves for, run '
                               f'`{_setting_cmd(ctx, "confirm_commitments", "false")}` (the same as --approvals none).',
               'console': 'Approvals: console. Your agent signs at once and Hermes asks nobody; the Valun server then holds '
                          f'for you in the owner console ({server}/pilot#owner), each until you approve or reject it, '
                          + CONSOLE_HOLDS + '. Never held: ' + CONSOLE_NEVER + '. Nothing notifies you. ' + CONSOLE_DECIDE
                          + f' ({server}/start#approve says what approving and rejecting do).',
               'none': 'Approvals: none. Your agent acts on its own within your limits '
                       f"(at most {vc.amount_text(limits.get('max_issue_per_deal'), c.unit)} per deal, "
                       f"{vc.amount_text(limits.get('max_issue_outstanding'), c.unit)} owed in total)."}[args.approvals])
    step[0] = 'report'
    if circulating:
        if sandbox:
            print('Consents: your Valun client signs, by itself, set-offs that leave your net position unchanged, transfers of '
                  'notes you issued to a new holder and payments to you by transfer from the sandbox agents (up to '
                  f"{vc.amount_text(cap, c.unit)} each), before Valun tool calls that are not read-only; set-offs and payments to "
                  'you also before valun_status and valun_inbox, at most once a minute (process_consents on; nothing runs '
                  f'in the background). Turn it off: `{_setting_cmd(ctx, "process_consents", "false")}`.')
        else:
            print('Consents: your Valun client signs none by itself (process_consents off). Once you turn it on '
                  f'(`{_setting_cmd(ctx, "process_consents", "true")}`) it signs set-offs that leave your net position '
                  'unchanged and transfers of notes you issued to a new holder; payments to you by transfer stay off '
                  '(consents.as_new_creditor). '
                  f'`{_hermes(ctx, "consents")}` shows what waits.')
    print(f'Next: `{_hermes(ctx, "doctor")}`, then talk to your agent: `{_hermes(ctx, "-t valun", sub="chat")}`.' + _home_note(ctx))
    return finished


def _status(ctx):
    tok, sec = bool(tools.secret_value('VALUN_AGENT_TOKEN')), bool(tools.secret_value('VALUN_SIGNING_SECRET'))
    store = tools.HermesStore(ctx)
    print(f"server:          {ctx.get_config('server_url', default=tools.DEFAULT_SERVER)}")
    print(f"network:         {tools.network(ctx)} (amounts in {tools.unit(ctx)})")
    print(f"participant:     {ctx.get_config('participant_id', default='') or '(not enrolled)'}")
    print(f"agent token set: {'yes' if tok else 'no'}")
    print(f"signing key set: {'yes' if sec else 'no'}")
    print(f"confirm commitments in Hermes: {tools.confirm_commitments(ctx)}")
    print(f"consent policy:  {_policy_line(store.kept_policy)}")
    if _cap_problem(ctx):
        print(f"consents.max_PC: {_cap_problem(ctx)}")
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


def _profile_cmd(args, ctx):
    """`hermes valun profile`: this agent's public profile, as the valun package's `valun profile` shows and changes it.
    Without options it shows the profile (GET /pilot/v1/me). With --services, --wants, --about, --display-name or
    --public-vouches on|off it changes only those fields (PUT /pilot/v1/profile). Public vouches are fix inbox #31: the
    guarantees between this agent and agents that also chose it are public, and the creditor is never named. The owner
    runs it at a terminal (_run_cli refuses inside an agent session)."""
    store, c = _connected(ctx)
    if c is None:
        return 1
    vouches = getattr(args, 'valun_public_vouches', None)
    fields = {'display_name': getattr(args, 'valun_display_name', None), 'about': getattr(args, 'valun_about', None),
              'services': getattr(args, 'valun_services', None), 'wants': getattr(args, 'valun_wants', None),
              'public_vouches': None if vouches is None else vouches == 'on'}
    try:
        if all(v is None for v in fields.values()):
            me, changed = c.me(), None
            p = me.get('participant') if isinstance(me, dict) else None
        else:
            r = c.set_profile(**fields)
            p, changed = (r.get('participant'), r.get('changed')) if isinstance(r, dict) else (None, None)
    except vc.ValunError as e:
        print(f'profile failed ({e.code}): {_terminal_text(e)}')
        return 1
    p = p if isinstance(p, dict) else {}
    out = {k: _terminal_text(p.get(k) or '') for k in ('handle', 'display_name', 'about', 'services', 'wants')}
    out['public_vouches'] = p.get('public_vouches') is True
    if changed is not None:
        out = {'changed': [_terminal_text(f, 40) for f in changed] if isinstance(changed, list) else [], **out}
    server = (ctx.get_config('server_url', default=tools.DEFAULT_SERVER) or tools.DEFAULT_SERVER).rstrip('/')
    print(json.dumps({**out, 'page': f"{server}/agent/{out['handle']}"}, ensure_ascii=False, indent=1))
    return 0


def _mandate_of(me):
    return next((me[k] for k in ('effective_limits', 'mandate') if isinstance(me, dict) and isinstance(me.get(k), dict)), None)


_ISO = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z\Z')


def _consent_line(item, unit=vc.UNIT):
    """One consent item of GET /consents, from checked values only (the server wrote it)."""
    kind = item.get('kind') if item.get('kind') in ('transfer', 'setoff') else '?'
    roles = [r for r in item.get('your_roles') or [] if r in ('payer', 'issuer', 'payee', 'member')] \
        if isinstance(item.get('your_roles'), list) else []
    ctxt = item.get('context') if isinstance(item.get('context'), dict) else {}
    summ = item.get('summary') if isinstance(item.get('summary'), dict) else {}
    out = f"{kind} {nv.shown_id(item.get('id')) or '?'} as {', '.join(roles) or '?'}"
    if _whole(ctxt.get('amount')):
        out += f", {ctxt['amount']} {unit}"
    if _whole(summ.get('your_net_change_PC')):
        out += f" (your net change {summ['your_net_change_PC']} {unit})"
    if isinstance(item.get('expires_at'), str) and _ISO.match(item['expires_at']):
        out += f", open until {item['expires_at']}"
    return out


def _decisions(report, unit=vc.UNIT):
    """process_consents() decisions, one line each (its entries hold checked values only)."""
    lines = []
    for bucket in ('signed', 'refused', 'waiting'):
        for e in report.get(bucket) or []:
            what = f"{e.get('kind')} {e.get('id')} as {e.get('role')}"
            if e.get('amount') is not None:
                what += f", {e.get('amount')} {unit} (your net change {e.get('your_net_change_PC')} {unit})"
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
        print('  ' + _consent_line(i, tools.unit(ctx)))
    if not getattr(args, 'valun_process', False):
        if waiting:
            print(f'`{_hermes(ctx, "consents --process")}` signs those your policy allows and refuses the others.')
        return 0
    try:
        r = c.process_consents(policy=policy if policy is not None else {})
    except vc.ValunError as e:
        print(f'Could not process them ({e.code}): {_owner_text(ctx, e)}')
        return 1
    lines = _decisions(r, tools.unit(ctx))
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
# An 'unknown' attempt the server ended before it ever reached the tracker (Client.attempts_overview ended_at_server,
# vc.NEVER_APPLIED_TRANSFER): shown as ended there, with no resolution advised, as `valun attempts list` shows it. It is
# the server's word: the client still judges by the tracker's state and its own records (R11), so it still counts the
# attempt when the deal is paid.
ENDED_AT_SERVER_WHY = {'void_rejected': 'your owner rejected it, before anyone else signed',
                       'void_stale': 'it was out of date when your owner approved it',
                       'void_expired': 'it expired while it waited for your owner',
                       'expired': 'it expired before every party had signed',
                       'cancelled': 'your client cancelled it while it collected signatures',
                       'refused': 'the issuer or the payee refused to sign it',
                       'void_key_revoked': "a party's key was revoked before it was submitted"}
ENDED_AT_SERVER = ('ended by the server ({state}: {why}), which says it never reached the tracker, so it did not apply; '
                   "your client cannot check that on the tracker, so it still pays this deal no more by itself")
ENDED_AT_SERVER_PAID = ('ended by the server ({state}: {why}), which says it never reached the tracker; the deal is '
                        '{paid}: nothing to resolve')
# Why a resolution changes only your client's records (Client.resolution_plan local).
LOCAL_WHY = {
    'note': ("The server holds no payment attempt for a note you issued: only your client's records change (your owner "
             'token is checked first, used once and not stored).'),
    'no_circulation': ("This server offers no circulating credit, so only your client's records change (your owner token is "
                       'checked first, used once and not stored).'),
    'no_server_id': ("Your client did not record the server's id of this transfer (its request got no answer, or the server "
                     'refused it). If the owner console shows a transfer of this deal (vt_...), stop here and run this again '
                     'with that id, so that the server records your decision too.'),
    'server_unknown': 'The server shows no such transfer of yours as this attempt (it may have refused it when it was posted).',
    'server_ended': ('The server holds this transfer as {state}: it ended there, so the server has nothing to resolve; your '
                     'client still counts it as a payment that may have applied.'),
}
# LOCAL_WHY['server_ended'] for a transfer the server ended before it ever reached the tracker (vc.NEVER_APPLIED_TRANSFER),
# and what an owner who records it as applied is told, as `valun attempts resolve` says them.
NEVER_APPLIED_WHY = ('The server ended this transfer as {state} before it ever reached the tracker: on its word the '
                     "payment did not apply, and the server has nothing to resolve. Your client cannot check that on "
                     'the tracker, so it still counts it as a payment that may have applied.')
NEVER_APPLIED_BUT_APPLIED = ("  The server says this payment never applied: recorded as applied, your client never pays "
                             'this deal again.')


def _who_key(c, pk):
    return 'you' if pk == c.pubkey else f'{str(pk)[:16]}...'


def _attempt_line(c, r):
    """One payment attempt, in plain words."""
    i, rcp = (str(r['edge']).split('>') + [''])[:2]
    when = datetime.fromtimestamp(r['timestamp'] / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    prior = r['prior'] if r['prior'] is not None else '?'
    if r['route'] == 'note':
        what = f"your note to {_who_key(c, rcp)}: its total {prior} -> {r['total']} {c.unit}"
    else:
        what = f"paying with the note of {_who_key(c, i)}: your claim {prior} -> {r['total']} {c.unit}"
    sid = f", server id {r['attempt_id']}" if r.get('attempt_id') else ''
    decided = (f"; you resolved it: {r['outcome']}" if r.get('outcome') in vc.ATTEMPT_OUTCOMES and r.get('outcome') != 'applied'
               else '')
    paid = f"paid by {r['paid_by']}" if r.get('paid_by') else 'recorded as paid'
    ended = r.get('ended_at_server') if r['state'] == 'unknown' else None
    if ended in ENDED_AT_SERVER_WHY:
        state = (ENDED_AT_SERVER_PAID if r.get('paid') else ENDED_AT_SERVER).format(
            state=ended, why=ENDED_AT_SERVER_WHY[ended], paid=paid)
    elif r['state'] == 'unknown' and r.get('paid'):
        state = UNKNOWN_BUT_PAID.format(paid=paid)
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


def _shell_word(s, limit=40):
    """A command argument as it can be pasted into bash, zsh or PowerShell: quoted when it is more than letters, digits
    and _.:/=+- (an attempt's '#n' unquoted starts a comment there, and the pasted command would fail)."""
    s = _terminal_text(s, limit).replace("'", '')
    return s if re.fullmatch(r'[A-Za-z0-9_.:/=+-]+', s) else f"'{s}'"


# For an attempt the server reads as inconsistent (one of its edges shows it applied, the other does not), as
# `valun attempts resolve` says it.
INCONSISTENT_WHY = ("  The server reads this transfer as INCONSISTENT: one of its edges shows it applied and the other "
                    "does not. That is the operator's case: ask the operator before you resolve it either way. Do not "
                    "resolve it as not applied only because the payee says the payment did not arrive: your own claim on "
                    "the issuer may already be lower, and paying the deal again would pay it twice.")


def _payee_edge_text(ctx, args, e, state=None):
    """What `hermes valun attempts resolve --applied` prints when the server answers 409 payee_edge_not_applied (the
    payee's edge on the tracker does not show the transfer; resolve_attempt raises it with the reason in its message), as
    `valun attempts resolve` does: what happened, and the ways on, with this profile's exact command (none for an attempt
    the server reads as inconsistent: that one is the operator's). No resolution in the client's records only is offered
    for it."""
    edge = (e.extra or {}).get('payee_edge')
    lines = [
        'The server did not record this payment as applied (payee_edge_not_applied'
        + (f', payee edge: {_terminal_text(edge, 40)}' if edge else '') + '). Nothing was changed, at the server or in '
        "your client's records.",
        '  ' + _terminal_text(e, 600) + '.']
    if state == 'inconsistent':
        return '\n'.join(lines + [INCONSISTENT_WHY, '  Do not resolve it until the operator has looked into it.'])
    again = f'attempts resolve {_shell_word(args.valun_deal)} {_shell_word(args.valun_attempt)} --not-applied'
    return '\n'.join(lines + [
        f'  If the payee confirms the payment did not arrive, resolve it as not applied: `{_hermes(ctx, again)}`.',
        '  If the payee says it arrived, ask the operator to look into it with the payee, and do not resolve it until '
        'then.'])


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
            rows = c.attempts_overview(**read)     # with the server's view of an attempt that may have applied
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
        # a paid deal has nothing to resolve, and no resolution is advised for an attempt the server ended unapplied
        if any(r['state'] == 'unknown' and not r['paid'] and not r.get('ended_at_server') for r in rows):
            print('Resolve one only after asking the payee whether the payment arrived: '
                  f'`{_hermes(ctx, "attempts resolve DEAL ATTEMPT --applied")}` (it arrived) or `--not-applied` (it did not). '
                  "ATTEMPT is '#n' in quotes (unquoted, a # starts a comment in the shell), or the server's id of a "
                  'transfer (vt_...).')
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
    server_state = (plan.get('transfer') or {}).get('state') if plan['server'] else None
    if server_state == 'inconsistent':
        print(INCONSISTENT_WHY)
    if plan['server']:
        print("  The server is told first (with your owner token, used once and not stored); then your client's records.")
        if outcome == 'applied':
            print("  The server records it as applied only if the payee's edge on the tracker shows this transfer; "
                  'otherwise it refuses, and nothing changes.')
    else:
        shown = (plan.get('transfer') or {}).get('state')
        never = plan['local'] == 'server_ended' and shown in vc.NEVER_APPLIED_TRANSFER
        print('  ' + (NEVER_APPLIED_WHY if never else LOCAL_WHY[plan['local']]).format(state=shown or 'ended'))
        if never and outcome == 'applied':
            print(NEVER_APPLIED_BUT_APPLIED)
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
        if e.code == 'payee_edge_not_applied':
            print(_payee_edge_text(ctx, args, e, server_state))
            return 1
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


def _ergo(args, ctx):
    """`hermes valun ergo ...`: settlement on Ergo testnet for this profile's agent (the testnet network only), as the
    valun package's `valun ergo` does it. The owner runs it at a terminal (never the agent: _run_cli refuses inside an
    agent session). The core checks what the tracker built against the chain before this agent's key signs it. Without
    --broadcast, reserve and redeem still ask the Valun server, with the agent's token, for what they sign (reserve: the
    reserve's payment request, POST /reserves/create, from which the core builds the transaction from this agent's own
    boxes; redeem: the transaction, POST /redemption/build) and read the Ergo testnet node that GET /chain names, but send no
    transaction: reserve --broadcast sends it to that node, redeem --broadcast submits it through the server (POST
    /redemption/submit). tERG are test coins with no monetary value."""
    action = getattr(args, 'valun_ergo', None)
    if action not in ('address', 'chain', 'reserves', 'reserve', 'redeem'):
        print(f'usage: `{_hermes(ctx, "ergo address|chain|reserves|reserve NANO [--broadcast]|redeem ISSUER AMOUNT [--broadcast]")}`')
        return 1
    store, c = _connected(ctx)
    if c is None:
        return 1
    broadcast = bool(getattr(args, 'valun_broadcast', False))
    try:
        if action == 'address':
            out = {'network': c.network, 'address': c.ergo_address(), 'unit': c.unit,
                   'note': 'Ergo TESTNET: test coins (tERG) with no monetary value; send tERG here from a testnet faucet'}
        elif action == 'chain':
            out = c.chain()
        elif action == 'reserves':
            out = c.reserves()
        elif action == 'reserve':
            out = c.create_reserve(args.valun_nano, broadcast=broadcast)
        else:
            out = c.redeem(args.valun_issuer, args.valun_amount, broadcast=broadcast)
        if action in ('reserve', 'redeem') and not broadcast and isinstance(out, dict):
            out = {k: v for k, v in out.items() if k != 'signed'}
            out['dry_run'] = ('checked and signed here, NOT sent: add --broadcast to send it' if action == 'reserve'
                              else 'checked and signed here, NOT submitted: add --broadcast to submit it')
    except vc.ValunError as e:
        # with this profile's Hermes commands where the core names its CLI's (0.7.2: reserve_busy, `valun ergo chain`)
        print(f'{action} failed ({e.code}): {_owner_text(ctx, e, 600)}')
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
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
    if cmd == 'ergo':
        return _ergo(args, ctx)
    if cmd == 'profile':
        return _profile_cmd(args, ctx)
    if cmd != 'enroll':
        print('usage: hermes valun join --handle H --operator NAME | hermes valun enroll [--participant vp_...] | '
              'hermes valun status | hermes valun doctor | hermes valun profile [--public-vouches on|off] | '
              'hermes valun consents [--process] | '
              'hermes valun attempts list|resolve | hermes valun ergo address|chain|reserves|reserve|redeem')
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
    ctx.set_config('network', r['network'])
    print(f"Enrolled {participant} on {r['network']}. This agent's public key: {r['pubkey'][:16]}...")
    print('The signing key and agent token are stored in this Hermes profile\'s secrets (not shown). '
          'Start a new Hermes session to use the Valun tools.')
    return 0
