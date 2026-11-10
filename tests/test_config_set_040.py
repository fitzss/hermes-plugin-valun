"""The agent's signing secret and token reach Hermes's `hermes config set` through the child's environment, never its
command line (audit SC-2): another local user can read a process's command line (Linux /proc/<pid>/cmdline without
hidepid), but not its environment. Offline: throwaway secrets, temporary folders, a fake `hermes_cli` for the child, and
real Hermes only where it is importable (the Hermes venv), always in a temporary HERMES_HOME."""
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from unittest import mock

import test_cli_040 as t

plugin, tools = t.plugin, t.tools
REAL_CONFIG_SET = plugin._hermes_config_set      # the tests' Base replaces it while a test runs
TOKEN = 'agent-token-throwaway-sc2'
VALUE_ENV = 'VALUN_PLUGIN_CONFIG_VALUE'      # the child's environment variable for the value

# A stand-in for `python -m hermes_cli.main`: it records what the child got, including the command line the operating
# system shows for the child process, and exits 3 for the key FAIL.
FAKE_MAIN = textwrap.dedent('''
    import json, os, sys

    def os_command_line():
        if sys.platform == 'win32':
            import ctypes
            get = ctypes.windll.kernel32.GetCommandLineW
            get.restype = ctypes.c_wchar_p
            return get()
        try:
            with open('/proc/self/cmdline', 'rb') as f:
                return f.read().replace(b'\\0', b' ').decode('utf-8', 'replace')
        except OSError:
            return None

    if __name__ == '__main__':
        seen = {'argv': sys.argv[1:], 'argv0': os.path.basename(sys.argv[0]), 'name': __name__,
                'env_left': [k for k in os.environ if k.upper() == 'VALUN_PLUGIN_CONFIG_VALUE'],
                'home': os.environ.get('HERMES_HOME'), 'cmdline': os_command_line()}
        with open(os.path.join(os.environ['HERMES_HOME'], 'seen-%d.json' % os.getpid()), 'w', encoding='utf-8') as f:
            json.dump(seen, f)
        sys.exit(3 if sys.argv[1:4] == ['config', 'set', 'FAIL'] else 0)
''')


def fake_constants(home):
    fake = types.ModuleType('hermes_constants')
    fake.get_hermes_home = lambda: pathlib.Path(home)
    return mock.patch.dict(sys.modules, {'hermes_constants': fake})


class TheValueIsNeverAnArgument(unittest.TestCase):
    def test_the_value_goes_to_the_child_in_its_environment(self):
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, '', '')
        with tempfile.TemporaryDirectory() as home, fake_constants(home), mock.patch.object(plugin.subprocess, 'run', run):
            self.assertTrue(REAL_CONFIG_SET('VALUN_SIGNING_SECRET', t.SECRET))
            self.assertTrue(REAL_CONFIG_SET('VALUN_AGENT_TOKEN', TOKEN))
        self.assertEqual(len(calls), 2)
        for (argv, kw), (key, value) in zip(calls, [('VALUN_SIGNING_SECRET', t.SECRET), ('VALUN_AGENT_TOKEN', TOKEN)]):
            self.assertNotIn(value, ' '.join(argv))
            self.assertEqual(argv[0], sys.executable)
            self.assertEqual(argv[-1], key)
            self.assertEqual(kw['env'][VALUE_ENV], value)
            self.assertEqual(kw['env']['HERMES_HOME'], home)          # the profile home stays pinned
            self.assertNotIn('shell', kw)
        self.assertEqual(plugin.CONFIG_VALUE_ENV, VALUE_ENV)

    def test_the_child_runs_hermes_config_set_and_its_command_line_has_no_value(self):
        """A real child Python with a fake hermes_cli.main: it is run as `__main__` with argv `config set KEY VALUE`,
        the value is gone from its environment before Hermes starts, the HERMES_HOME is pinned, the exit code is the
        result, and the command line the system shows for the child holds the key but not the value."""
        with tempfile.TemporaryDirectory() as tmp:
            pkg, home = pathlib.Path(tmp) / 'path' / 'hermes_cli', pathlib.Path(tmp) / 'home'
            pkg.mkdir(parents=True)
            home.mkdir()
            (pkg / '__init__.py').write_text('', encoding='utf-8')
            (pkg / 'main.py').write_text(FAKE_MAIN, encoding='utf-8')
            env = {'PYTHONPATH': str(pkg.parent), 'HERMES_HOME': str(pathlib.Path(tmp) / 'not-this-home')}
            with mock.patch.dict(os.environ, env), fake_constants(home):
                self.assertTrue(REAL_CONFIG_SET('VALUN_SIGNING_SECRET', t.SECRET))
                self.assertFalse(REAL_CONFIG_SET('FAIL', 'value-of-a-failing-key'))
            seen = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(home.glob('seen-*.json'))]
        self.assertEqual(len(seen), 2, seen)
        ok = next(s for s in seen if s['argv'][2] == 'VALUN_SIGNING_SECRET')
        self.assertEqual(ok['argv'], ['config', 'set', 'VALUN_SIGNING_SECRET', t.SECRET])
        self.assertEqual((ok['argv0'], ok['name'], ok['env_left'], ok['home']), ('main.py', '__main__', [], str(home)))
        for s in seen:
            if sys.platform == 'win32' or sys.platform.startswith('linux'):
                self.assertIsNotNone(s['cmdline'])
            if s['cmdline'] is not None:                   # Windows and Linux say; elsewhere the argv checks hold
                self.assertIn(s['argv'][2], s['cmdline'])
                self.assertNotIn(s['argv'][3], s['cmdline'])


class JoinAndEnrollPassNoSecretAsAnArgument(t.Base):
    """Through `hermes valun join --direct-tools` and `hermes valun enroll`: every child the plugin starts gets an
    argument list without the signing secret or the agent token."""

    def setUp(self):
        super().setUp()
        self.calls = []

        def run(argv, **kw):
            self.calls.append((list(argv), dict(kw.get('env') or {})))
            return subprocess.CompletedProcess(argv, 0, '', '')
        for p in (mock.patch.object(plugin, '_hermes_config_set', REAL_CONFIG_SET),
                  mock.patch.object(plugin.subprocess, 'run', run)):
            p.start()
            self.addCleanup(p.stop)

    def check(self, keys):
        for argv, _env in self.calls:
            self.assertFalse([a for a in argv if re.search(r'[0-9a-f]{64}', a)], argv)      # no signing secret
            self.assertNotIn('agent-token-throwaway', ' '.join(argv), argv)
        self.assertEqual([argv[-1] for argv, _env in self.calls], keys)
        values = [env.get(VALUE_ENV) for _argv, env in self.calls]
        self.assertRegex(values[0], r'^[0-9a-f]{64}$')
        self.assertEqual(values[1:], ['agent-token-throwaway', 'off'][:len(keys) - 1])

    def test_join(self):
        code, out = self.run_cli(t.Ctx(), t.Server(t.join_routes(self)), 'join', '--sandbox', '--direct-tools',
                                 '--handle', 's1', '--operator', 'Tester', '--server', t.SERVER)
        self.assertEqual(code, 0, out)
        self.check(['VALUN_SIGNING_SECRET', 'VALUN_AGENT_TOKEN', 'tools.tool_search.enabled'])

    def test_enroll(self):
        code, out = self.run_cli(t.Ctx(), t.Server(t.join_routes(self)), 'enroll', '--server', t.SERVER,
                                 '--participant', t.PID, '--code-from-stdin', stdin='enroll-code-throwaway\n')
        self.assertEqual(code, 0, out)
        self.check(['VALUN_SIGNING_SECRET', 'VALUN_AGENT_TOKEN'])


@unittest.skipIf(importlib.util.find_spec('hermes_cli') is None, 'needs Hermes (run with the Hermes venv)')
class RealHermes(unittest.TestCase):
    def test_hermes_stores_the_secret_in_the_pinned_home(self):
        """The child runs Hermes's own `hermes config set`: the secret lands in the pinned home's .env, nothing else."""
        with tempfile.TemporaryDirectory(prefix='vh-') as home:
            with mock.patch.dict(os.environ, {'HERMES_HOME': home}):
                self.assertTrue(REAL_CONFIG_SET('VALUN_SIGNING_SECRET', t.SECRET))
            text = (pathlib.Path(home) / '.env').read_text(encoding='utf-8')
        self.assertIn(f'VALUN_SIGNING_SECRET={t.SECRET}', text.splitlines())
        self.assertNotIn(VALUE_ENV, text)


if __name__ == '__main__':
    unittest.main()
