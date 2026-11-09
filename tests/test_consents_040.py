"""0.4.0: process_consents (config, default off). Off: the client never processes consents, and makes no request for
them. On: the core processes them before a Valun tool that is not read-only, at most once per 60 s (consents_last_run_ms,
kept in valun-records.json); a read-only tool makes no full run (before valun_status and valun_inbox the core makes only
its value-safe run, decision D-3 a, and not within 60 s of the last run of either kind: test_reads_roles_040). Offline:
a fake server; throwaway keys."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if 'valun_plugin_pkg' not in sys.modules:
    spec = importlib.util.spec_from_file_location('valun_plugin_pkg', os.path.join(ROOT, '__init__.py'), submodule_search_locations=[ROOT])
    mod = importlib.util.module_from_spec(spec)
    sys.modules['valun_plugin_pkg'] = mod
    spec.loader.exec_module(mod)
tools = sys.modules['valun_plugin_pkg.tools']
va, vc = tools.va, tools.vc
# Hermetic: never a real Hermes home or secret, also when Hermes itself is importable (its venv); tests that need a
# records folder or credentials patch these for themselves.
tools.data_dir = lambda: None
tools.secret_value = lambda name: None
SECRET = va.vb.generate_secret().hex()          # throwaway
DEAL = 'vd_0000000000000001'


class Ctx:
    def __init__(self, **config):
        self.config = {'participant_id': 'vp_000000000001', 'server_url': 'http://127.0.0.1:9', 'confirm_commitments': True}
        self.config.update(config)
        self.profile_name, self.plugin_id = 'valun-test', 'valun'

    def get_config(self, key, default=None):
        return self.config.get(key, default)


SANDBOX = {'consents.setoff': True, 'consents.as_issuer': True, 'consents.as_new_creditor': True}


class ConsentProcessing(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='valun-plugin-test-')
        self.requests, self.runs = [], []
        test = self

        def server(client, method, path, body=None, auth=True, token=None):
            test.requests.append((method, path))
            if (method, path) == ('GET', '/pilot/v1/info'):
                return {'network': 'valun-pilot-1', 'unit': 'PC', 'features': []}
            if (method, path) == ('GET', f'/pilot/v1/deals/{DEAL}'):
                return {'id': DEAL, 'current': {'terms_hash': 'ab' * 32}}
            if (method, path) == ('POST', f'/pilot/v1/deals/{DEAL}/decline'):
                return {'status': 'declined', 'next_actions': []}
            if (method, path) == ('GET', '/pilot/v1/inbox'):
                return {'to_answer': [], 'to_deliver': [], 'to_review': []}
            raise AssertionError(f'unexpected request {method} {path}')
        real = vc.Client.process_consents

        def spy(client, *a, **kw):
            test.runs.append(kw.get('now_ms'))
            return real(client, *a, **kw)
        self.patches = [mock.patch.object(tools, 'data_dir', lambda: self.dir),
                        mock.patch.object(tools, 'secret_value', lambda n: {'VALUN_SIGNING_SECRET': SECRET,
                                                                            'VALUN_AGENT_TOKEN': 'agent-token-throwaway'}.get(n)),
                        mock.patch.object(vc.Client, 'request', server), mock.patch.object(vc.Client, 'process_consents', spy)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def decline(self, ctx):
        out = json.loads(tools.make_handlers(ctx)['valun_respond']({'deal_id': DEAL, 'action': 'decline', 'reason': 'not now'}))
        self.assertEqual(out['status'], 'declined')

    def last_run(self, ms):
        path = os.path.join(self.dir, tools.RECORDS_FILE)
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        data['consents_last_run_ms'] = ms
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    def test_off_consents_are_never_processed(self):
        ctx = Ctx(**SANDBOX)                     # a policy, but process_consents is not on (the default)
        for _ in range(3):
            self.decline(ctx)
        if os.path.exists(os.path.join(self.dir, tools.RECORDS_FILE)):
            self.last_run(0)                     # long ago: still nothing, because processing is off
        self.decline(Ctx(process_consents=False, **SANDBOX))
        self.assertEqual(self.runs, [])
        self.assertNotIn(('GET', '/pilot/v1/info'), self.requests)
        self.assertNotIn(('GET', '/pilot/v1/consents'), self.requests)

    def test_on_consents_are_processed_once_per_60_s_before_tools_that_are_not_read_only(self):
        ctx = Ctx(process_consents=True, **SANDBOX)
        self.decline(ctx)
        self.assertEqual(len(self.runs), 1)
        self.decline(ctx)                        # within 60 s of the last run (kept in valun-records.json): not again
        self.assertEqual(len(self.runs), 1)
        tools.make_handlers(ctx)['valun_inbox']({})       # a read-only tool: no full run, and no value-safe run within 60 s
        self.assertEqual(len(self.runs), 1)
        now = int(time.time() * 1000)
        self.last_run(now - 59_000)
        self.decline(ctx)
        self.assertEqual(len(self.runs), 1)
        self.last_run(now - 61_000)
        self.decline(ctx)
        self.assertEqual(len(self.runs), 2)
        self.decline(ctx)
        self.assertEqual(len(self.runs), 2)

    def test_on_without_a_policy_or_payments_nothing_is_processed(self):
        self.decline(Ctx(process_consents=True))   # no consent policy and no payment by transfer: nothing to consent to
        self.assertEqual(self.runs, [])


if __name__ == '__main__':
    unittest.main()
