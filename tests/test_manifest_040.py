"""0.4.0 packaging: plugin.yaml names version 0.4.0 and still exactly the nine tools of the core, in its order; its
config_schema has process_consents (bool, default false) and the three consents.<role> switches; the vendored core is
the eight files of valun 0.4.0; and no user-facing text still says that nothing is settled."""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import valun_core  # noqa: E402
from valun_core import schemas  # noqa: E402

CORE_FILES = ['agent', 'basis', 'client', 'measure', 'novation', 'records', 'sandbox', 'schemas']


def read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


class Manifest(unittest.TestCase):
    def setUp(self):
        self.yaml = read('plugin.yaml')

    def block(self, key):
        """The '  - item' lines right under `key:` (a top-level list)."""
        m = re.search(rf'^{key}:\n((?:  - .*\n)+)', self.yaml, re.M)
        return [line[4:].strip() for line in m.group(1).splitlines()] if m else None

    def test_version(self):
        self.assertRegex(self.yaml, r'(?m)^version: 0\.4\.4$')
        self.assertEqual(valun_core.__version__, '0.4.4')

    def test_still_nine_tools_in_the_cores_order(self):
        self.assertEqual(self.block('provides_tools'), [t['name'] for t in schemas.ALL])
        self.assertEqual(len(schemas.ALL), 9)
        self.assertEqual(self.block('provides_hooks'), ['pre_tool_call'])

    def test_config_schema(self):
        for key, kind, default in (('process_consents', 'bool', 'false'), ('consents.setoff', 'bool', 'false'),
                                   ('consents.as_issuer', 'bool', 'false'), ('consents.as_new_creditor', 'bool', 'false'),
                                   ('confirm_commitments', 'bool', 'true'), ('check_link_hosts', 'bool', 'true')):
            self.assertRegex(self.yaml, rf'(?m)^  {re.escape(key)}:\n    type: {kind}\n    default: {default}\n    description: "[^"]+"$', key)

    def test_the_core_is_the_eight_files_of_the_package(self):
        listed = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, 'valun_core')) if f.endswith('.py') and f != '__init__.py')
        self.assertEqual(listed, CORE_FILES)
        doc = valun_core.__doc__
        for name in CORE_FILES:
            self.assertIn(name, doc)

    def test_no_text_says_nothing_is_settled(self):
        for name in ('plugin.yaml', 'README.md', 'after-install.md'):
            text = re.sub(r'\s+', ' ', read(name)).lower()
            for phrase in ('nothing is settled', 'nothing is ever settled', 'there is no settlement', 'no redemption, reserve, clearing'):
                self.assertNotIn(phrase, text, name)
            self.assertIn('no monetary value', text, name)


if __name__ == '__main__':
    unittest.main()
