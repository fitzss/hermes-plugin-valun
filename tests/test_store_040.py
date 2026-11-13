"""0.4.0: HermesStore keeps every key of the core's CORE_STATE_KEYS in plugin-data/valun/valun-records.json, under the
core FileStore's lock protocol, reading the file again for every change; it starts from 0.3's issued-notes.json; it
refuses to change or sign anything when the file exists but cannot be read; and it gives the core the consent policy
only while process_consents is on. Every test uses a temporary folder in place of plugin-data/valun."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
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


class Ctx:
    def __init__(self, **config):
        self.config = {'participant_id': 'vp_000000000001', 'server_url': 'http://127.0.0.1:9'}
        self.config.update(config)
        self.profile_name, self.plugin_id = 'valun-test', 'valun'

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value


def secrets_of(name):
    return {'VALUN_SIGNING_SECRET': SECRET, 'VALUN_AGENT_TOKEN': 'agent-token-throwaway'}.get(name)


# One distinct, JSON-shaped value per CORE_STATE_KEY.
VALUES = {
    'issued': {'aa>bb': 7},
    'records': {'aa>bb': {'confirmed': {'total': 7, 'timestamp': 1700000000000, 'signature': 'cd' * 33}, 'pending': []},
                'paid_deals': {'vd_0000000000000001': {'route': 'note', 'edge': 'aa>bb', 'total': 7, 'timestamp': 1700000000000}}},
    'signed': {'aa>bb': {'1700000000999': {'op': 'vt_0000000000000001', 'slots': {'aa>bb:new': 5}}}},
    'novated_edges': {'aa>bb': True},
    'transfer_attempts': {'vd_0000000000000002': [{'deal_id': 'vd_0000000000000002', 'route': 'transfer', 'edge': 'cc>dd',
                                                    'prior': 5, 'total': 3, 'timestamp': 1700000000999}]},
    'approved_payments': {'vd_0000000000000002': 'ef' * 32},
    'consent_policy': {'setoff': True, 'as_issuer': False, 'as_new_creditor': True, 'max_PC': 12},
    'consent_loop': 'off',
    'consents_last_run_ms': 1700000000123,
    'consent_log': [{'at': '2026-11-09T00:00:00.000Z', 'decision': 'signed', 'kind': 'setoff'}],
    'pins': {'tracker_nft_id': '42' * 32, 'denomination': 'token:' + 'ab' * 32},
    'journal_seq': 41,
    'consents_last_safe_run_ms': 1700000000456,     # 0.4 (D-3 a): the last value-safe consent run, before a read
    'consent_roles_declared': 'setoff,issuer,creditor',   # 0.4 (D-3 b): the roles the server last took
    # 0.6 (fix inbox #18): the calls a backer honored or is honoring, so it never pays beyond the cap it signed.
    'guarantee_honors': {'vg_' + '1' * 16: {'cap': 5, 'calls': {'vgc_' + '2' * 16: {'amount': 5, 'state': 'honored', 'total': 5,
                                                                              'timestamp': 1700000000997}}}},
}


class Folder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='valun-plugin-test-')
        self.patch = mock.patch.object(tools, 'data_dir', lambda: self.dir)
        self.patch.start()
        self.path = os.path.join(self.dir, tools.RECORDS_FILE)

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def file(self):
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)


class RoundTrip(Folder):
    def test_the_core_keys_are_the_ones_in_valun_records(self):
        self.assertEqual(sorted(VALUES), sorted(vc.CORE_STATE_KEYS))

    def test_every_core_key_round_trips(self):
        s = tools.HermesStore(Ctx(process_consents=True))
        s.update(lambda state: state.update(VALUES))
        self.assertEqual(self.file(), VALUES, 'only CORE_STATE_KEYS are written: no settings, no secrets')
        fresh = tools.HermesStore(Ctx(process_consents=True))
        for k, v in VALUES.items():
            self.assertEqual(fresh.state[k], v, k)
        self.assertEqual(fresh.state['participant_id'], 'vp_000000000001')

    def test_every_core_key_survives_two_concurrent_saves(self):
        """(d) two stores save at the same time, many times: each writes half the keys; every key arrives intact."""
        keys = sorted(VALUES)
        a, b = tools.HermesStore(Ctx(process_consents=True)), tools.HermesStore(Ctx(process_consents=True))
        a.state.update({k: VALUES[k] for k in keys[::2]})
        b.state.update({k: VALUES[k] for k in keys[1::2]})
        errors = []

        def run(store):
            try:
                for _ in range(25):
                    store.save()
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=run, args=(s,)) for s in (a, b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        fresh = tools.HermesStore(Ctx(process_consents=True))
        for k, v in VALUES.items():
            self.assertEqual(fresh.state[k], v, k)
        self.assertFalse(os.path.exists(self.path + '.lock'), 'the lock is released')

    def test_two_concurrent_writers_lose_no_change(self):
        """update() reads the file again under the lock: 2 x 40 changes from two stores all arrive."""
        a, b = tools.HermesStore(Ctx()), tools.HermesStore(Ctx())

        def log(state):
            state['consent_log'] = (state.get('consent_log') or []) + [len(state.get('consent_log') or [])]

        def seq(state):
            state['journal_seq'] = (state.get('journal_seq') or 0) + 1
        errors = []

        def run(store, fn):
            try:
                for _ in range(40):
                    store.update(fn)
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=run, args=(a, log)), threading.Thread(target=run, args=(b, seq))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        data = self.file()
        self.assertEqual(data['consent_log'], list(range(40)))
        self.assertEqual(data['journal_seq'], 40)

    def test_a_change_that_raises_changes_nothing(self):
        s = tools.HermesStore(Ctx())
        s.update(lambda state: state.update(journal_seq=1))

        def bad(state):
            state['journal_seq'] = 2
            raise ValueError('no')
        with self.assertRaises(ValueError):
            s.update(bad)
        self.assertEqual(self.file()['journal_seq'], 1)
        self.assertEqual(s.state['journal_seq'], 1)

    def test_reload_reads_what_another_process_wrote(self):
        a, b = tools.HermesStore(Ctx()), tools.HermesStore(Ctx())
        b.update(lambda state: state.update(journal_seq=9))
        self.assertNotIn('journal_seq', a.state)
        a.reload()
        self.assertEqual(a.state['journal_seq'], 9)

    def test_keys_of_another_version_are_kept(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'journal_seq': 1, 'future_key': {'x': 1}}, f)
        tools.HermesStore(Ctx()).update(lambda state: state.update(journal_seq=2))
        self.assertEqual(self.file(), {'journal_seq': 2, 'future_key': {'x': 1}})

    def test_the_client_records_through_the_store(self):
        # The core's own write path (Client._update -> HermesStore.update), and its pin on the first GET /info.
        info = {'network': 'valun-pilot-1', 'unit': 'PC', 'tracker_nft_id': '42' * 32, 'denomination': 'token:' + 'ab' * 32}
        with mock.patch.object(tools, 'secret_value', secrets_of), \
                mock.patch.object(vc.Client, 'request', lambda c, m, p, body=None, auth=True, token=None: dict(info)):
            vc.Client(tools.HermesStore(Ctx())).info()
            self.assertEqual(self.file()['pins'], {'tracker_nft_id': '42' * 32, 'denomination': 'token:' + 'ab' * 32})
            with self.assertRaises(vc.ValunError) as e:          # a later answer naming another tracker is refused
                info['tracker_nft_id'] = '43' * 32
                vc.Client(tools.HermesStore(Ctx())).info()
            self.assertEqual(e.exception.code, 'tracker_mismatch')

    def test_outside_hermes_the_records_live_in_memory(self):
        with mock.patch.object(tools, 'data_dir', lambda: None):
            s = tools.HermesStore(Ctx())
            self.assertIsNone(s.path)
            s.update(lambda state: state.update(journal_seq=3))
            s.save()
            s.reload()
            self.assertEqual(s.state['journal_seq'], 3)
        self.assertEqual(os.listdir(self.dir), [])


class Migration(Folder):
    def test_issued_notes_json_is_the_starting_point(self):
        legacy = os.path.join(self.dir, tools.LEGACY_FILE)
        with open(legacy, 'w', encoding='utf-8') as f:
            json.dump({'issued': {'aa>bb': 5}}, f)
        s = tools.HermesStore(Ctx())
        self.assertEqual(s.state['issued'], {'aa>bb': 5})
        self.assertEqual(self.file(), {'issued': {'aa>bb': 5}}, 'copied into valun-records.json on first load')
        with open(legacy, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'issued': {'aa>bb': 5}}, 'the old file is left as it was')
        with open(legacy, 'w', encoding='utf-8') as f:
            json.dump({'issued': {'aa>bb': 99}}, f)
        self.assertEqual(tools.HermesStore(Ctx()).state['issued'], {'aa>bb': 5}, 'once migrated, the old file is not read')

    def test_nothing_to_migrate_writes_nothing(self):
        tools.HermesStore(Ctx())
        self.assertEqual(os.listdir(self.dir), [])

    def test_a_busy_lock_defers_the_copy_to_the_next_change(self):
        legacy = os.path.join(self.dir, tools.LEGACY_FILE)
        with open(legacy, 'w', encoding='utf-8') as f:
            json.dump({'issued': {'aa>bb': 5}}, f)
        with mock.patch.object(tools._RecordsFile, 'LOCK_WAIT_S', 0.05), open(self.path + '.lock', 'w') as lock:
            lock.write('another process\n')
            lock.flush()
            s = tools.HermesStore(Ctx())
        os.unlink(self.path + '.lock')
        self.assertIsNone(s.unreadable)
        self.assertEqual(s.state['issued'], {'aa>bb': 5})
        self.assertFalse(os.path.exists(self.path))
        s.update(lambda state: state.update(journal_seq=1))
        self.assertEqual(self.file(), {'issued': {'aa>bb': 5}, 'journal_seq': 1})


class Unreadable(Folder):
    """(e) a records file that exists but cannot be read: records_unreadable, and nothing is changed or signed."""

    def corrupt(self, text='{"issued": {"aa>bb": 5'):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(text)

    def test_the_store_refuses_to_change_anything(self):
        for text in ('{"issued": {', '[1, 2]', 'not json'):
            self.corrupt(text)
            s = tools.HermesStore(Ctx())
            self.assertEqual(s.unreadable.code, 'records_unreadable', text)
            for op in (lambda: s.update(lambda state: state.update(journal_seq=1)), s.save, s.reload):
                with self.assertRaises(vc.ValunError) as e:
                    op()
                self.assertEqual(e.exception.code, 'records_unreadable')
            with open(self.path, encoding='utf-8') as f:
                self.assertEqual(f.read(), text, 'never replaced by empty records')

    def test_a_corrupt_0_3_record_is_unreadable_too(self):
        with open(os.path.join(self.dir, tools.LEGACY_FILE), 'w', encoding='utf-8') as f:
            f.write('{"issued": 5}')
        s = tools.HermesStore(Ctx())
        self.assertEqual(s.unreadable.code, 'records_unreadable')
        self.assertFalse(os.path.exists(self.path))

    def test_a_file_that_breaks_later_stops_the_next_change(self):
        s = tools.HermesStore(Ctx())
        s.update(lambda state: state.update(journal_seq=1))
        self.corrupt()
        with self.assertRaises(vc.ValunError) as e:
            s.update(lambda state: state.update(journal_seq=2))
        self.assertEqual(e.exception.code, 'records_unreadable')
        self.assertEqual(s.unreadable.code, 'records_unreadable')

    def test_the_tools_refuse_to_sign_and_the_hook_blocks(self):
        self.corrupt()
        requests = []
        ctx = Ctx(confirm_commitments=True)
        with mock.patch.object(tools, 'secret_value', secrets_of), \
                mock.patch.object(vc.Client, 'request', lambda c, m, p, body=None, auth=True, token=None: requests.append((m, p))):
            h = tools.make_handlers(ctx)
            for name, args in (('valun_respond', {'deal_id': 'vd_0000000000000001', 'action': 'decline', 'reason': 'no'}),
                               ('valun_deliver', {'deal_id': 'vd_0000000000000001', 'content': 'x'}),
                               ('valun_review_delivery', {'deal_id': 'vd_0000000000000001', 'decision': 'dispute', 'reason': 'x'})):
                out = json.loads(h[name](dict(args)))
                self.assertEqual(out['error'], 'records_unreadable', name)
                self.assertIn('signs nothing', out['what_this_means'])
            out = plugin._gate(ctx, 'valun_respond', {'deal_id': 'vd_0000000000000001', 'action': 'accept'})
            self.assertEqual(out, {'action': 'block', 'message': tools.RECORDS_BLOCK_TEXT})
            off = Ctx(confirm_commitments=False)
            out = json.loads(tools.make_handlers(off)['valun_respond']({'deal_id': 'vd_0000000000000001', 'action': 'accept'}))
            self.assertEqual(out['error'], 'records_unreadable')
        self.assertEqual(requests, [], 'nothing was sent')

    def test_a_data_folder_that_cannot_be_used_is_unreadable(self):
        with mock.patch.object(tools, 'data_dir', side_effect=PermissionError('denied')):
            s = tools.HermesStore(Ctx())
        self.assertEqual(s.unreadable.code, 'records_unreadable')
        with self.assertRaises(vc.ValunError):
            s.update(lambda state: None)


class ConsentPolicy(Folder):
    POLICY = {'setoff': True, 'as_issuer': True, 'as_new_creditor': False, 'max_PC': 50}

    def test_off_the_client_declares_and_signs_no_consent_role(self):
        tools.HermesStore(Ctx(process_consents=True)).update(lambda state: state.update(consent_policy=dict(self.POLICY)))
        s = tools.HermesStore(Ctx())                            # process_consents off (the default)
        self.assertNotIn('consent_policy', s.state)
        self.assertEqual(s.kept_policy, self.POLICY)
        c = vc.Client(s)
        c._advertised = ['bound_notes/1', 'circulation/1']
        self.assertIsNone(c.features_header(), 'no X-Valun-Features: the server proposes it nothing to sign')
        self.assertFalse(va.wants_consents(s.state))
        s.update(lambda state: state.update(journal_seq=5))
        self.assertEqual(self.file()['consent_policy'], self.POLICY, 'the policy stays in the file')

    def test_on_the_policy_is_in_force(self):
        tools.HermesStore(Ctx(process_consents=True)).update(lambda state: state.update(consent_policy=dict(self.POLICY)))
        s = tools.HermesStore(Ctx(process_consents=True))
        self.assertEqual(s.state['consent_policy'], self.POLICY)
        c = vc.Client(s)
        c._advertised = ['bound_notes/1', 'circulation/1']
        self.assertEqual(c.features_header(), 'consents/1;roles=setoff,issuer')

    def test_the_config_flags_are_the_owners_switches(self):
        tools.HermesStore(Ctx(process_consents=True)).update(lambda state: state.update(consent_policy=dict(self.POLICY)))
        s = tools.HermesStore(Ctx(process_consents=True, **{'consents.setoff': False, 'consents.as_new_creditor': True}))
        self.assertEqual(s.state['consent_policy'], {'setoff': False, 'as_issuer': True, 'as_new_creditor': True, 'max_PC': 50})
        fresh = tools.HermesStore(Ctx(process_consents=True, **{'consents.setoff': True, 'consents.as_issuer': True,
                                                                 'consents.as_new_creditor': True}))
        self.assertEqual(fresh.state['consent_policy']['max_PC'], 50)
        os.unlink(self.path)
        started = tools.HermesStore(Ctx(process_consents=True, **{'consents.setoff': True}))
        self.assertEqual(started.state['consent_policy'], {'setoff': True, 'max_PC': tools.CONSENT_MAX_PC})

    def test_only_true_turns_processing_on(self):
        for value, on in ((True, True), (False, False), ('true', False), (1, False), (None, False)):
            self.assertEqual(tools.HermesStore(Ctx(process_consents=value)).process_consents, on, value)
            self.assertEqual(tools.agent_for(Ctx(process_consents=value)).process_consents, on, value)
        self.assertFalse(tools.agent_for(Ctx()).process_consents, 'default off (decision D-2 g)')


if __name__ == '__main__':
    unittest.main()
