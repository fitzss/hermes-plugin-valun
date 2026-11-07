"""0.3.0: what the owner approves is what gets signed. The approval hook pins an acceptance to the terms hash it shows
the owner; the handler signs with that pinned hash, so a counter-offer that arrives in between is refused, not signed."""
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if 'valun_plugin_pkg' not in sys.modules:
    spec = importlib.util.spec_from_file_location('valun_plugin_pkg', os.path.join(ROOT, '__init__.py'), submodule_search_locations=[ROOT])
    mod = importlib.util.module_from_spec(spec)
    sys.modules['valun_plugin_pkg'] = mod
    spec.loader.exec_module(mod)
plugin = sys.modules['valun_plugin_pkg']
tools = sys.modules['valun_plugin_pkg.tools']


class Ctx:
    def __init__(self, confirm=True):
        self.confirm, self.tools, self.hooks = confirm, {}, {}

    def get_config(self, key, default=None):
        return self.confirm if key == 'confirm_commitments' else default

    def register_tool(self, name, handler, **kw):
        self.tools[name] = handler

    def register_hook(self, event, fn):
        self.hooks[event] = fn

    def register_cli_command(self, **kw):
        pass


class FakeAgent:
    """Stands in for the core: the offer's current terms hash is `world['hash']`."""
    world = {'hash': 'h1'}
    signed = []

    def __init__(self, *a, **kw):
        pass

    def bind(self, name, args):
        args = dict(args)
        if name == 'valun_respond' and args.get('action') == 'accept' and not args.get('expected_terms_hash'):
            args['expected_terms_hash'] = self.world['hash']
        return args

    def describe_commitment(self, name, args):
        return f"ACCEPT terms {args.get('expected_terms_hash')}"

    def call_safe(self, name, args):
        FakeAgent.signed.append((name, dict(args)))
        if args.get('expected_terms_hash') not in (None, self.world['hash']):
            return {'error': 'terms_changed'}
        return {'ok': True}


class BoundApprovals(unittest.TestCase):
    def setUp(self):
        self._orig = tools.va.ValunAgent
        tools.va.ValunAgent = FakeAgent
        FakeAgent.world['hash'], FakeAgent.signed[:] = 'h1', []
        tools._BOUND.clear()
        self.ctx = Ctx()
        plugin.register(self.ctx)
        self.gate, self.respond = self.ctx.hooks['pre_tool_call'], self.ctx.tools['valun_respond']

    def tearDown(self):
        tools.va.ValunAgent = self._orig
        tools._BOUND.clear()

    def test_the_owner_sees_the_hash_that_is_signed(self):
        args = {'deal_id': 'vd_1', 'action': 'accept'}
        ask = self.gate('valun_respond', args)
        self.assertEqual(ask['action'], 'approve')
        self.assertIn('h1', ask['message'])
        self.assertEqual(json.loads(self.respond(dict(args))), {'ok': True})
        self.assertEqual(FakeAgent.signed[-1][1]['expected_terms_hash'], 'h1')

    def test_a_counter_offer_after_approval_is_refused_not_signed(self):
        args = {'deal_id': 'vd_1', 'action': 'accept'}
        self.gate('valun_respond', args)            # owner shown h1
        FakeAgent.world['hash'] = 'h2'              # the other side counters before the signature
        self.assertEqual(json.loads(self.respond(dict(args))), {'error': 'terms_changed'})
        self.assertEqual(FakeAgent.signed[-1][1]['expected_terms_hash'], 'h1')

    def test_a_binding_is_used_once(self):
        args = {'deal_id': 'vd_1', 'action': 'accept'}
        self.gate('valun_respond', args)
        self.respond(dict(args))
        self.respond(dict(args))                    # no new approval: nothing pinned (the core binds nothing itself)
        self.assertNotIn('expected_terms_hash', FakeAgent.signed[-1][1])

    def test_harmless_calls_are_not_pinned(self):
        args = {'deal_id': 'vd_1', 'action': 'decline'}
        self.assertIsNone(self.gate('valun_respond', args))
        self.respond(dict(args))
        self.assertEqual(FakeAgent.signed[-1][1], args)
        self.assertEqual(tools._BOUND, {})

    def test_the_agents_own_hash_is_kept(self):
        args = {'deal_id': 'vd_1', 'action': 'accept', 'expected_terms_hash': 'h0'}
        ask = self.gate('valun_respond', args)
        self.assertIn('h0', ask['message'])
        self.respond(dict(args))
        self.assertEqual(FakeAgent.signed[-1][1]['expected_terms_hash'], 'h0')


if __name__ == '__main__':
    unittest.main()
