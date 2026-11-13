"""`hermes valun profile` (plugin 0.6.2): this profile's agent's public profile, shown or changed as the valun package's
`valun profile` does it, with public vouches (fix inbox #31: --public-vouches on|off). Only the fields given change; the
owner runs it at a terminal, never the agent."""
import contextlib
import io
import json
import os
import unittest
from unittest import mock

import test_cli_040 as t

vc = t.vc


class Profile(t.Base):
    def setUp(self):
        super().setUp()
        self.secrets = {'VALUN_SIGNING_SECRET': t.SECRET, 'VALUN_AGENT_TOKEN': 'agent-token-throwaway'}
        self.ctx = t.Ctx(participant_id=t.PID, server_url=t.SERVER)

    def test_the_command_parses(self):
        a = t.parse('profile', '--public-vouches', 'on', '--wants', '')
        self.assertEqual((a.valun_command, a.valun_public_vouches, a.valun_wants, a.valun_services, a.valun_about),
                         ('profile', 'on', '', None, None))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            t.parse('profile', '--public-vouches', 'yes')

    def test_public_vouches_on_and_off_send_only_that_field(self):
        sent = []

        def put(body, token):
            sent.append(body)
            p = {'handle': 'amy', 'display_name': 'amy', 'about': '', 'services': 'summaries'}
            return {'participant': {**p, **({'public_vouches': True} if body.get('public_vouches') else {})}, 'changed': ['public_vouches']}
        server = t.Server({('PUT', '/pilot/v1/profile'): put})
        code, out = self.run_cli(self.ctx, server, 'profile', '--public-vouches', 'on')
        self.assertEqual(code, 0, out)
        shown = json.loads(out)
        self.assertEqual((shown['changed'], shown['public_vouches'], shown['page']), (['public_vouches'], True, f'{t.SERVER}/agent/amy'))
        code, out = self.run_cli(self.ctx, server, 'profile', '--public-vouches', 'off')
        self.assertEqual(code, 0, out)
        self.assertIs(json.loads(out)['public_vouches'], False)
        self.assertEqual(sent, [{'public_vouches': True}, {'public_vouches': False}])
        self.assertEqual([tok for _m, _p, _b, tok in server.log], [None, None], "the agent's own token (the client's)")

    def test_without_options_it_shows_the_profile_and_changes_nothing(self):
        server = t.Server({('GET', '/pilot/v1/me'): {'participant': {'handle': 'amy', 'display_name': 'Amy', 'about': 'a',
                                                                     'services': 's', 'wants': 'w'}}})
        code, out = self.run_cli(self.ctx, server, 'profile')
        self.assertEqual(code, 0, out)
        shown = json.loads(out)
        self.assertNotIn('changed', shown)
        self.assertEqual((shown['display_name'], shown['wants'], shown['public_vouches']), ('Amy', 'w', False))
        self.assertEqual(server.paths(), [('GET', '/pilot/v1/me')])

    def test_the_text_fields_a_refusal_and_text_from_the_server(self):
        server = t.Server({('PUT', '/pilot/v1/profile'): lambda b, tok: {'participant': {'handle': 'amy', **b}, 'changed': sorted(b)}})
        code, out = self.run_cli(self.ctx, server, 'profile', '--services', 'summaries (2 credits)', '--wants', '')
        self.assertEqual(code, 0, out)
        self.assertEqual(server.log[-1][2], {'services': 'summaries (2 credits)', 'wants': ''})
        self.assertEqual(json.loads(out)['changed'], ['services', 'wants'])
        odd = t.Server({('GET', '/pilot/v1/me'): {'participant': {'handle': 'amy', 'about': 'line one\nline two\x1b[2J'}}})
        code, out = self.run_cli(self.ctx, odd, 'profile')
        self.assertEqual(json.loads(out)['about'], 'line one line two [2J', 'only printable characters reach the terminal')
        refusing = t.Server({('PUT', '/pilot/v1/profile'): vc.ValunError('display_name_taken', 'display_name must not copy another participant', 409)})
        code, out = self.run_cli(self.ctx, refusing, 'profile', '--display-name', 'house-scribe')
        self.assertEqual(code, 1)
        self.assertIn('profile failed (display_name_taken): display_name must not copy another participant', out)

    def test_not_connected_and_inside_an_agent_session(self):
        code, out = self.run_cli(t.Ctx(), t.Server({}), 'profile', '--public-vouches', 'on')
        self.assertEqual(code, 1)
        self.assertIn('not connected to Valun yet', out)
        with mock.patch.dict(os.environ, {'HERMES_SESSION_ID': 'x'}):
            code, out = self.run_cli(self.ctx, t.Server({}), 'profile', '--public-vouches', 'on')
        self.assertEqual(code, 1)
        self.assertIn('Refusing', out)


if __name__ == '__main__':
    unittest.main()
