"""0.2.1: a misspelt or unknown action/decision must ask the owner (fail closed) and must never sign anything."""
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('valun_plugin_pkg', os.path.join(ROOT, '__init__.py'), submodule_search_locations=[ROOT])
plugin = importlib.util.module_from_spec(spec)
sys.modules['valun_plugin_pkg'] = plugin
spec.loader.exec_module(plugin)
tools = sys.modules['valun_plugin_pkg.tools']


class Ctx:
    def get_config(self, key, default=None):
        return default


class FailClosed(unittest.TestCase):
    def test_unknown_values_are_commitments(self):
        for args in ({'decision': 'Accept'}, {'decision': 'accepted'}, {'decision': None}, {}):
            self.assertTrue(plugin._is_commitment('valun_review_delivery', args), args)
        for args in ({'action': 'Accept'}, {'action': 'agree'}, {}):
            self.assertTrue(plugin._is_commitment('valun_respond', args), args)
        self.assertFalse(plugin._is_commitment('valun_review_delivery', {'decision': 'dispute'}))
        self.assertFalse(plugin._is_commitment('valun_respond', {'action': 'decline'}))
        self.assertFalse(plugin._is_commitment('valun_respond', {'action': 'withdraw'}))
        self.assertTrue(plugin._is_commitment('valun_review_delivery', {'decision': 'accept'}))

    def test_review_refuses_unknown_decisions_before_anything_else(self):
        handler = tools.make_handlers(Ctx())['valun_review_delivery']
        for d in ('Accept', 'ok', None):
            out = json.loads(handler({'deal_id': 'vd_0000000000000000', 'decision': d}))
            self.assertEqual(out['error'], 'bad_decision', d)


if __name__ == '__main__':
    unittest.main()
