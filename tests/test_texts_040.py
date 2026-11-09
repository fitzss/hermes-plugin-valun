"""0.4.0 texts the owner and the agent act on: the profile a printed command names (a Hermes home Hermes calls 'custom'
is no named profile), the core's `valun join --sandbox` as this profile's Hermes command, the read run (decision D-3 a)
in what status, join and the agent's approvals text say, and join's word for whether the server holds the agent's key.
Offline: the fake server and helpers of test_cli_040, throwaway tokens, temporary folders."""
import os
import unittest
from unittest import mock

import test_cli_040 as t

plugin, tools = t.plugin, t.tools
READS = 'before valun_status and valun_inbox'


class Profiles(unittest.TestCase):
    def ctx(self, name):
        ctx = t.Ctx()
        ctx.profile_name = name
        return ctx

    def test_a_named_profile_is_named(self):
        for name in ('default', 'valun', 'valun-test', 'a_1'):
            self.assertEqual(tools.profile_name(self.ctx(name)), name)
            self.assertEqual(tools.hermes_cmd(self.ctx(name), 'doctor'), f'hermes -p {name} valun doctor')

    def test_custom_and_unusable_names_give_plain_hermes_commands(self):
        """Hermes calls a HERMES_HOME that is no named profile 'custom' (hermes_cli.profiles.get_active_profile_name):
        `hermes -p custom` would name a profile that does not exist."""
        for name in ('custom', 'Valun', '-x', 'a b', 'x' * 65, '', None, 7):
            self.assertIsNone(tools.profile_name(self.ctx(name)), name)
            self.assertEqual(tools.hermes_cmd(self.ctx(name), 'doctor'), 'hermes valun doctor', name)
            self.assertEqual(tools.setting_cmd(self.ctx(name), 'process_consents', 'true'),
                             'hermes config set plugins.entries.valun.settings.process_consents true', name)


class CoreCommands(unittest.TestCase):
    def test_join_sandbox_stays_a_sandbox_join(self):
        text = tools.hermes_words(t.Ctx(), 'the tour is for sandbox-only participants (`valun join --sandbox`)')
        self.assertEqual(text, 'the tour is for sandbox-only participants (`hermes -p valun-test valun join --sandbox`)')
        self.assertEqual(tools.hermes_words(t.Ctx(), 'run `valun join` first'), 'run `hermes -p valun-test valun join` first')


class ReadRunTexts(t.Base):
    """What the owner and the agent are told about the value-safe consents signed before valun_status and valun_inbox."""

    def test_status_says_when_consents_are_signed_with_process_consents_on(self):
        self.secrets = {'VALUN_SIGNING_SECRET': t.SECRET, 'VALUN_AGENT_TOKEN': 'agent-token-throwaway'}
        ctx = t.Ctx(participant_id=t.PID, server_url=t.SERVER, process_consents=True, **{'consents.setoff': True})
        code, out = self.run_cli(ctx, t.Server({}), 'status')
        self.assertEqual(code, 0)
        line = next(x for x in out.splitlines() if x.startswith('consents:'))
        self.assertIn('before Valun tool calls that are not read-only, at most once a minute', line)
        self.assertIn('set-offs and payments to you by transfer also ' + READS, line)
        self.assertIn('nothing runs in the background', line)

    def test_the_sandbox_join_says_that_reads_sign_set_offs(self):
        code, out = self.join_sandbox()
        self.assertEqual(code, 0, out)
        line = next(x for x in out.splitlines() if x.startswith('Consents:'))
        self.assertIn('set-offs and payments to you also ' + READS + ', at most once a minute', line)
        self.assertIn('nothing runs in the background', line)

    def test_the_agent_is_told_what_its_reads_sign(self):
        on = tools._approvals_text(t.Ctx(process_consents=True))
        self.assertIn('set-offs and payments to you also ' + READS, on)
        self.assertIn('consents_signed_now', on)
        self.assertNotIn(READS, tools._approvals_text(t.Ctx()))

    def join_sandbox(self):
        return self.run_cli(t.Ctx(), t.Server(t.join_routes(self)), 'join', '--sandbox', '--handle', 's1', '--operator',
                            'Tester', '--server', t.SERVER)


class EnrolledKeyNotStored(t.Base):
    def test_a_key_the_server_took_but_this_profile_did_not_keep_holds(self):
        """The server answered the enrolment (the client recorded the participant), then keeping the key failed: the
        server HOLDS the key, so the owner revokes the agent for a new code (no 'if the console shows no agent key')."""
        def fail(store, secret_hex, token):
            raise RuntimeError('no room')
        with mock.patch.object(plugin._MemoryStore, 'save_credentials', fail):
            code, out = self.run_cli(t.Ctx(), t.Server(t.join_routes(self)), 'join', '--sandbox', '--handle', 's1',
                                     '--operator', 'Tester', '--server', t.SERVER)
        self.assertEqual(code, 1)
        self.assertIn(f"Your participant {t.PID} was created, and the server holds this agent's key, but the key could "
                      'not be stored here (RuntimeError): no room', out)
        self.assertIn('revoke this agent, which gives a new enrollment code (a new code is refused while a key is '
                      f'registered), then run `hermes -p valun-test valun enroll --server {t.SERVER} --participant {t.PID}`',
                      out)
        self.assertNotIn('if the console shows no agent key', out)
        self.assertIn(t.OWNER, out)


if __name__ == '__main__':
    unittest.main()
