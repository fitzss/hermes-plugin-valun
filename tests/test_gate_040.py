"""0.4.0: the approval hook asks the core about every valun_* tool (no list of its own), approves with the text the
core prepared and binds, blocks when preparing fails, and the handler refuses a commitment it holds no binding for.
Offline: the server is replaced, and the keys are throwaway ones."""
import importlib.util
import json
import os
import sys
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
va, vc, schemas = tools.va, tools.vc, sys.modules['valun_plugin_pkg.valun_core.schemas']
# Hermetic: never a real Hermes home or secret, also when Hermes itself is importable (its venv); tests that need a
# records folder or credentials patch these for themselves.
tools.data_dir = lambda: None
tools.secret_value = lambda name: None
SECRET = va.vb.generate_secret().hex()          # a throwaway key: these tests never reach a real server


class Ctx:
    def __init__(self, **config):
        self.config = {'confirm_commitments': True, 'participant_id': 'vp_000000000001', 'server_url': 'http://127.0.0.1:9'}
        self.config.update(config)
        self.profile_name, self.plugin_id, self.tools, self.hooks = 'valun-test', 'valun', {}, {}

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value

    def register_tool(self, name, handler, **kw):
        self.tools[name] = handler

    def register_hook(self, event, fn):
        self.hooks[event] = fn

    def register_cli_command(self, **kw):
        pass


def secrets_of(name):
    return {'VALUN_SIGNING_SECRET': SECRET, 'VALUN_AGENT_TOKEN': 'agent-token-throwaway'}.get(name)


def no_server(client, method, path, body=None, auth=True, token=None):
    raise AssertionError(f'unexpected request {method} {path}')


# Arguments for every tool of schemas.ALL, the commitments and the calls that are not (as the core classifies them).
ARGS = {
    'valun_status': [{}],
    'valun_directory': [{}, {'service': 'notary'}],
    'valun_inbox': [{}],
    'valun_deal': [{'deal_id': 'vd_0000000000000001'}],
    'valun_propose': [{'counterparty': 'sandbox-exact', 'role': 'payer', 'title': 't', 'description': 'd', 'price': 2}, {}],
    'valun_respond': [{'deal_id': 'vd_0000000000000001', 'action': a} for a in ('accept', 'counter', 'decline', 'withdraw', 'Accept', None)]
                     + [{'deal_id': 'vd_0000000000000001'}],
    'valun_deliver': [{'deal_id': 'vd_0000000000000001', 'content': 'x'}, {'deal_id': 'vd_0000000000000001', 'exact_result': True}],
    'valun_review_delivery': [{'deal_id': 'vd_0000000000000001', 'decision': d} for d in ('accept', 'dispute', 'Accept', None)]
                             + [{'deal_id': 'vd_0000000000000001', 'decision': 'accept', 'pay_with': 'sandbox-exact'}],
    'valun_set_limits': [{'max_per_deal_as_payer': 30}, {'max_per_deal_as_payer': 5}, {'goals': 'buy one brief'},
                         {'clear': ['max_per_deal_as_payer']}, {'min_price_as_payee': 1}],
}
RECORDED = {'agent_limits': {'max_issue_per_deal': 10, 'min_price_as_payee': 3}}


class GateLikeTheCore(unittest.TestCase):
    """(a) the gate gives the same classification as the core for every tool of schemas.ALL, and an unknown valun_
    tool is a commitment (approve, with the core's generic text)."""

    def setUp(self):
        tools._BOUND.clear()
        self.ctx = Ctx()
        self.prepared = []
        real_prepare = va.ValunAgent.prepare

        def prepare(agent, name, args):          # the core's prepare without its read: a known text for a known tool
            self.prepared.append(name)
            if name not in agent.handlers:
                return real_prepare(agent, name, args)
            return {'args': dict(args, pinned='yes'), 'text': f'TEXT for {name}'}
        self.patches = [mock.patch.object(va.ValunAgent, 'prepare', prepare),
                        mock.patch.object(tools, 'secret_value', secrets_of),
                        mock.patch.object(vc.Client, 'me', lambda c: dict(RECORDED)),
                        mock.patch.object(vc.Client, 'request', no_server)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        tools._BOUND.clear()

    def test_every_tool_is_classified_as_the_core_classifies_it(self):
        self.assertEqual(sorted(ARGS), sorted(t['name'] for t in schemas.ALL))
        core = va.ValunAgent(tools.HermesStore(self.ctx))
        seen = {True: 0, False: 0}
        for name in [t['name'] for t in schemas.ALL]:
            for args in ARGS[name]:
                with self.subTest(name=name, args=args):
                    commits = core.is_commitment(name, args)
                    seen[commits] += 1
                    out = plugin._gate(self.ctx, name, args)
                    if commits:
                        self.assertEqual(out['action'], 'approve')
                        self.assertEqual(out['message'], f'TEXT for {name}')
                        self.assertRegex(out['rule_key'], rf'^valun:{name}:[0-9a-f]{{16}}$')
                    else:
                        self.assertIsNone(out)
        self.assertGreater(seen[True], 10)
        self.assertGreater(seen[False], 8)

    def test_an_unknown_valun_tool_is_a_commitment(self):
        out = plugin._gate(self.ctx, 'valun_unknown_new_tool', {'x': 1})
        self.assertEqual(out['action'], 'approve')
        self.assertEqual(out['message'], va.GENERIC_TEXT.format(name='valun_unknown_new_tool'))

    def test_other_tools_and_confirm_off_are_left_alone(self):
        self.assertIsNone(plugin._gate(self.ctx, 'terminal', {'command': 'ls'}))
        self.assertIsNone(plugin._gate(self.ctx, 'web_search', {}))
        off = Ctx(confirm_commitments=False)
        for name in ('valun_propose', 'valun_respond', 'valun_review_delivery', 'valun_unknown'):
            self.assertIsNone(plugin._gate(off, name, {'deal_id': 'vd_0000000000000001', 'action': 'accept', 'decision': 'accept'}))
        self.assertEqual(self.prepared, [])
        self.assertEqual(tools._BOUND, {})

    def test_only_false_turns_the_questions_off(self):
        args = ARGS['valun_propose'][0]
        for value in (None, 0, '', 'false', 'no', [], True):
            self.assertTrue(tools.confirm_commitments(Ctx(confirm_commitments=value)), value)
            self.assertEqual(plugin._gate(Ctx(confirm_commitments=value), 'valun_propose', args)['action'], 'approve', value)
        self.assertFalse(tools.confirm_commitments(Ctx(confirm_commitments=False)))
        self.assertTrue(tools.confirm_commitments(Ctx(**{'confirm_commitments': True})))

    def test_each_approval_has_its_own_rule_key(self):
        args = ARGS['valun_propose'][0]
        keys = {plugin._gate(self.ctx, 'valun_propose', args)['rule_key'] for _ in range(5)}
        self.assertEqual(len(keys), 5)

    def test_the_commitment_list_is_gone(self):
        self.assertFalse(hasattr(plugin, 'COMMITMENT_TOOLS'))
        self.assertFalse(hasattr(plugin, '_is_commitment'))


class PrepareFails(unittest.TestCase):
    """(b) when the core cannot prepare a commitment, the gate blocks: nothing is asked and nothing is signed."""

    def setUp(self):
        tools._BOUND.clear()
        self.ctx = Ctx()

    def tearDown(self):
        tools._BOUND.clear()

    def test_a_prepare_that_raises_blocks(self):
        def prepare(agent, name, args):          # as the core's prepare raises it: approval_unbound, with its cause
            raise va.ValunAgent._unbound(vc.ValunError('unreachable', 'cannot reach http://127.0.0.1:9'))
        with mock.patch.object(va.ValunAgent, 'prepare', prepare), mock.patch.object(tools, 'secret_value', secrets_of):
            out = plugin._gate(self.ctx, 'valun_respond', {'deal_id': 'vd_0000000000000001', 'action': 'accept'})
        self.assertEqual(out['action'], 'block')
        self.assertTrue(out['message'].startswith('Valun could not read what you would sign; nothing was signed.'), out)
        self.assertIn('unreachable', out['message'])
        self.assertEqual(tools._BOUND, {}, 'nothing is bound for the handler')

    def test_the_real_core_offline_blocks_every_commitment(self):
        # The real prepare(): the deal cannot be read (nothing listens on 127.0.0.1:9), so nothing is asked.
        def down(client, method, path, body=None, auth=True, token=None):
            raise vc.ValunError('unreachable', 'cannot reach the server')
        with mock.patch.object(tools, 'secret_value', secrets_of), mock.patch.object(vc.Client, 'request', down):
            for name, args in (('valun_respond', {'deal_id': 'vd_0000000000000001', 'action': 'accept'}),
                               ('valun_review_delivery', {'deal_id': 'vd_0000000000000001', 'decision': 'accept'}),
                               ('valun_review_delivery', {'deal_id': 'vd_0000000000000001', 'decision': 'accept', 'pay_with': 'x'})):
                out = plugin._gate(self.ctx, name, args)
                self.assertEqual(out['action'], 'block', (name, args))
                self.assertTrue(out['message'].startswith(tools.BLOCK_TEXT))

    def test_a_misspelt_decision_is_blocked_with_the_cores_refusal(self):
        out = plugin._gate(self.ctx, 'valun_review_delivery', {'deal_id': 'vd_0000000000000001', 'decision': 'Accept'})
        self.assertEqual(out['action'], 'block')
        self.assertTrue(out['message'].startswith(tools.BLOCK_TEXT))
        self.assertIn('bad_decision', out['message'])

    def test_an_agent_that_cannot_be_built_blocks_commitments_only(self):
        with mock.patch.object(tools, 'agent_for', side_effect=RuntimeError('boom')):
            self.assertEqual(plugin._gate(self.ctx, 'valun_propose', {}), {'action': 'block', 'message': tools.BLOCK_TEXT})
            self.assertEqual(plugin._gate(self.ctx, 'valun_unknown', {}), {'action': 'block', 'message': tools.BLOCK_TEXT})
            self.assertIsNone(plugin._gate(self.ctx, 'valun_status', {}))
            self.assertIsNone(plugin._gate(self.ctx, 'valun_deliver', {}))

    def test_a_snapshot_that_shows_nothing_to_approve_runs_with_its_binding(self):
        # set_limits: the first read says it loosens; prepare's own read shows it does not (text None): no question, and
        # the handler signs the prepared arguments.
        ran = []

        def prepare(agent, name, args):
            return {'args': dict(args, pinned=True), 'text': None}
        with mock.patch.object(va.ValunAgent, 'prepare', prepare), mock.patch.object(tools, 'secret_value', secrets_of), \
                mock.patch.object(vc.Client, 'me', lambda c: dict(RECORDED)), \
                mock.patch.object(va.ValunAgent, 'set_limits', lambda agent, a: ran.append(a) or {'ok': True}):
            self.assertIsNone(plugin._gate(self.ctx, 'valun_set_limits', {'max_per_deal_as_payer': 30}))
            handler = tools.make_handlers(self.ctx)['valun_set_limits']
            self.assertEqual(json.loads(handler({'max_per_deal_as_payer': 30})), {'ok': True})
        self.assertEqual(ran, [{'max_per_deal_as_payer': 30, 'pinned': True}])


class UnboundCommitment(unittest.TestCase):
    """(c) with confirm_commitments on, the handler refuses a commitment it holds no binding for: nothing is signed or
    sent. The real core runs against a fake server that records every request and every signature."""

    def setUp(self):
        tools._BOUND.clear()
        self.ctx = Ctx()
        self.requests, self.signed = [], []
        test = self

        def server(client, method, path, body=None, auth=True, token=None):
            test.requests.append((method, path))
            if (method, path) == ('GET', '/pilot/v1/deals/vd_0000000000000001'):
                return {'id': 'vd_0000000000000001', 'current': {'terms_hash': 'ab' * 32}}
            if (method, path) == ('POST', '/pilot/v1/deals/vd_0000000000000001/decline'):
                return {'status': 'declined', 'next_actions': []}
            raise AssertionError(f'unexpected request {method} {path}')
        real_sign = vc.Client._sign

        def sign(client, text):
            test.signed.append(text.split(chr(10))[0])
            return real_sign(client, text)
        self.patches = [mock.patch.object(tools, 'secret_value', secrets_of), mock.patch.object(vc.Client, 'request', server),
                        mock.patch.object(vc.Client, '_sign', sign)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        tools._BOUND.clear()

    def test_an_unbound_commitment_is_refused_and_nothing_is_signed_or_sent(self):
        h = tools.make_handlers(self.ctx)
        for name, args in (('valun_respond', {'deal_id': 'vd_0000000000000001', 'action': 'accept'}),
                           ('valun_respond', {'deal_id': 'vd_0000000000000001', 'action': 'counter', 'price': 3}),
                           ('valun_review_delivery', {'deal_id': 'vd_0000000000000001', 'decision': 'accept'}),
                           ('valun_review_delivery', {'deal_id': 'vd_0000000000000001', 'decision': 'accept', 'pay_with': 'sandbox-exact'}),
                           ('valun_propose', {'counterparty': 'sandbox-exact', 'role': 'payer', 'title': 't', 'description': 'd', 'price': 2})):
            out = json.loads(h[name](dict(args)))
            self.assertEqual(out['error'], 'approval_missing', (name, args))
        self.assertEqual(self.requests, [])
        self.assertEqual(self.signed, [])

    def test_a_harmless_call_runs_without_a_binding(self):
        out = json.loads(tools.make_handlers(self.ctx)['valun_respond']({'deal_id': 'vd_0000000000000001', 'action': 'decline', 'reason': 'no'}))
        self.assertEqual(out['status'], 'declined')
        self.assertEqual(self.requests, [('GET', '/pilot/v1/deals/vd_0000000000000001'),
                                         ('POST', '/pilot/v1/deals/vd_0000000000000001/decline')])
        self.assertEqual(self.signed, ['valun-pilot/decline/1'])

    def test_a_bound_call_signs_exactly_the_binding_once(self):
        calls = []
        args = {'deal_id': 'vd_0000000000000001', 'action': 'accept'}
        tools.remember_binding('valun_respond', args, dict(args, expected_terms_hash='ab' * 32))
        def respond(agent, a):                    # as the core's respond does first: a keyless agent stops here
            agent.client()
            calls.append(a)
            return {'ok': True}
        with mock.patch.object(va.ValunAgent, 'respond', respond):
            h = tools.make_handlers(self.ctx)
            self.assertEqual(json.loads(h['valun_respond'](dict(args))), {'ok': True})
            self.assertEqual(calls, [dict(args, expected_terms_hash='ab' * 32)])
            self.assertEqual(json.loads(h['valun_respond'](dict(args)))['error'], 'approval_missing')
        self.assertEqual(len(calls), 1)

    def test_the_newest_binding_replaces_one_whose_approval_was_declined(self):
        calls = []
        args = {'deal_id': 'vd_0000000000000001', 'action': 'accept'}
        tools.remember_binding('valun_respond', args, dict(args, expected_terms_hash='h1'))   # the owner declined it
        tools.remember_binding('valun_respond', args, dict(args, expected_terms_hash='h2'))   # asked again: approved

        def respond(agent, a):
            agent.client()
            calls.append(a)
            return {'ok': True}
        with mock.patch.object(va.ValunAgent, 'respond', respond):
            h = tools.make_handlers(self.ctx)
            self.assertEqual(json.loads(h['valun_respond'](dict(args))), {'ok': True})
            self.assertEqual(json.loads(h['valun_respond'](dict(args)))['error'], 'approval_missing')
        self.assertEqual(calls, [dict(args, expected_terms_hash='h2')])

    def test_other_arguments_than_the_bound_ones_are_refused(self):
        tools.remember_binding('valun_respond', {'deal_id': 'vd_0000000000000001', 'action': 'accept'},
                               {'deal_id': 'vd_0000000000000001', 'action': 'accept', 'expected_terms_hash': 'ab' * 32})
        out = json.loads(tools.make_handlers(self.ctx)['valun_respond']({'deal_id': 'vd_0000000000000002', 'action': 'accept'}))
        self.assertEqual(out['error'], 'approval_missing')
        self.assertEqual((self.requests, self.signed), ([], []))

    def test_with_confirm_off_the_call_runs_as_given(self):
        calls = []
        off = Ctx(confirm_commitments=False)
        args = {'deal_id': 'vd_0000000000000001', 'action': 'accept'}
        def respond(agent, a):
            agent.client()
            calls.append(a)
            return {'ok': True}
        with mock.patch.object(va.ValunAgent, 'respond', respond):
            self.assertEqual(json.loads(tools.make_handlers(off)['valun_respond'](dict(args))), {'ok': True})
        self.assertEqual(calls, [args])

    def test_a_misspelt_decision_gets_the_cores_refusal(self):
        for decision in ('ok', 'Accept', None):
            out = json.loads(tools.make_handlers(self.ctx)['valun_review_delivery']({'deal_id': 'vd_0000000000000001', 'decision': decision}))
            self.assertEqual(out['error'], 'bad_decision', decision)
        self.assertEqual((self.requests, self.signed), ([], []))

    def test_a_handler_never_raises(self):
        with mock.patch.object(tools, 'agent_for', side_effect=RuntimeError('boom')):
            out = json.loads(tools.make_handlers(self.ctx)['valun_status']({}))
        self.assertEqual(out['error'], 'internal_error')


if __name__ == '__main__':
    unittest.main()
