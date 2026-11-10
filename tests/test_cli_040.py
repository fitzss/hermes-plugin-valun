"""0.4.0 operator commands: `hermes valun join` hands the owner token over before anything else can fail and says how to
finish (naming this Hermes profile) when the mandate step fails; `join --sandbox` turns process_consents on with the
sandbox consent policy; `hermes valun status` shows the consent policy; `hermes valun consents [--process]`; and the
owner-run `hermes valun attempts list|resolve`. Offline: a fake server, throwaway tokens, temporary folders."""
import argparse
import contextlib
import importlib.util
import io
import json
import re
import os
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
PID, OWNER, SERVER = 'vp_aaaaaaaaaaaa', 'owner-token-throwaway', 'http://127.0.0.1:9'
PINS = {'tracker_nft_id': '42' * 32, 'denomination': 'token:' + 'ab' * 32}
MANDATE = {'max_issue_per_deal': 5, 'max_accept_per_deal': 4, 'max_issue_outstanding': 40, 'auto_clear': True,
           'novate_own_debt': True, 'accept_transfers': True}


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
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_cli(self, ctx, server, *argv, stdin='', tty=False):
        out = TTY() if tty else io.StringIO()
        def request(client, *a, **kw):           # a method of the client: the fake server sees which client asks
            return server(client, *a, **kw)
        with mock.patch.object(vc.Client, 'request', request), contextlib.redirect_stdout(out), \
                mock.patch.object(sys, 'stdin', TTY(stdin) if tty else io.StringIO(stdin)):
            code = plugin._run_cli(parse(*argv), ctx)
        return code, out.getvalue()

    def records(self):
        with open(os.path.join(self.dir, 'plugin-data', tools.RECORDS_FILE), encoding='utf-8') as f:
            return json.load(f)


def join_routes(test, owner=None, put=None, enroll=None, sandbox=True):
    joined = {'participant_id': PID, 'owner_token': OWNER, 'enrollment_code': 'enroll-code-throwaway',
              'sandbox_only': sandbox, 'note': 'Sandbox only: you can deal with the sandbox agents for a week.' if sandbox else None}
    return {('POST', '/pilot/v1/join-sandbox' if sandbox else '/pilot/v1/join'): joined,
            ('POST', '/pilot/v1/enroll'): enroll or {'network': 'valun-pilot-1', 'agent_token': 'agent-token-throwaway'},
            ('GET', '/pilot/v1/me'): {'participant': {'id': PID, 'handle': 's1'}},
            ('GET', '/pilot/v1/info'): dict(PINS, network='valun-pilot-1', unit='PC', features=['bound_notes/1', 'circulation/1']),
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
        self.assertIn(OWNER, out)
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
        self.assertIn(OWNER, out)
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
        self.assertIn(OWNER, out)
        self.assertIn(f'`hermes -p valun-test valun enroll --server {SERVER} --participant {PID}`', out)
        self.assertNotIn('participant_id', ctx.config)
        self.assertEqual(self.config_set, [])

    def test_an_existing_owner_token_file_is_never_overwritten(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        with open(token_file, 'w', encoding='utf-8') as f:
            f.write('another participant\'s owner token\n')
        ctx = Ctx()
        code, out = self.run_cli(ctx, Server(join_routes(self)), 'join', '--sandbox', '--handle', 's1', '--operator', 'Tester',
                                 '--server', SERVER, '--owner-token-file', token_file)
        self.assertEqual(code, 1, 'a script relying on the file notices')
        with open(token_file, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'another participant\'s owner token\n')
        self.assertIn(OWNER, out)
        self.assertIn('was not written', out)
        self.assertEqual(ctx.config['participant_id'], PID, 'the other steps were done')

    def test_a_joined_profile_does_not_join_again(self):
        code, out = self.run_cli(Ctx(participant_id=PID), Server({}), 'join', '--sandbox', '--handle', 's1', '--operator', 'T')
        self.assertEqual(code, 1)
        self.assertIn('already connected', out)

    # ----- decision D-3 c: the owner token first, whatever fails after the join answer -----
    def join(self, ctx, server, *extra):
        return self.run_cli(ctx, server, 'join', '--sandbox', '--handle', 's1', '--operator', 'Tester', '--server', SERVER,
                            *extra)

    def test_the_owner_token_is_handed_over_before_enrolment(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        seen = []

        def enroll(body, token):
            seen.append(os.path.exists(token_file))
            return {'network': 'valun-pilot-1', 'agent_token': 'agent-token-throwaway'}
        code, out = self.join(Ctx(), Server(join_routes(self, enroll=enroll)), '--owner-token-file', token_file)
        self.assertEqual(code, 0, out)
        self.assertEqual(seen, [True], 'the token file was written before the enrolment request')
        code, out = self.join(Ctx(), Server(join_routes(self, enroll=vc.ValunError('rate_limited', 'slow down', 429))))
        self.assertLess(out.index(OWNER), out.index("this agent's key could not be registered"))

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
        self.assertLess(out.index(OWNER), out.index('Joining stopped (PermissionError)'))
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
        self.assertIn(OWNER, out)

    def test_an_answer_this_client_cannot_take_says_the_server_may_hold_the_key(self):
        ctx = Ctx()
        code, out = self.join(ctx, Server(join_routes(self, enroll={'agent_token': 'agent-token-throwaway'})))  # no network
        self.assertEqual(code, 1)
        self.assertIn("and the server may hold this agent's key, but the key could not be stored here (KeyError)", out)
        self.assertIn('revoke this agent, which gives a new enrollment code (a new code is refused while a key is '
                      'registered); if the console shows no agent key, issue an enrollment code instead', out)
        self.assertIn(f'`hermes -p valun-test valun enroll --server {SERVER} --participant {PID}`', out)
        self.assertIn(OWNER, out)
        self.assertEqual((self.config_set, ctx.config), ([], {}))

    def test_credentials_hermes_cannot_store_name_the_server_and_the_participant(self):
        with mock.patch.object(plugin, '_hermes_config_set', lambda k, v: False):
            code, out = self.join(Ctx(), Server(join_routes(self)))
        self.assertEqual(code, 1)
        self.assertIn('Hermes could not store the agent credentials, so this profile is not connected.', out)
        self.assertIn('revoke this agent, which gives a new enrollment code', out)
        self.assertIn(f'`hermes -p valun-test valun enroll --server {SERVER} --participant {PID}`', out)

    def test_a_token_file_that_cannot_be_written_shows_the_token(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        with mock.patch.object(plugin.os, 'fdopen', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(OSError):
                plugin._write_new_private(token_file, 'x\n')
        self.assertFalse(os.path.exists(token_file), 'the file this call created is removed')
        with mock.patch.object(plugin, '_write_new_private', side_effect=OSError(28, 'No space left on device')):
            code, out = self.join(Ctx(), Server(join_routes(self)), '--owner-token-file', token_file)
        self.assertEqual(code, 1)
        self.assertIn(f'Cannot write {token_file} (No space left on device), so your owner token is shown here instead.', out)
        self.assertIn('  ' + OWNER, out)
        self.assertTrue(out.rstrip().endswith(f'Exit code 1: {token_file} was not written (your owner token is shown above '
                                              'instead).'))

    def test_an_existing_owner_token_file_is_said_to_exist(self):
        token_file = os.path.join(self.dir, 'owner.txt')
        with open(token_file, 'w', encoding='utf-8') as f:
            f.write('kept\n')
        code, out = self.join(Ctx(), Server(join_routes(self)), '--owner-token-file', token_file)
        self.assertEqual(code, 1)
        self.assertIn(f'{token_file} exists already, so it was not replaced (it may hold another owner token); your owner '
                      'token is shown here instead.', out)

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
        return {('GET', '/pilot/v1/info'): dict(PINS, network='valun-pilot-1', unit='PC', features=list(features)),
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
        server = Server({('GET', '/pilot/v1/info'): dict(PINS, network='valun-pilot-1', unit='PC', features=['circulation/1']),
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
        self.assertIn(f'`hermes -p valun-test valun attempts resolve {deal} #1 --not-applied`', out)
        self.assertIn('ask the operator to look into it with the payee, and do not resolve it until then', out)
        self.assertNotIn("client's records only?", out)
        self.assertNotIn('\x1b', out)
        self.assertNotIn(OWNER, out)
        disk = self.records()                                       # nothing resolved, nothing recorded as paid
        self.assertEqual(disk.get('transfer_attempts') or {}, {})
        self.assertNotIn('paid_deals', disk.get('records') or {})

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
