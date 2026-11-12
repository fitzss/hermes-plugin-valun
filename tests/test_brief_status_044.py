"""0.4.4: the brief_status setting (for small models) reaches the core's agent, whose valun_status then answers the brief
view unless a call passes brief=false; the shipped schema offers brief; the manifest declares the setting."""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('valun_plugin_pkg', os.path.join(ROOT, '__init__.py'), submodule_search_locations=[ROOT])
plugin = sys.modules.get('valun_plugin_pkg') or importlib.util.module_from_spec(spec)
if 'valun_plugin_pkg' not in sys.modules:
    sys.modules['valun_plugin_pkg'] = plugin
    spec.loader.exec_module(plugin)
tools = sys.modules['valun_plugin_pkg.tools']
core_agent = sys.modules['valun_plugin_pkg.valun_core.agent']
core_schemas = sys.modules['valun_plugin_pkg.valun_core.schemas']
tools.data_dir = lambda: None
tools.secret_value = lambda name: None


class Ctx:
    def __init__(self, **config):
        self.config = config

    def get_config(self, key, default=None):
        return self.config.get(key, default)


class BriefStatus(unittest.TestCase):
    def test_the_setting_reaches_the_agent(self):
        self.assertIs(tools.agent_for(Ctx()).brief_status, False)
        self.assertIs(tools.agent_for(Ctx(brief_status=True)).brief_status, True)
        self.assertIs(tools.agent_for(Ctx(brief_status='yes')).brief_status, False, 'only a real true turns it on')

    def test_the_agent_answers_the_brief_view(self):
        a = tools.agent_for(Ctx(brief_status=True))
        a._full_status = lambda: {'connected': True, 'you': {'id': 'vp_a', 'handle': 'a'}, 'position': {'claims_held': 1, 'owes': 0, 'net': 1},
                                  'honest_labels': {'x': 'y' * 500}}
        b = a.status({})
        self.assertTrue(b['brief'])
        self.assertNotIn('honest_labels', b)
        self.assertNotIn('brief', a.status({'brief': False}))
        self.assertIs(core_agent.brief_status, getattr(core_agent, 'brief_status'))

    def test_schema_and_manifest(self):
        self.assertEqual(core_schemas.STATUS['parameters']['properties']['brief']['type'], 'boolean')
        with open(os.path.join(ROOT, 'plugin.yaml'), encoding='utf-8') as f:
            y = f.read()
        self.assertIn('  brief_status:\n    type: bool\n    default: false', y)


if __name__ == '__main__':
    unittest.main()
