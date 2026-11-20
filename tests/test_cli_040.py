"""0.4.0 operator commands: `hermes valun join` hands the owner token over before anything else can fail and says how to
finish (naming this Hermes profile) when the mandate step fails (0.7.3, as valun 0.7.2's join: into a new file, the
default one without --owner-token-file, never on stdout; a file that exists or a folder that cannot take one is refused
before anything is sent, exit 2); `join --sandbox` turns process_consents on with the
sandbox consent policy; `hermes valun status` shows the consent policy; `hermes valun consents [--process]`; and the
owner-run `hermes valun attempts list|resolve`. Offline: a fake server, throwaway tokens, temporary folders."""
import argparse
import contextlib
import importlib.util
import io
import json
import re
import os
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if 'valun_plugin_pkg' not in sys.modules:
    spec = importlib.util.spec_from_file_location('valun_plugin_pkg', os.path.join(ROOT, '__init__.py'), submodule_search_locations=[ROOT])
    mod = importlib.util.module_from_spec(spec)
    sys.modules['valun_plugin_pkg'] = mod
    spec.loader.exec_module(mod)
plugin = sys.modules['valun_plugin_pkg']
tools = sys.modules['valun_plugin_pkg.tools']
va, vc = tools.va, tools.vc
# Hermetic: never a real Hermes home or secret, also when Hermes itself is importable (its venv); tests that need a
# records folder or credentials patch these for themselves.
tools.data_dir = lambda: None
tools.secret_value = lambda name: None
SECRET = va.vb.generate_secret().hex()          # throwaway
# OWNER has a real owner token's prefix (vpo_), so a test that finds no 'vpo_' in what join printed finds no token.
PID, OWNER, SERVER = 'vp_aaaaaaaaaaaa', 'vpo_owner-token-throwaway', 'http://127.0.0.1:9'
PINS = {'tracker_nft_id': '42' * 32, 'denomination': 'token:' + 'ab' * 32}
MANDATE = {'max_issue_per_deal': 5, 'max_accept_per_deal': 4, 'max_issue_outstanding': 40, 'auto_clear': True,
           'novate_own_debt': True, 'accept_transfers': True}
# GET /info novation of a v16 server on tracker v3.2 (circulation on): the novation constants and the tracker contract
# the core signs prior acknowledgments for (design v3.2 section 5; valun-open novation.CONTRACT).
INFO_NOVATION = {'transfer_ms': 999, 'setoff_ms': 998, 'bound_note_ms': 997, 'reserved_ms': [995, 996, 997, 998, 999],
                 'max_age_ms': 3600000, 'transfer_ttl_ms': 1200000, 'setoff_ttl_ms': 1800000, 'fallback_margin_ms': 60000,
                 'tracker_build': 'valun-circulating-credit/4', 'transfer_version': 4, 'bound_note_version': 3,
                 'prior_ack': 'basis/prior-ack/1'}


class Ctx:
    def __init__(self, **config):
        self.config = dict(config)
        self.profile_name, self.plugin_id = 'valun-test', 'valun'

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value


def parse(*argv):
    p = argparse.ArgumentParser(prog='hermes valun')
    plugin._setup_cli(p, None)
    return p.parse_args(list(argv))


class TTY(io.StringIO):
    def isatty(self):
        return True


class Server:
    """A fake Valun server: routes {(method, path): answer | callable(body, token) | ValunError}; every request logged."""

    def __init__(self, routes):
        self.routes, self.log = routes, []

    def __call__(self, client, method, path, body=None, auth=True, token=None):
        self.log.append((method, path, body, token))
        answer = self.routes.get((method, path))
        if answer is None:
            raise vc.ValunError('not_found', f'no route {method} {path}', 404)
        if callable(answer):
            answer = answer(body, token)
        if isinstance(answer, Exception):
            raise answer
        return json.loads(json.dumps(answer))

    def paths(self):
        return [(m, p) for m, p, _b, _t in self.log]


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='valun-plugin-test-')
        self.secrets = {}
        self.config_set = []
        self.patches = [mock.patch.object(tools, 'data_dir', lambda: os.path.join(self.dir, 'plugin-data')),
                        mock.patch.object(tools, 'secret_value', lambda n: self.secrets.get(n)),
                        mock.patch.object(plugin, '_hermes_config_set', lambda k, v: self.config_set.append((k, v)) or True)]
        os.makedirs(os.path.join(self.dir, 'plugin-data'))
        for p in self.patches:
            p.start()
        env = {k: v for k, v in os.environ.items() if not k.startswith('HERMES_SESSION_')}
        env['HERMES_HOME'] = os.path.join(self.dir, 'home')          # never a real Hermes home
        # 0.7.3: join writes the owner token to valun-owner-token-HANDLE.txt in the home folder when no
        # --owner-token-file is given (as valun 0.7.2's join): the home folder is this test's own, never the real one.
        self.user_home = os.path.join(self.dir, 'user')
        os.makedirs(self.user_home)
        env['HOME'] = env['USERPROFILE'] = self.user_home
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        if os.path.abspath(os.path.expanduser('~')) != os.path.abspath(self.user_home):
            self.tearDown()
            self.fail('the home folder is not the test\'s own: a join could write a real owner token file')
        self.err = ''

    def tearDown(self):
        self.env.stop()
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_cli(self, ctx, server, *argv, stdin='', tty=False, err_tty=False):
        """(exit code, stdout); what went to stderr is in self.err."""
        out, err = TTY() if tty else io.StringIO(), TTY() if err_tty else io.StringIO()
        def request(client, *a, **kw):           # a method of the client: the fake server sees which client asks
            return server(client, *a, **kw)
        with mock.patch.object(vc.Client, 'request', request), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err), mock.patch.object(sys, 'stdin', TTY(stdin) if tty else io.StringIO(stdin)):
            code = plugin._run_cli(parse(*argv), ctx)
        self.err = err.getvalue()
        return code, out.getvalue()

    def default_token_file(self, handle='s1'):
        return os.path.join(self.user_home, f'valun-owner-token-{handle}.txt')

    def assertTokenOnlyIn(self, path, out):
        """The owner token is in the file at `path` (as join writes it), and nowhere in what join printed."""
        with open(path, encoding='utf-8') as f:
            self.assertEqual(f.read(), OWNER + '\n')
        self.assertNotIn('vpo_', out)
        self.assertNotIn('vpo_', self.err)

    def records(self):
        with open(os.path.join(self.dir, 'plugin-data', tools.RECORDS_FILE), encoding='utf-8') as f:
            return json.load(f)


def join_routes(test, owner=None, put=None, enroll=None, sandbox=True):
    joined = {'participant_id': PID, 'owner_token': OWNER, 'enrollment_code': 'enroll-code-throwaway',
              'sandbox_only': sandbox, 'note': 'Sandbox only: you can deal with the sandbox agents for a week.' if sandbox else None}
    return {('POST', '/pilot/v1/join-sandbox' if sandbox else '/pilot/v1/join'): joined,
            ('POST', '/pilot/v1/enroll'): enroll or {'network': 'valun-pilot-1', 'agent_token': 'agent-token-throwaway'},
            ('GET', '/pilot/v1/me'): {'participant': {'id': PID, 'handle': 's1'}},
            ('GET', '/pilot/v1/info'): dict(PINS, network='valun-pilot-1', unit='PC', features=['bound_notes/1', 'circulation/1'],
                                            novation=dict(INFO_NOVATION)),
            ('GET', '/pilot/v1/owner'): owner or {'participant': {'id': PID}, 'mandate': dict(MANDATE)},
            ('PUT', '/pilot/v1/owner/mandate'): put or {'mandate': dict(MANDATE)}}


class Join(Base):
    def test_sandbox_join_turns_consents_on_and_pins_the_tracker(self):
        ctx = Ctx()
        server = Server(join_routes(self))
        code, out = self.run_cli(ctx, server, 'join', '--sandbox', '--handle', 's1', '--operator', 'Tester', '--server', SERVER)
        self.assertEqual(code, 0, out)
        self.assertEqual(ctx.config['process_consents'], True)
        self.assertEqual({k: ctx.config[f'consents.{k}'] for k in tools.CONSENT_ROLES},
                         {k: va.SANDBOX_CONSENT_POLICY[k] for k in tools.CONSENT_ROLES})
        self.assertEqual((ctx.config['participant_id'], ctx.config['server_url'], ctx.config['confirm_commitments']), (PID, SERVER, True))
        self.assertEqual([k for k, _v in self.config_set], ['VALUN_SIGNING_SECRET', 'VALUN_AGENT_TOKEN'])
        self.assertRegex(self.config_set[0][1], r'^[0-9a-f]{64}$')
        self.assertEqual(self.records()['pins'], PINS)
        self.assertEqual(server.log[-1][:3], ('PUT', '/pilot/v1/owner/mandate', {'approve_above': 5}))
        self.assertEqual(server.log[-1][3], OWNER)
        # 0.7.3: no --owner-token-file, so join writes the token to its default file, as valun 0.7.2's join does
        self.assertTokenOnlyIn(self.default_token_file(), out)
        self.assertIn(f'Your OWNER token is in {self.default_token_file()} (no --owner-token-file was given: this is where '
                      'join writes it). Keep it private; never give it to your agent.', out)
        self.assertIn('Consents: your Valun client signs, by itself', out)
        self.assertIn('`hermes -p valun-test valun doctor`', out)
        self.assertIn('`hermes -p valun-test chat -t valun`', out)
        # The consent policy the core sees is the sandbox one: the store composes it from the config the join wrote.
        store = tools.HermesStore(ctx)
        self.assertEqual(store.state['consent_policy'], va.SANDBOX_CONSENT_POLICY)

    def test_invite_join_in_console_mode_holds_commitments_in_the_console(self):
        ctx = Ctx()
        server = Server(join_routes(self, sandbox=False))
        code, out = self.run_cli(ctx, server, 'join', '--invite-from-stdin', '--handle', 'i1', '--operator', 'Tester',
                                 '--server', SERVER, '--approvals', 'console', stdin='vpi_throwaway\n')
        self.assertEqual(code, 0, out)
        self.assertEqual(server.log[0][2]['invite'], 'vpi_throwaway')
        self.assertEqual(server.log[-1][:3], ('PUT', '/pilot/v1/owner/mandate', {'approve_above': 0, 'review_deliveries': True}))
        self.assertIs(ctx.config['confirm_commitments'], False)
        self.assertNotIn('process_consents', ctx.config, 'decision D-2 g: off unless the owner turns it on')
        self.assertEqual({k: ctx.config[f'consents.{k}'] for k in tools.CONSENT_ROLES},
                         {k: va.INVITE_CONSENT_POLICY[k] for k in tools.CONSENT_ROLES})
        self.assertIn('your Valun client signs none by itself (process_consents off)', out)
        self.assertNotIn('consent_policy', tools.HermesStore(ctx).state)

    def test_a_429_at_the_mandate_step_keeps_the_owner_token_and_hermes_asking(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        seen = []

        def owner(body, token):
            seen.append(os.path.exists(token_file))          # the token was handed over before this step
            return vc.ValunError('rate_limited', 'too many requests', 429)
        ctx = Ctx()
        server = Server(join_routes(self, owner=owner))
        code, out = self.run_cli(ctx, server, 'join', '--sandbox', '--handle', 's1', '--operator', 'Tester', '--server', SERVER,
                                 '--approvals', 'console', '--owner-token-file', token_file)
        self.assertEqual(code, 1, out)
        self.assertEqual(seen, [True])
        with open(token_file, encoding='utf-8') as f:
            self.assertEqual(f.read(), OWNER + '\n')
        self.assertIs(ctx.config['confirm_commitments'], True, 'Hermes keeps asking: the console holds nothing yet')
        self.assertNotIn(('PUT', '/pilot/v1/owner/mandate'), server.paths())
        self.assertIn('Your owner mandate could not be set (rate_limited)', out)
        self.assertIn('`hermes -p valun-test config set plugins.entries.valun.settings.confirm_commitments false`', out)
        self.assertIn('Then run `hermes -p valun-test valun doctor`.', out)
        self.assertEqual(ctx.config['participant_id'], PID)

    def test_a_failed_put_also_says_what_is_not_set(self):
        ctx = Ctx()
        server = Server(join_routes(self, put=vc.ValunError('rate_limited', 'too many requests', 429)))
        code, out = self.run_cli(ctx, server, 'join', '--sandbox', '--handle', 's1', '--operator', 'Tester', '--server', SERVER,
                                 '--approvals', 'none')
        self.assertEqual(code, 1)
        self.assertIn('approve_above in your owner mandate was not set', out)
        self.assertTokenOnlyIn(self.default_token_file(), out)
        self.assertIs(ctx.config['confirm_commitments'], False)

    def test_an_owner_answer_for_another_participant_sets_nothing(self):
        ctx = Ctx()
        server = Server(join_routes(self, owner={'participant': {'id': 'vp_bbbbbbbbbbbb'}, 'mandate': dict(MANDATE)}))
        code, out = self.run_cli(ctx, server, 'join', '--sandbox', '--handle', 's1', '--operator', 'Tester', '--server', SERVER,
                                 '--approvals', 'console')
        self.assertEqual(code, 1)
        self.assertIn('(not_owner)', out)
        self.assertNotIn(('PUT', '/pilot/v1/owner/mandate'), server.paths())
        self.assertIs(ctx.config['confirm_commitments'], True)

    def test_a_failed_enrolment_names_the_server_and_the_participant(self):
        ctx = Ctx()
        server = Server(join_routes(self, enroll=vc.ValunError('bad_code', 'the enrollment code expired', 400)))
        code, out = self.run_cli(ctx, server, 'join', '--sandbox', '--handle', 's1', '--operator', 'Tester', '--server', SERVER)
        self.assertEqual(code, 1)
        self.assertTokenOnlyIn(self.default_token_file(), out)
        self.assertIn(f'`hermes -p valun-test valun enroll --server {SERVER} --participant {PID}`', out)
        self.assertNotIn('participant_id', ctx.config)
        self.assertEqual(self.config_set, [])

    # ----- 0.7.3 (fix inbox #174, as #167 for valun 0.7.2's join): the owner token file is checked before anything is
    # sent, and the owner token is never printed on stdout -----
    def refused(self, ctx, server, out, *, why):
        """Join refused before anything was sent: exit 2 is checked by the caller; here, nothing sent, nothing set,
        no token printed, the reason, and the same join to run with a new FILE (a command the parser takes)."""
        self.assertEqual(server.log, [], 'nothing was sent')
        self.assertEqual((ctx.config, self.config_set), ({}, []))
        self.assertNotIn('vpo_', out)
        self.assertNotIn('vpo_', self.err)
        self.assertTrue(out.startswith('Nothing was sent: '), out)
        self.assertIn(why, out)
        self.assertTrue(out.rstrip().endswith("(FILE: a new file for this join's owner token)."), out)
        again = re.search(r'run this join again: `hermes -p valun-test valun (join [^`]*)`', out)
        self.assertIsNotNone(again, out)
        a = parse(*shlex.split(again.group(1)))
        self.assertEqual((a.sandbox, a.handle, a.operator, a.server, a.owner_token_file), (True, 's1', 'Tester', SERVER, 'FILE'))
        return a

    def test_an_existing_owner_token_file_is_refused_before_anything_is_sent(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        with open(token_file, 'w', encoding='utf-8') as f:
            f.write('another participant\'s owner token\n')
        ctx, server = Ctx(), Server(join_routes(self))
        code, out = self.join(ctx, server, '--owner-token-file', token_file)
        self.assertEqual(code, 2, out)
        self.refused(ctx, server, out, why=f'{token_file} exists already, and join never replaces a file: it may hold '
                                           'another owner token. Name a new file, one that does not exist yet')
        with open(token_file, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'another participant\'s owner token\n')

    def test_a_symlink_at_the_token_file_is_refused_even_a_broken_one(self):
        kept = os.path.join(self.dir, 'target.txt')
        with open(kept, 'w', encoding='utf-8') as f:
            f.write('kept\n')
        for target in (os.path.join(self.dir, 'missing-target.txt'), kept):
            link = os.path.join(self.dir, 'owner-link.txt')
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError) as e:       # Windows without the symlink privilege
                self.skipTest(f'cannot make a symlink here ({e})')
            try:
                ctx, server = Ctx(), Server(join_routes(self))
                code, out = self.join(ctx, server, '--owner-token-file', link)
                self.assertEqual(code, 2, out)
                self.refused(ctx, server, out, why=f'{link} exists already, and join never replaces a file')
                self.assertTrue(os.path.islink(link))
                if os.path.exists(target):
                    with open(target, encoding='utf-8') as f:
                        self.assertEqual(f.read(), 'kept\n')
                else:
                    self.assertFalse(os.path.lexists(target), 'nothing was written through the broken link')
            finally:
                os.unlink(link)

    def test_a_missing_folder_or_one_that_is_not_a_folder_is_refused(self):
        not_a_folder = os.path.join(self.dir, 'a-file')
        with open(not_a_folder, 'w', encoding='utf-8') as f:
            f.write('x\n')
        for folder in (os.path.join(self.dir, 'no-such-folder'), not_a_folder):
            token_file = os.path.join(folder, 'owner.txt')
            ctx, server = Ctx(), Server(join_routes(self))
            code, out = self.join(ctx, server, '--owner-token-file', token_file)
            self.assertEqual(code, 2, out)
            self.refused(ctx, server, out, why=f'the folder of {token_file}, {folder}, does not exist, so your owner token '
                                               'could not be written there. Name a new file in a folder that exists')
            self.assertFalse(os.path.isdir(os.path.join(self.dir, 'no-such-folder')))

    def test_a_folder_that_cannot_take_a_new_file_is_refused(self):
        folder = os.path.join(self.dir, 'read-only')
        os.makedirs(folder)
        token_file = os.path.join(folder, 'owner.txt')
        real = plugin.vh.open_new_private       # the core's home.py: join's probe of the folder, and its file

        def denied(path):                       # this user may list the folder but not add a file to it
            if os.path.dirname(os.path.abspath(path)) == os.path.abspath(folder):
                raise PermissionError(13, 'Permission denied', path)
            return real(path)
        cases = ['probe denied']
        if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() != 0:
            cases.append('mode 0500')           # and the real thing, where a folder mode is a permission
        for case in cases:
            if case == 'mode 0500':
                os.chmod(folder, 0o500)
            try:
                with mock.patch.object(plugin.vh, 'open_new_private', denied) if case == 'probe denied' else \
                        contextlib.nullcontext():
                    ctx, server = Ctx(), Server(join_routes(self))
                    code, out = self.join(ctx, server, '--owner-token-file', token_file)
            finally:
                os.chmod(folder, 0o700)
            self.assertEqual(code, 2, (case, out))
            self.refused(ctx, server, out, why=f'this user may not create a file in {folder} (Permission denied), where '
                                               f'{token_file} would go. Name a new file in a folder you can write')
            self.assertEqual(os.listdir(folder), [], 'the probe file is gone, and no token file was made')

    # ----- the review of 0.7.3: the token file itself is made, written and removed before the join is sent (valun
    # 0.7.2's join probes only its folder, so a name the folder refuses, or a full disk, failed only after the join) -----
    def cannot_be_written(self, token_file, why_word, **kw):
        ctx, server = Ctx(), Server(join_routes(self))
        before = sorted(os.listdir(self.dir))
        code, out = self.join(ctx, server, '--owner-token-file', token_file, **kw)
        self.assertEqual(code, 2, out)
        self.refused(ctx, server, out, why=f'{token_file} cannot be written as a new file (')
        self.assertIn('), so your owner token could not be written there. Name another new file, and run this join '
                      'again: ', out)
        if why_word:
            self.assertIn(why_word, out)
        self.assertEqual(sorted(os.listdir(self.dir)), before, 'nothing is left behind: no probe, no token file')
        return out

    def test_a_name_the_folder_refuses_is_refused_before_anything_is_sent(self):
        names = [os.path.join(self.dir, 'x' * 300 + '.txt'),            # longer than any file system takes a name
                 os.path.join(self.dir, 'new-folder') + os.sep]          # a final separator: a folder, not a file
        if os.name == 'nt':                                               # characters Windows takes in no file name
            names += [os.path.join(self.dir, n) for n in ('tok?.txt', 'a|b.txt', 'tok"x.txt', 'tok<1>.txt')]
        for token_file in names:
            with self.subTest(token_file=token_file[-24:]):
                self.cannot_be_written(token_file, None)
                self.assertFalse(os.path.isdir(os.path.join(self.dir, 'new-folder')))

    def test_a_disk_with_no_room_for_the_token_is_refused_before_anything_is_sent(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        with mock.patch.object(plugin.os, 'write', side_effect=OSError(28, 'No space left on device')):
            self.cannot_be_written(token_file, '(No space left on device)')
        self.assertFalse(os.path.lexists(token_file))
        # a write that takes nothing is no room either
        with mock.patch.object(plugin.os, 'write', return_value=0):
            self.cannot_be_written(token_file, 'cannot be written as a new file (')
        # a file system that cannot flush a file to disk is not refused for that (EINVAL), a failing disk is (EIO)
        with mock.patch.object(plugin.os, 'fsync', side_effect=OSError(22, 'Invalid argument')):
            self.assertIsNone(plugin._token_path_refused(token_file))
        with mock.patch.object(plugin.os, 'fsync', side_effect=OSError(5, 'Input/output error')):
            self.assertEqual(plugin._token_path_refused(token_file), 'Input/output error')
        self.assertFalse(os.path.lexists(token_file))
        code, out = self.join(Ctx(), Server(join_routes(self)), '--owner-token-file', token_file)
        self.assertEqual(code, 0, out)
        self.assertTokenOnlyIn(token_file, out)

    def test_the_check_writes_room_for_the_token_and_removes_the_file(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        written, real_write = [], os.write

        def write(fd, data):
            written.append(len(data))
            return real_write(fd, data)
        with mock.patch.object(plugin.os, 'write', write):
            self.assertIsNone(plugin._token_path_refused(token_file))
        self.assertEqual(sum(written), plugin.TOKEN_ROOM)
        self.assertGreater(plugin.TOKEN_ROOM, len(OWNER) + 2)
        self.assertFalse(os.path.lexists(token_file))

    def test_a_file_that_is_not_a_regular_file_is_refused(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        real_fstat = os.fstat

        def fstat(fd):                           # what was made there is not a file (a device, say)
            st = real_fstat(fd)
            return os.stat_result((0o020600,) + tuple(st)[1:])
        with mock.patch.object(plugin.os, 'fstat', fstat):
            ctx, server = Ctx(), Server(join_routes(self))
            code, out = self.join(ctx, server, '--owner-token-file', token_file)
        self.assertEqual(code, 2, out)
        self.refused(ctx, server, out, why=f'{token_file} cannot be written as a new file (it is not a regular file)')

    @unittest.skipUnless(os.name == 'nt', 'Windows permissions')
    def test_on_windows_the_token_file_is_owner_only_whatever_its_folder_allows(self):
        """The review of 0.7.3: the plugin's token file had its folder's permissions on Windows (an agent sandbox group
        could change one in %TEMP%), where valun 0.7.2's join sets an access list. The core's home.py is now in the
        plugin: the file lets in only this user (named by its SID), SYSTEM and Administrators."""
        import subprocess
        vh = plugin.vh
        folder = os.path.join(self.dir, 'shared')
        os.makedirs(folder)
        subprocess.run(['icacls', folder, '/grant', '*S-1-5-32-545:(OI)(CI)M'], capture_output=True, check=True)
        token_file = os.path.join(folder, 'owner.txt')
        code, out = self.join(Ctx(), Server(join_routes(self)), '--owner-token-file', token_file)
        self.assertEqual(code, 0, out)
        self.assertTokenOnlyIn(token_file, out)
        acl = subprocess.run(['icacls', token_file], capture_output=True, text=True, check=True).stdout
        self.assertNotIn('(I)', acl, f'nothing inherited from its folder: {acl}')
        canonical = lambda sddl: re.sub(r';;;([^;)]+)\)', lambda m: f';;;{vh.sid_string(m.group(1))})', sddl)  # noqa: E731
        self.assertTrue(canonical(vh._sddl_of(token_file)).endswith(canonical(vh.private_sddl(vh._user_sid()))),
                        vh._sddl_of(token_file))
        self.assertEqual((vh.others_with_access(token_file), plugin._not_private_note(token_file)), ([], ''))
        self.assertNotIn('Windows could not make that file private', out)
        # a file that takes its folder's permissions is named so, with whom else they let in
        plain = os.path.join(folder, 'plain.txt')
        with open(plain, 'w', encoding='utf-8') as f:
            f.write('x')
        self.assertEqual(vh.others_with_access(plain), ['S-1-5-32-545'])
        self.assertIn('Windows could not make that file private (it can also be opened by ', plugin._not_private_note(plain))
        self.assertTrue(plugin._not_private_note(plain).endswith('whom its folder lets in), so move it somewhere only '
                                                                 'you can open.'))

    def test_no_token_file_given_refuses_an_existing_default_file(self):
        default = self.default_token_file()
        with open(default, 'w', encoding='utf-8') as f:
            f.write('the owner token of an earlier join as s1\n')
        ctx, server = Ctx(), Server(join_routes(self))
        code, out = self.join(ctx, server)
        self.assertEqual(code, 2, out)
        a = self.refused(ctx, server, out, why=f'{default} exists already (join writes your owner token there when no '
                                               '--owner-token-file is given), and join never replaces a file')
        self.assertEqual(a.approvals, 'conversation')
        with open(default, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'the owner token of an earlier join as s1\n')

    def test_no_token_file_given_writes_the_default_file_and_prints_no_token(self):
        ctx, server = Ctx(), Server(join_routes(self))
        code, out = self.join(ctx, server)
        self.assertEqual(code, 0, out)
        self.assertEqual(server.paths()[0], ('POST', '/pilot/v1/join-sandbox'))
        self.assertTokenOnlyIn(self.default_token_file(), out)
        if os.name != 'nt':
            self.assertEqual(os.stat(self.default_token_file()).st_mode & 0o777, 0o600)
        self.assertEqual(os.listdir(self.user_home), ['valun-owner-token-s1.txt'], 'no probe file is left behind')

    def test_the_join_to_run_again_keeps_what_was_given(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        with open(token_file, 'w', encoding='utf-8') as f:
            f.write('kept\n')
        ctx, server = Ctx(), Server(join_routes(self, sandbox=False))
        code, out = self.run_cli(ctx, server, 'join', '--invite-from-stdin', '--handle', 'i1', '--operator', 'Ann Lee',
                                 '--about', 'reviews, 50% off', '--services', 'PR reviews', '--approvals', 'console',
                                 '--direct-tools', '--server', SERVER, '--owner-token-file', token_file, stdin='vpi_throwaway\n')
        self.assertEqual(code, 2, out)
        self.assertEqual(server.log, [])
        again = re.search(r'run this join again: `hermes -p valun-test valun (join [^`]*)`( \(with the same [^)]*\))?', out)
        self.assertEqual(again.group(2), ' (with the same --about as before)')     # a % is not one word in Windows cmd
        a = parse(*shlex.split(again.group(1)))
        self.assertEqual((a.sandbox, a.invite_from_stdin, a.handle, a.operator, a.services, a.about, a.approvals,
                          a.direct_tools, a.server, a.owner_token_file),
                         (False, True, 'i1', 'Ann Lee', 'PR reviews', '', 'console', True, SERVER, 'FILE'))
        self.assertNotIn('vpi_throwaway', out)

    def test_a_file_that_appears_after_the_check_gets_a_new_file_beside_it(self):
        token_file = os.path.join(self.dir, 'owner.txt')

        def appears(body, token):                # another join writes that file while this one is on its way
            with open(token_file, 'w', encoding='utf-8') as f:
                f.write('kept\n')
            return join_routes(self)[('POST', '/pilot/v1/join-sandbox')]
        routes = join_routes(self)
        routes[('POST', '/pilot/v1/join-sandbox')] = appears
        ctx = Ctx()
        code, out = self.join(ctx, Server(routes), '--owner-token-file', token_file)
        self.assertEqual(code, 1, 'a script relying on the file notices')
        beside = os.path.join(self.dir, 'owner.1.txt')
        self.assertTokenOnlyIn(beside, out)
        with open(token_file, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'kept\n')
        self.assertIn(f'{token_file} appeared after join checked it, and join never replaces a file', out)
        self.assertTrue(out.rstrip().endswith(f'Exit code 1: {token_file} was not written: your owner token is in '
                                              f'{os.path.abspath(beside)}.'), out)
        self.assertEqual(ctx.config['participant_id'], PID, 'the other steps were done')

    def test_a_token_file_that_cannot_be_written_never_shows_the_token_on_stdout(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        with mock.patch.object(plugin.os, 'fdopen', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(OSError):
                plugin._write_new_private(token_file, 'x\n')
        self.assertFalse(os.path.exists(token_file), 'the file this call created is removed')
        # The folder took the probe, then the write failed (the disk filled up): no file holds the token.
        for err_tty in (False, True):
            with mock.patch.object(plugin, '_write_new_private', side_effect=OSError(28, 'No space left on device')):
                code, out = self.join(Ctx(), Server(join_routes(self)), '--owner-token-file', token_file, err_tty=err_tty)
            self.assertEqual(code, 1)
            self.assertNotIn('vpo_', out)
            self.assertIn(f'Cannot write {token_file} (No space left on device).', out)
            self.assertTrue(out.rstrip().endswith(f'Exit code 1: {token_file} was not written.'), out)
            if err_tty:          # a person's terminal: shown there once, on stderr only
                self.assertIn('  ' + OWNER + '\n', self.err)
                self.assertIn('Your owner token was shown once on this terminal (stderr), never on stdout', out)
            else:                # an agent's pipe, say: shown nowhere
                self.assertNotIn('vpo_', self.err)
                self.assertIn('Your owner token was not shown either: stderr is not a terminal here', out)
                self.assertIn(f'nobody can sign in to the owner console ({SERVER}/pilot#owner) for {PID}', out)

    def test_the_help_says_where_the_token_goes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            parse('join', '--help')
        text = ''.join(out.getvalue().split())           # argparse wraps lines, also after a hyphen
        for words in ('without it, valun-owner-token-HANDLE.txt in your home folder', 'The token is never printed on stdout',
                      'only you, SYSTEM and Administrators can open it: created 0600, and on Windows with an access list',
                      'Join refuses before anything is sent (exit 2) when that file exists already',
                      'or the file cannot be written there (join makes it, writes it and removes it again to check)',
                      'never an honor of a guarantee call'):
            self.assertIn(''.join(words.split()), text)

    def test_a_joined_profile_does_not_join_again(self):
        code, out = self.run_cli(Ctx(participant_id=PID), Server({}), 'join', '--sandbox', '--handle', 's1', '--operator', 'T')
        self.assertEqual(code, 1)
        self.assertIn('already connected', out)

    # ----- decision D-3 c: the owner token first, whatever fails after the join answer -----
    def join(self, ctx, server, *extra, **kw):
        return self.run_cli(ctx, server, 'join', '--sandbox', '--handle', 's1', '--operator', 'Tester', '--server', SERVER,
                            *extra, **kw)

    def test_the_owner_token_is_handed_over_before_enrolment(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        seen = []

        def enroll(body, token):
            seen.append((os.path.exists(token_file), os.path.exists(self.default_token_file())))
            return {'network': 'valun-pilot-1', 'agent_token': 'agent-token-throwaway'}
        code, out = self.join(Ctx(), Server(join_routes(self, enroll=enroll)), '--owner-token-file', token_file)
        self.assertEqual(code, 0, out)
        self.assertEqual(seen, [(True, False)], 'the token file was written before the enrolment request')
        self.assertTokenOnlyIn(token_file, out)
        code, out = self.join(Ctx(), Server(join_routes(self, enroll=enroll)))       # no --owner-token-file: the default
        self.assertEqual(seen[1:], [(True, True)])
        code, out = self.join(Ctx(), Server(join_routes(self, enroll=vc.ValunError('rate_limited', 'slow down', 429))),
                              '--owner-token-file', os.path.join(self.dir, 'owner2.txt'))
        self.assertLess(out.index('Your OWNER token is in'), out.index("this agent's key could not be registered"))
        self.assertTokenOnlyIn(os.path.join(self.dir, 'owner2.txt'), out)

    def test_an_answer_without_an_owner_token_writes_nothing(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        for bad in ('absent', None, '', 123):
            routes = join_routes(self)
            answer = routes[('POST', '/pilot/v1/join-sandbox')]
            if bad == 'absent':
                del answer['owner_token']
            else:
                answer['owner_token'] = bad
            ctx, server = Ctx(), Server(routes)
            code, out = self.join(ctx, server, '--owner-token-file', token_file)
            self.assertEqual(code, 1, bad)
            self.assertEqual(out.strip(), 'Joining failed (bad_response): the server did not answer an owner token.', bad)
            self.assertEqual(server.paths(), [('POST', '/pilot/v1/join-sandbox')], bad)
            self.assertFalse(os.path.exists(token_file), bad)
            self.assertEqual((ctx.config, self.config_set), ({}, []), bad)

    def test_a_settings_write_that_fails_still_leaves_the_token_handed_over(self):
        class Failing(Ctx):
            def set_config(self, key, value):
                if key == 'participant_id':
                    raise PermissionError(13, 'Permission denied', 'config.yaml')
                super().set_config(key, value)
        ctx = Failing()
        code, out = self.join(ctx, Server(join_routes(self)))
        self.assertEqual(code, 1)
        self.assertLess(out.index('Your OWNER token is in'), out.index('Joining stopped (PermissionError)'))
        self.assertTokenOnlyIn(self.default_token_file(), out)
        self.assertIn(f'Your participant {PID} exists, and your owner token was handed over above. Not done: ', out)
        self.assertIn(f'`hermes -p valun-test config set plugins.entries.valun.settings.server_url {SERVER}`', out)
        self.assertIn(f'`hermes -p valun-test config set plugins.entries.valun.settings.participant_id {PID}`', out)
        self.assertIn('Then run `hermes -p valun-test valun doctor`.', out)
        self.assertNotIn('process_consents', ctx.config, 'a join stopped early leaves the client signing nothing')
        self.assertNotIn('Traceback', out)

    def test_an_unexpected_error_in_the_mandate_step_keeps_hermes_asking(self):
        ctx = Ctx()
        code, out = self.join(ctx, Server(join_routes(self, owner=lambda body, token: RuntimeError('boom'))),
                              '--approvals', 'console')
        self.assertEqual(code, 1)
        self.assertIn('Joining stopped (RuntimeError): boom', out)
        self.assertIn("Not done: your owner mandate's approve_above (with --approvals console also review_deliveries)", out)
        self.assertIs(ctx.config['confirm_commitments'], True)
        self.assertTokenOnlyIn(self.default_token_file(), out)

    def test_an_answer_this_client_cannot_take_says_the_server_may_hold_the_key(self):
        ctx = Ctx()
        code, out = self.join(ctx, Server(join_routes(self, enroll={'agent_token': 'agent-token-throwaway'})))  # no network
        self.assertEqual(code, 1)
        # The 0.5 core checks the answer's network before it stores the key (wrong_network); 0.4's raised KeyError.
        self.assertIn("and the server may hold this agent's key, but the key could not be stored here (wrong_network)", out)
        self.assertIn('revoke this agent, which gives a new enrollment code (a new code is refused while a key is '
                      'registered); if the console shows no agent key, issue an enrollment code instead', out)
        self.assertIn(f'`hermes -p valun-test valun enroll --server {SERVER} --participant {PID}`', out)
        self.assertTokenOnlyIn(self.default_token_file(), out)
        self.assertEqual((self.config_set, ctx.config), ([], {}))

    def test_credentials_hermes_cannot_store_name_the_server_and_the_participant(self):
        with mock.patch.object(plugin, '_hermes_config_set', lambda k, v: False):
            code, out = self.join(Ctx(), Server(join_routes(self)))
        self.assertEqual(code, 1)
        self.assertIn('Hermes could not store the agent credentials, so this profile is not connected.', out)
        self.assertIn('revoke this agent, which gives a new enrollment code', out)
        self.assertIn(f'`hermes -p valun-test valun enroll --server {SERVER} --participant {PID}`', out)

    def test_join_sets_the_consent_cap(self):
        ctx = Ctx()
        self.assertEqual(self.join(ctx, Server(join_routes(self)))[0], 0)
        self.assertEqual(ctx.config['consents.max_PC'], va.SANDBOX_CONSENT_POLICY['max_PC'])
        self.assertEqual(tools.HermesStore(ctx).state['consent_policy'], va.SANDBOX_CONSENT_POLICY)

    def test_every_command_join_prints_names_this_profile_and_exists(self):
        with open(os.path.join(ROOT, 'plugin.yaml'), encoding='utf-8') as f:
            schema = set(re.findall(r'^  ([a-z_][a-zA-Z_.]*):\s*$', f.read().split('config_schema:', 1)[1], re.M))
        invited = self.run_cli(Ctx(), Server(join_routes(self, sandbox=False)), 'join', '--invite-from-stdin', '--handle', 'i1',
                               '--operator', 'Tester', '--server', SERVER, stdin='vpi_throwaway\n')
        self.assertEqual(invited[0], 0, invited[1])
        outs = [self.join(Ctx(), Server(join_routes(self)))[1], invited[1],
                self.join(Ctx(), Server(join_routes(self, enroll=vc.ValunError('bad_code', 'expired', 400))))[1],
                self.join(Ctx(), Server(join_routes(self, put=vc.ValunError('rate_limited', 'slow', 429))),
                          '--approvals', 'console')[1],
                self.join(Ctx(), Server(join_routes(self, enroll={'agent_token': 'x'})))[1]]
        commands = {c for out in outs for c in re.findall(r'`(hermes [^`]*)`', out)}
        self.assertGreaterEqual(len(commands), 7, commands)
        for cmd in commands:
            words = cmd.split()
            self.assertEqual(words[:3], ['hermes', '-p', 'valun-test'], cmd)
            if words[3] == 'valun':
                parse(*words[4:])                    # exits (fails the test) on a command that does not exist
            elif words[3] == 'config':
                self.assertEqual(words[4], 'set', cmd)
                key = words[5]
                if key.startswith('plugins.entries.valun.settings.'):
                    self.assertIn(key[len('plugins.entries.valun.settings.'):], schema, cmd)
                else:
                    self.assertIn(key, ('tools.tool_search.enabled', 'approvals.mode'), cmd)
            else:
                self.assertEqual(words[3:], ['chat', '-t', 'valun'], cmd)


class Joined(Base):
    def setUp(self):
        super().setUp()
        self.secrets = {'VALUN_SIGNING_SECRET': SECRET, 'VALUN_AGENT_TOKEN': 'agent-token-throwaway'}
        self.ctx = Ctx(participant_id=PID, server_url=SERVER, process_consents=True,
                       **{'consents.setoff': True, 'consents.as_issuer': True, 'consents.as_new_creditor': True})

    def test_status_shows_the_consent_policy(self):
        code, out = self.run_cli(self.ctx, Server({}), 'status')
        self.assertEqual(code, 0)
        self.assertIn('consent policy:  setoff on, as_issuer on, as_new_creditor on, max_PC 50', out)
        self.assertIn('signed by your client by itself', out)
        self.ctx.config['process_consents'] = False
        code, out = self.run_cli(self.ctx, Server({}), 'status')
        self.assertIn('none signed by itself (process_consents off)', out)
        self.assertNotIn(SECRET, out)

    def test_the_commands_refuse_inside_an_agent_session(self):
        with mock.patch.dict(os.environ, {'HERMES_SESSION_ID': 'x'}):
            for argv in (('status',), ('consents', '--process'), ('attempts', 'list'), ('join', '--sandbox')):
                code, out = self.run_cli(self.ctx, Server({}), *argv)
                self.assertEqual(code, 1, argv)
                self.assertIn('Refusing', out)

    def consent_routes(self, features=('bound_notes/1', 'circulation/1')):
        good = {'kind': 'setoff', 'id': 'vs_0000000000000001', 'state': 'collecting', 'your_roles': ['member'],
                'context': {'amount': 1}, 'summary': {'your_net_change_PC': 0}, 'expires_at': '2026-11-09T01:00:00.000Z'}
        odd = {'kind': 'setoff\nPAY 99', 'id': 'vs_1\nFAKE LINE', 'state': 'collecting', 'your_roles': ['member', 'owner\n'],
               'context': {'amount': '1\nx'}, 'expires_at': 'soon\nx'}
        info = dict(PINS, network='valun-pilot-1', unit='PC', features=list(features),
                    **({'novation': dict(INFO_NOVATION)} if features else {}))      # circulation off: no novation object
        return {('GET', '/pilot/v1/info'): info,
                ('GET', '/pilot/v1/me'): {'participant': {'id': PID}, 'effective_limits': dict(MANDATE)},
                ('GET', '/pilot/v1/consents'): {'consents': [good, odd, {'kind': 'transfer', 'id': 'vt_0000000000000001', 'state': 'applied'}]}}

    def test_consents_lists_what_waits_in_checked_words_only(self):
        with mock.patch.object(vc.Client, 'process_consents') as run:
            code, out = self.run_cli(self.ctx, Server(self.consent_routes()), 'consents')
        self.assertEqual(code, 0, out)
        run.assert_not_called()
        self.assertIn('Consents waiting for your client: 2', out)
        self.assertIn('setoff vs_0000000000000001 as member, 1 PC (your net change 0 PC), open until 2026-11-09T01:00:00.000Z', out)
        self.assertIn('? ? as member', out)
        self.assertNotIn('FAKE LINE', out)
        self.assertNotIn('PAY 99', out)
        self.assertIn('`hermes -p valun-test valun consents --process`', out)

    def test_consents_process_signs_with_the_configured_policy(self):
        report = {'signed': [{'kind': 'setoff', 'id': 'vs_0000000000000001', 'role': 'member', 'amount': 1, 'your_net_change_PC': 0}],
                  'refused': [], 'waiting': []}
        for on in (True, False):
            self.ctx.config['process_consents'] = on
            with mock.patch.object(vc.Client, 'process_consents', return_value=report) as run:
                code, out = self.run_cli(self.ctx, Server(self.consent_routes()), 'consents', '--process')
            self.assertEqual(code, 0, out)
            run.assert_called_once_with(policy=va.SANDBOX_CONSENT_POLICY)
            self.assertIn('signed   setoff vs_0000000000000001 as member, 1 PC (your net change 0 PC)', out)

    def test_consents_without_circulating_credit(self):
        code, out = self.run_cli(self.ctx, Server(self.consent_routes(features=())), 'consents', '--process')
        self.assertEqual(code, 0)
        self.assertIn('does not offer circulating credit', out)

    def test_consents_needs_a_joined_profile(self):
        self.secrets = {}
        code, out = self.run_cli(self.ctx, Server({}), 'consents')
        self.assertEqual(code, 1)
        self.assertIn('`hermes -p valun-test valun join --sandbox`', out)

    def test_attempts_list_with_none(self):
        server = Server({('GET', '/pilot/v1/info'): dict(PINS, network='valun-pilot-1', unit='PC', features=[])})
        code, out = self.run_cli(self.ctx, server, 'attempts', 'list')
        self.assertEqual(code, 0, out)
        self.assertIn('records no payment attempts', out)

    def test_attempts_list_advice_quotes_the_attempt(self):
        # Post-audit: unquoted, '#1' starts a comment in bash, zsh and PowerShell, so the advice says to quote it.
        server = Server({('GET', '/pilot/v1/info'): dict(PINS, network='valun-pilot-1', unit='PC', features=[])})
        with mock.patch.object(vc.Client, 'payment_attempts', return_value=[self.plan()['attempt']]):
            code, out = self.run_cli(self.ctx, server, 'attempts', 'list')
        self.assertEqual(code, 0, out)
        self.assertIn('`hermes -p valun-test valun attempts resolve DEAL ATTEMPT --applied`', out)
        self.assertIn("ATTEMPT is '#n' in quotes (unquoted, a # starts a comment in the shell)", out)

    def test_attempts_resolve_needs_a_person_at_the_terminal(self):
        with mock.patch.object(vc.Client, 'resolve_attempt') as resolve:
            code, out = self.run_cli(self.ctx, Server({}), 'attempts', 'resolve', 'vd_0000000000000001', '#1', '--not-applied')
        self.assertEqual(code, 3)
        self.assertIn('no person is at it', out)
        resolve.assert_not_called()

    def plan(self):
        row = {'deal_id': 'vd_0000000000000001', 'ref': '#1', 'attempt_id': None, 'route': 'note', 'edge': 'aa>bb', 'prior': 0,
               'total': 2, 'timestamp': 1762650000000, 'outcome': None, 'state': 'unknown', 'paid': False, 'paid_by': None}
        return {'deal_id': 'vd_0000000000000001', 'attempt': row, 'attempts': [row], 'attempt_id': None, 'route': 'note',
                'transfer': None, 'server': False, 'local': 'note'}

    def test_attempts_resolve_asks_and_resolves_exactly_the_attempt_shown(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        with open(token_file, 'w', encoding='utf-8') as f:
            f.write(OWNER + '\n')
        plan = self.plan()
        result = {'deal_id': 'vd_0000000000000001', 'attempt_id': None, 'outcome': 'not_applied', 'server': None,
                  'local': 'note', 'matched': [plan['attempt']], 'paid': False}
        server = Server({('GET', '/pilot/v1/info'): dict(PINS, network='valun-pilot-1', unit='PC', features=[])})
        with mock.patch.object(vc.Client, 'resolution_plan', return_value=plan), \
                mock.patch.object(vc.Client, 'resolve_attempt', return_value=result) as resolve, \
                mock.patch('builtins.input', side_effect=['y']):
            code, out = self.run_cli(self.ctx, server, 'attempts', 'resolve', 'vd_0000000000000001', '#1', '--not-applied',
                                     '--owner-token-file', token_file, tty=True)
        self.assertEqual(code, 0, out)
        resolve.assert_called_once_with('vd_0000000000000001', '#1', 'not_applied', OWNER, note=None, local_only=False,
                                        expected_attempt=vc.attempt_key(plan['attempt']))
        self.assertIn('NOT APPLIED', out)
        self.assertIn('<- this one', out)
        self.assertIn("Your client's records now hold it for 1 attempt(s) of this deal.", out)
        self.assertNotIn(OWNER, out)

    def test_attempts_resolve_applied_refused_by_the_payees_edge_says_what_to_do(self):
        # valun-app SV-AUTH-1: the server answers 409 payee_edge_not_applied {payee_edge} to 'applied' while the payee's
        # edge on the tracker does not show the transfer. The core's resolve_attempt (not mocked) raises it with the
        # reason; the command says why and names the two ways on (this profile's exact --not-applied command, or the
        # operator), as `valun attempts resolve` does, and offers no resolution in the client's records only.
        deal, sid = 'vd_0000000000000001', 'vt_0123456789abcdef'
        row = dict(self.plan()['attempt'], attempt_id=sid, route='transfer')
        plan = {'deal_id': deal, 'attempt': row, 'attempts': [row], 'attempt_id': sid, 'route': 'transfer',
                'transfer': {'id': sid, 'state': 'outcome_unknown', 'timestamp': row['timestamp'], 'edge': row['edge'],
                             'total': row['total']}, 'server': True, 'local': None}
        token_file = os.path.join(self.dir, 'owner.txt')
        with open(token_file, 'w', encoding='utf-8') as f:
            f.write(OWNER + '\n')
        route = ('POST', f'/pilot/v1/owner/deals/{deal}/attempts/{sid}/resolve')
        server = Server({('GET', '/pilot/v1/info'): dict(PINS, network='valun-pilot-1', unit='PC', features=['circulation/1'],
                                                         novation=dict(INFO_NOVATION)),
                         route: vc.ValunError('payee_edge_not_applied', "the payee's edge does not show it\x1b[2J", 409,
                                              {'payee_edge': 'moved_past'})})
        with mock.patch.object(vc.Client, 'resolution_plan', return_value=plan), \
                mock.patch('builtins.input', side_effect=['y']):          # one confirmation; no second question follows
            code, out = self.run_cli(self.ctx, server, 'attempts', 'resolve', deal, '#1', '--applied',
                                     '--owner-token-file', token_file, tty=True)
        self.assertEqual(code, 1, out)
        self.assertEqual([(m, p, b, t) for m, p, b, t in server.log if (m, p) == route],
                         [('POST', route[1], {'outcome': 'applied'}, OWNER)])
        self.assertIn("The server records it as applied only if the payee's edge on the tracker shows this transfer", out)
        self.assertIn('The server did not record this payment as applied (payee_edge_not_applied, payee edge: '
                      "moved_past). Nothing was changed, at the server or in your client's records.", out)
        self.assertIn("moved past the transfer's timestamp at its earlier total", out)
        # The attempt is quoted: pasted into bash, zsh or PowerShell, an unquoted '#1 --not-applied' is a comment.
        self.assertIn(f"`hermes -p valun-test valun attempts resolve {deal} '#1' --not-applied`", out)
        self.assertNotIn(' #1 --not-applied', out)
        self.assertIn('ask the operator to look into it with the payee, and do not resolve it until then', out)
        self.assertNotIn('INCONSISTENT', out)
        self.assertNotIn("client's records only?", out)
        self.assertNotIn('\x1b', out)
        self.assertNotIn(OWNER, out)
        disk = self.records()                                       # nothing resolved, nothing recorded as paid
        self.assertEqual(disk.get('transfer_attempts') or {}, {})
        self.assertNotIn('paid_deals', disk.get('records') or {})

    def test_attempts_resolve_an_inconsistent_attempt_is_the_operators_case(self):
        # Post-audit, as `valun attempts resolve`: for a transfer the server reads as inconsistent, the plan says before
        # the question that it is the operator's case, and the payee_edge_not_applied refusal names no --not-applied
        # command (resolving it as not applied on the payee's word could pay the deal twice).
        deal, sid = 'vd_0000000000000001', 'vt_0123456789abcdef'
        row = dict(self.plan()['attempt'], attempt_id=sid, route='transfer')
        plan = {'deal_id': deal, 'attempt': row, 'attempts': [row], 'attempt_id': sid, 'route': 'transfer',
                'transfer': {'id': sid, 'state': 'inconsistent', 'timestamp': row['timestamp'], 'edge': row['edge'],
                             'total': row['total']}, 'server': True, 'local': None}
        token_file = os.path.join(self.dir, 'owner.txt')
        with open(token_file, 'w', encoding='utf-8') as f:
            f.write(OWNER + '\n')
        route = ('POST', f'/pilot/v1/owner/deals/{deal}/attempts/{sid}/resolve')
        server = Server({('GET', '/pilot/v1/info'): dict(PINS, network='valun-pilot-1', unit='PC', features=['circulation/1'],
                                                         novation=dict(INFO_NOVATION)),
                         route: vc.ValunError('payee_edge_not_applied', "the payee's edge does not show it", 409,
                                              {'payee_edge': 'moved_past'})})
        with mock.patch.object(vc.Client, 'resolution_plan', return_value=plan), \
                mock.patch('builtins.input', side_effect=['y']):
            code, out = self.run_cli(self.ctx, server, 'attempts', 'resolve', deal, '#1', '--applied',
                                     '--owner-token-file', token_file, tty=True)
        self.assertEqual(code, 1, out)
        self.assertIn('The server reads this transfer as INCONSISTENT', out)
        self.assertLess(out.index('INCONSISTENT'), out.index('<- this one'))      # in the plan, before the question
        self.assertIn('Do not resolve it until the operator has looked into it.', out)
        self.assertNotIn('--not-applied`', out)
        self.assertNotIn(OWNER, out)
        disk = self.records()
        self.assertEqual(disk.get('transfer_attempts') or {}, {})

    def test_attempts_resolve_declined_changes_nothing(self):
        with mock.patch.object(vc.Client, 'resolution_plan', return_value=self.plan()), \
                mock.patch.object(vc.Client, 'resolve_attempt') as resolve, mock.patch('builtins.input', side_effect=['n']):
            code, out = self.run_cli(self.ctx, Server({('GET', '/pilot/v1/info'): {'network': 'valun-pilot-1', 'unit': 'PC'}}),
                                     'attempts', 'resolve', 'vd_0000000000000001', '#1', '--applied', tty=True)
        self.assertEqual(code, 3)
        self.assertIn('Nothing was changed.', out)
        resolve.assert_not_called()

    def test_a_bad_note_is_refused_before_anything_is_asked(self):
        with mock.patch.object(vc.Client, 'resolution_plan') as plan, mock.patch('builtins.input') as ask:
            code, out = self.run_cli(self.ctx, Server({}), 'attempts', 'resolve', 'vd_0000000000000001', '#1', '--applied',
                                     '--note', 'x' * 301, tty=True)
        self.assertEqual(code, 1)
        plan.assert_not_called()
        ask.assert_not_called()

    def test_the_doctor_reports_unreadable_records(self):
        with open(os.path.join(self.dir, 'plugin-data', tools.RECORDS_FILE), 'w', encoding='utf-8') as f:
            f.write('{broken')
        server = Server({('GET', '/pilot/v1/info'): dict(PINS, network='valun-pilot-1', unit='PC'),
                         ('GET', '/pilot/v1/me'): {'participant': {'id': PID, 'handle': 's1', 'status': 'active'},
                                                   'mandate': dict(MANDATE)}})
        code, out = self.run_cli(self.ctx, server, 'doctor')
        self.assertEqual(code, 1)
        self.assertIn('cannot read its records', out)


if __name__ == '__main__':
    unittest.main()
