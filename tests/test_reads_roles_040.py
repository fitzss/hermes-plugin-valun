"""0.4.0 after phase 2 (decisions D-3 a and b): with process_consents on, the plugin's client signs the value-safe
consents (set-offs; payments to it by transfer) before valun_status and valun_inbox too, at most once a minute, and
says so (consents_signed_now), so the sandbox tour no longer stalls at its set-off waits; with process_consents off, or
every consent role off, its next request withdraws the consent roles it declared before (X-Valun-Features
'consents/1;roles='), once. Tracker v3.2: a payment to it by transfer (a prior acknowledgment) is signed only when GET
/info reports the tracker v3.2 contract; otherwise it waits (feature_unavailable). Also: results name this profile's Hermes commands where the core names its CLI's; inside
Hermes without its plugin storage the records are unreadable (never memory-only); the consents.max_PC setting.

Offline: every request goes through the real Client.request and urllib into a fake v16 server (Wire) with its tracker
states, real signatures and throwaway keys."""
import argparse
import copy
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import types
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
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
vb, nv = va.vb, sys.modules['valun_plugin_pkg.valun_core.novation']
# Hermetic: never a real Hermes home or secret, also when Hermes itself is importable (its venv); tests that need a
# records folder or credentials patch these for themselves.
tools.data_dir = lambda: None
tools.secret_value = lambda name: None
REAL_REQUEST = vc.Client.request

SERVER = 'http://valun.invalid'
AGENT_TOKEN = 'vpa_test-only'                               # throwaway
NFT = '42' * 32
DENOM = 'token:' + 'ab' * 32
S_ID, X_ID = 'vp_5a5a5a5a5a5a', 'vp_e1e1e1e1e1e1'
S_SECRET, X_SECRET = vb.generate_secret(), vb.generate_secret()     # throwaway keys
S_PK, X_PK = vb.public_key(S_SECRET).hex(), vb.public_key(X_SECRET).hex()
KEYS = {S_PK: S_SECRET, X_PK: X_SECRET}
HANDLES = {S_PK: 'stranger', X_PK: va.SANDBOX_EXACT}
IDS = {S_PK: S_ID, X_PK: X_ID}
MANDATE = {'objective': 'Self-serve sandbox', 'may_issue_credit': True, 'may_accept_credit': True, 'max_issue_per_deal': 5,
           'max_issue_outstanding': 40, 'max_accept_per_deal': 5, 'max_accept_per_issuer': 40, 'approve_above': 1000,
           'review_deliveries': False, 'auto_clear': True, 'novate_own_debt': True, 'accept_transfers': True,
           'accept_notes_from': [X_ID], 'refuse_notes_from': [], 'pay_with_held_notes': 'when_asked'}
SANDBOX = {'consents.setoff': True, 'consents.as_issuer': True, 'consents.as_new_creditor': True}
SET_OFF_WAIT = 'A set-off of the cycle is due'
# GET /info novation of a v16 server on tracker v3.2: the novation constants and the tracker contract the core signs
# prior acknowledgments for (design v3.2 section 5; valun-open novation.CONTRACT).
INFO_NOVATION = {'transfer_ms': 999, 'setoff_ms': 998, 'bound_note_ms': 997, 'reserved_ms': [995, 996, 997, 998, 999],
                 'max_age_ms': 3600000, 'transfer_ttl_ms': 1200000, 'setoff_ttl_ms': 1800000, 'fallback_margin_ms': 60000,
                 'tracker_build': 'valun-circulating-credit/4', 'transfer_version': 4, 'bound_note_version': 3,
                 'prior_ack': 'basis/prior-ack/1'}
PUBLIC = ('/pilot/v1/info', '/pilot/v1/participants', '/pilot/v1/ledger', '/pilot/v1/clearing', '/pilot/v1/join',
          '/pilot/v1/join-sandbox', '/pilot/v1/enroll')


def now_ms():
    return int(time.time() * 1000)


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def note_sig(issuer_pk, recipient_pk, total, ts):
    data = vb.note_message(bytes.fromhex(issuer_pk), bytes.fromhex(recipient_pk), total, ts, bytes.fromhex(NFT), DENOM)
    return vb.sign(data, KEYS[issuer_pk], bytes.fromhex(issuer_pk)).hex()


class _Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Wire:
    """A fake v16 server for one sandbox participant S (this client) and sandbox-exact X: GET /info, /me, /inbox,
    /consents; POST /consents/{id} (checks S's signatures over the new states, then applies them) and /refuse. It takes
    S's consent roles as v16 does: from an agent request it authenticated (agent token, a route that is not public) and
    answered with 2xx; 'consents/1;roles=' withdraws them at once; a request without the header leaves them. Its GET
    /info reports `novation` (tracker v3.2, INFO_NOVATION; None: no novation object, as a server before tracker v3.2)."""

    def __init__(self, features=('bound_notes/1', 'circulation/1'), novation=INFO_NOVATION):
        self.features = list(features)
        self.novation = copy.deepcopy(novation)
        self.tracker = {}                   # 'I>R' -> {total, timestamp, signature}
        self.items = {}
        self.log = []                       # (method, route, X-Valun-Features)
        self.roles = None                   # S's declared roles as the server holds them (None: never declared)
        self.seq = 0

    # ---------- the world ----------
    def set_edge(self, issuer_pk, recipient_pk, total, ts):
        self.tracker[f'{issuer_pk}>{recipient_pk}'] = {'total': total, 'timestamp': ts,
                                                         'signature': note_sig(issuer_pk, recipient_pk, total, ts)}

    def total(self, issuer_pk, recipient_pk):
        st = self.tracker.get(f'{issuer_pk}>{recipient_pk}')
        return st['total'] if st else 0

    def cycle(self):
        """The tour's step 3: S holds 1 PC of X's notes and owes X 2 PC, so v16 proposes S a set-off of 1 PC (S signs
        as member, on both of its edges). Returns the set-off's id."""
        t = now_ms()
        self.set_edge(X_PK, S_PK, 1, t - 120_000 + 1)
        self.set_edge(S_PK, X_PK, 2, t - 110_000 + 2)
        return self.propose_setoff()

    def propose_setoff(self):
        d = min(self.total(X_PK, S_PK), self.total(S_PK, X_PK))
        self.seq += 1
        sid = f'vs_{self.seq:016x}'
        edges = []
        for i, r in ((X_PK, S_PK), (S_PK, X_PK)):
            before = dict(self.tracker[f'{i}>{r}'])
            base = now_ms() - 1500          # read the clock once: two reads can straddle a millisecond
            ts = base - ((base - nv.SETOFF_MS) % 1000)
            ts = ts if ts > before['timestamp'] else before['timestamp'] + 1 + (nv.SETOFF_MS - (before['timestamp'] + 1)) % 1000
            edges.append({'edge': f'{i}>{r}', 'issuer': {'id': IDS[i], 'handle': HANDLES[i]},
                          'recipient': {'id': IDS[r], 'handle': HANDLES[r]},
                          'your_role': 'issuer' if i == S_PK else 'creditor', 'before': before, 'change': -d,
                          'new_total': before['total'] - d, 'timestamp': ts, 'revision': 1, 'slots': ['new'],
                          'signed_by_you': []})
        self.items[sid] = {'kind': 'setoff', 'id': sid, 'state': 'collecting', 'your_roles': ['member'],
                           'created_at': iso(now_ms()), 'expires_at': iso(now_ms() + 1_800_000), 'network': vc.NETWORK,
                           'tracker_nft_id': NFT, 'denomination': DENOM, 'edges': edges, 'context': {},
                           'summary': {'your_net_change_PC': 0, 'your_total_owed_change_PC': -d}}
        return sid

    def issuer_item(self):
        """A consent in S's issuer role (a transfer of S's own debt): never signed or refused from a read."""
        tid = 'vt_' + 'e' * 16
        self.items[tid] = {'kind': 'transfer', 'id': tid, 'state': 'collecting', 'your_roles': ['issuer'],
                           'created_at': iso(now_ms()), 'expires_at': iso(now_ms() + 1_200_000), 'network': vc.NETWORK,
                           'tracker_nft_id': NFT, 'denomination': DENOM, 'edges': [], 'context': {'amount': 1},
                           'summary': {'your_net_change_PC': 0, 'your_total_owed_change_PC': 0}}
        return tid

    def payee_item(self):
        """A consent in S's payee role (a payment to S by transfer, X's note moving to S): value-safe, so a read run
        handles it; it signs a prior acknowledgment, so only for the tracker v3.2 contract. The Wire serves no deal, so
        past the contract gate it waits because its deal cannot be read."""
        tid = 'vt_' + 'f' * 16
        self.items[tid] = {'kind': 'transfer', 'id': tid, 'state': 'collecting', 'your_roles': ['payee'],
                           'created_at': iso(now_ms()), 'expires_at': iso(now_ms() + 1_200_000), 'network': vc.NETWORK,
                           'tracker_nft_id': NFT, 'denomination': DENOM, 'edges': [],
                           'context': {'amount': 1, 'deal': {'deal_id': 'vd_' + 'f' * 16}},
                           'summary': {'your_net_change_PC': 1, 'your_total_owed_change_PC': 0}}
        return tid

    def position(self):
        hold, owe = self.total(X_PK, S_PK), self.total(S_PK, X_PK)
        waiting = sum(1 for it in self.items.values() if it['state'] == 'collecting')
        return {'claims_held': hold, 'owes': owe, 'net': hold - owe, 'cleared_by_setoff': 0,
                'claims': [{'issuer_id': X_ID, 'from': va.SANDBOX_EXACT, 'issuer_pubkey': X_PK, 'amount': hold}] if hold else [],
                'debts': [{'to': va.SANDBOX_EXACT, 'amount': owe}] if owe else [],
                'pending': {'consents_waiting_for_you': waiting}}

    # ---------- the HTTP surface ----------
    def route(self, method, route, body, token):
        if token != AGENT_TOKEN and route not in PUBLIC:
            raise vc.ValunError('unauthorized', 'no agent token', 401)
        if (method, route) == ('GET', '/pilot/v1/info'):
            return dict({'network': vc.NETWORK, 'unit': 'PC', 'tracker_nft_id': NFT, 'denomination': DENOM,
                         'features': list(self.features)},
                        **({'novation': copy.deepcopy(self.novation)} if self.novation is not None else {}))
        if (method, route) == ('GET', '/pilot/v1/me'):
            return {'participant': {'id': S_ID, 'handle': 'stranger', 'status': 'active', 'sandbox_only': True},
                    'mandate': dict(MANDATE), 'effective_limits': dict(MANDATE), 'mandate_version': 1,
                    'position': self.position(), 'authority': {}, 'agent_limits': {}}
        if (method, route) == ('GET', '/pilot/v1/inbox'):
            return {'respond': [], 'deliver': [], 'review_delivery': [], 'waiting_on_others': [], 'awaiting_your_owner': [],
                    'recent_closed': [], 'payments_in_progress': [],
                    'consents_waiting_for_your_client': sum(1 for it in self.items.values() if it['state'] == 'collecting')}
        if (method, route) == ('GET', '/pilot/v1/consents'):
            return {'consents': [copy.deepcopy(it) for it in self.items.values()]}
        m = re.fullmatch(r'/pilot/v1/consents/([a-z0-9_]+)(/refuse)?', route)
        if method == 'POST' and m and m.group(1) in self.items:
            item = self.items[m.group(1)]
            if m.group(2):
                item['state'] = 'refused'
                return dict(copy.deepcopy(item))
            return self.consent(item, body)
        raise vc.ValunError('not_found', f'no route {method} {route}', 404)

    def consent(self, item, body):
        for slot, sig in body['signatures'].items():
            edge = slot.rsplit(':', 1)[0]
            e = next(x for x in item['edges'] if x['edge'] == edge)
            i, r = edge.split('>')
            data = vb.note_message(bytes.fromhex(i), bytes.fromhex(r), e['new_total'], e['timestamp'], bytes.fromhex(NFT), DENOM)
            if not vb.verify(bytes.fromhex(sig), data, bytes.fromhex(S_PK)):     # S signs every edge's new state
                raise vc.ValunError('bad_signature', f'{slot} does not verify', 403)
        if set(body['signatures']) != {f"{e['edge']}:new" for e in item['edges']}:
            return dict(copy.deepcopy(item), rejected=[])
        for e in item['edges']:
            i, r = e['edge'].split('>')
            self.tracker[e['edge']] = {'total': e['new_total'], 'timestamp': e['timestamp'],
                                       'signature': note_sig(i, r, e['new_total'], e['timestamp'])}
        item['state'] = 'applied'
        return dict(copy.deepcopy(item), rejected=[])

    def urlopen(self, req, timeout=None):
        if not req.full_url.startswith(SERVER):
            raise urllib.error.URLError(f'not the test server: {req.full_url}')
        path, method = req.full_url[len(SERVER):], req.get_method()
        route = path.split('?', 1)[0]
        auth = req.get_header('Authorization') or ''
        token = auth[len('Bearer '):] if auth.startswith('Bearer ') else None
        caps = req.get_header('X-valun-features')
        self.log.append((method, route, caps))
        try:
            out = self.route(method, route, json.loads(req.data.decode('utf-8')) if req.data else None, token)
        except vc.ValunError as e:
            body = json.dumps(dict(e.extra or {}, error=e.code, message=str(e))).encode('utf-8')
            raise urllib.error.HTTPError(req.full_url, e.status, e.code, {}, io.BytesIO(body)) from None
        if caps is not None and token == AGENT_TOKEN and route not in PUBLIC:
            self.roles = {x for x in caps.split('roles=', 1)[-1].split(',') if x}
        return _Answer(json.dumps(out).encode('utf-8'))

    def asked(self, method, route):
        return [x for x in self.log if x[:2] == (method, route)]


class Ctx:
    def __init__(self, **config):
        self.config = {'participant_id': S_ID, 'server_url': SERVER, 'confirm_commitments': True}
        self.config.update(config)
        self.profile_name, self.plugin_id = 'valun-test', 'valun'

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='valun-plugin-test-')
        self.wire = Wire()
        secrets_ = {'VALUN_SIGNING_SECRET': S_SECRET.hex(), 'VALUN_AGENT_TOKEN': AGENT_TOKEN}
        self.patches = [mock.patch.object(tools, 'data_dir', lambda: self.dir),
                        mock.patch.object(tools, 'secret_value', lambda n: secrets_.get(n)),
                        mock.patch.object(vc.Client, 'request', REAL_REQUEST),
                        mock.patch.object(vc.urllib.request, 'urlopen', self.wire.urlopen)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, ctx, name, args=None):
        return json.loads(tools.make_handlers(ctx)[name](args or {}))

    def records(self):
        with open(os.path.join(self.dir, tools.RECORDS_FILE), encoding='utf-8') as f:
            return json.load(f)

    def set_records(self, **changes):
        tools.HermesStore(Ctx()).update(lambda state: state.update(changes))


class ConsentsOnReads(Base):
    """D-3 a in Hermes: the sandbox tour's set-off waits (steps 3 to 5) are signed during valun_status."""

    def test_the_tours_set_off_is_signed_during_valun_status(self):
        ctx = Ctx(process_consents=True, **SANDBOX)                  # as `hermes valun join --sandbox` leaves it
        sid = self.wire.cycle()
        out = self.call(ctx, 'valun_status')
        self.assertNotIn('error', out, out)
        self.assertEqual([(e['id'], e['kind'], e['role'], e['your_net_change_PC']) for e in out['consents_signed_now']],
                         [(sid, 'setoff', 'member', 0)])
        self.assertEqual(list(out['consents_signed_now'][0]), list(va.SIGNED_NOW_KEYS))
        self.assertEqual(self.wire.items[sid]['state'], 'applied')
        self.assertEqual((self.wire.total(X_PK, S_PK), self.wire.total(S_PK, X_PK)), (0, 1))
        # The status is read after the run: it shows the cycle cleared, and the tour no longer waits for the set-off.
        self.assertEqual((out['money_view']['you_hold_PC'], out['money_view']['you_owe_PC']), (0, 1))
        self.assertNotIn(SET_OFF_WAIT, out['sandbox_next_steps']['then'])
        rec = self.records()
        self.assertEqual(rec['consent_log'][-1]['decision'], 'signed')
        self.assertIn('consents_last_safe_run_ms', rec)
        self.assertNotIn('consents_last_run_ms', rec, 'a read run never delays a full run')
        self.assertEqual(rec['consent_roles_declared'], 'setoff,issuer,creditor')
        self.assertEqual(self.wire.asked('GET', '/pilot/v1/consents')[0][2], 'consents/1;roles=setoff,issuer,creditor')

    def test_without_the_read_run_the_tour_would_wait(self):
        """The same state with process_consents off: valun_status signs nothing and tells the agent to wait."""
        sid = self.wire.cycle()
        out = self.call(Ctx(**SANDBOX), 'valun_status')
        self.assertNotIn('consents_signed_now', out)
        self.assertIn(SET_OFF_WAIT, out['sandbox_next_steps']['then'])
        self.assertEqual(self.wire.items[sid]['state'], 'collecting')
        self.assertEqual(self.wire.asked('GET', '/pilot/v1/consents'), [])

    def test_the_next_set_off_waits_a_minute_then_valun_inbox_signs_it(self):
        ctx = Ctx(process_consents=True, **SANDBOX)
        first = self.wire.cycle()
        self.assertEqual(len(self.call(ctx, 'valun_status')['consents_signed_now']), 1)
        after = self.wire.tracker[f'{X_PK}>{S_PK}']['timestamp']        # the first set-off's new state
        self.wire.set_edge(X_PK, S_PK, 2, after + 2)                    # X pays S again: another cycle through S
        second = self.wire.propose_setoff()
        self.assertNotEqual(first, second)
        out = self.call(ctx, 'valun_status')                           # within a minute of the last run: no run
        self.assertNotIn('consents_signed_now', out)
        self.assertEqual(len(self.wire.asked('GET', '/pilot/v1/consents')), 1)
        self.assertEqual(self.wire.items[second]['state'], 'collecting')
        self.set_records(consents_last_safe_run_ms=now_ms() - va.CONSENTS_EVERY_MS - 1000)
        box = self.call(ctx, 'valun_inbox')
        self.assertEqual([e['id'] for e in box['consents_signed_now']], [second])
        self.assertEqual(self.wire.items[second]['state'], 'applied')

    def test_an_issuers_consent_is_never_signed_or_refused_from_a_read(self):
        ctx = Ctx(process_consents=True, **SANDBOX)
        sid, tid = self.wire.cycle(), self.wire.issuer_item()
        out = self.call(ctx, 'valun_status')
        self.assertEqual([e['id'] for e in out['consents_signed_now']], [sid])
        self.assertEqual(self.wire.items[tid]['state'], 'collecting')
        self.assertEqual([x for x in self.wire.log if tid in x[1]], [], 'not posted, not refused')
        self.assertNotIn(tid, json.dumps(self.records().get('consent_log')))

    def test_a_payment_to_you_waits_without_the_tracker_v32_contract(self):
        """Tracker v3.2 (fail closed): a payee consent signs a prior acknowledgment, so on a server whose GET /info
        reports no tracker v3.2 contract a read run leaves it waiting (feature_unavailable): not signed, not refused,
        its deal not read. The set-off of the same run is no prior acknowledgment and is still signed."""
        v31 = dict(INFO_NOVATION, tracker_build='valun-circulating-credit/3', transfer_version=3, bound_note_version=2)
        for novation in (None, v31, dict(INFO_NOVATION, transfer_version='4')):
            with self.subTest(novation=novation):
                self.wire.novation = novation                   # a fresh world and fresh records for each server
                self.wire.items.clear(), self.wire.tracker.clear(), self.wire.log.clear()
                shutil.rmtree(self.dir, ignore_errors=True)
                os.makedirs(self.dir)
                sid, pid = self.wire.cycle(), self.wire.payee_item()
                out = self.call(Ctx(process_consents=True, **SANDBOX), 'valun_status')
                self.assertEqual([e['id'] for e in out['consents_signed_now']], [sid])
                self.assertEqual(self.wire.items[pid]['state'], 'collecting')
                self.assertEqual([x for x in self.wire.log if pid in x[1] or x[1].startswith('/pilot/v1/deals/')], [],
                                 'not posted, not refused, its deal not read')
                logged = [e for e in self.records()['consent_log'] if e['id'] == pid]
                self.assertEqual([(e['decision'], e['code'], e['role']) for e in logged],
                                 [('waiting', 'feature_unavailable', 'payee')])

    def test_with_the_contract_a_payment_to_you_goes_past_the_gate(self):
        """The same item on a tracker v3.2 server: the client reads its deal (here it cannot, so it waits for a later
        run with that code, not feature_unavailable)."""
        pid = self.wire.payee_item()
        self.call(Ctx(process_consents=True, **SANDBOX), 'valun_status')
        self.assertEqual(self.wire.asked('GET', '/pilot/v1/deals/vd_' + 'f' * 16)[:1],
                         [('GET', '/pilot/v1/deals/vd_' + 'f' * 16, 'consents/1;roles=setoff,issuer,creditor')])
        self.assertEqual(self.wire.items[pid]['state'], 'collecting')
        logged = [e for e in self.records()['consent_log'] if e['id'] == pid]
        self.assertEqual([(e['decision'], e['code']) for e in logged], [('waiting', 'not_found')])

    def test_a_consent_above_the_cap_is_not_signed_from_a_read(self):
        ctx = Ctx(process_consents=True, **dict(SANDBOX, **{'consents.max_PC': 0}))
        sid = self.wire.cycle()
        out = self.call(ctx, 'valun_status')
        self.assertEqual(out['consents_signed_now'], [])
        self.assertEqual(self.wire.items[sid]['state'], 'collecting', 'waits for a full run, which refuses it')
        self.assertEqual(out['consent_policy_in_force']['max_PC'], 0)

    def test_unreadable_records_make_no_read_run(self):
        ctx = Ctx(process_consents=True, **SANDBOX)
        with open(os.path.join(self.dir, tools.RECORDS_FILE), 'w', encoding='utf-8') as f:
            f.write('{not json')
        self.wire.cycle()
        self.call(ctx, 'valun_status')
        self.assertEqual(self.wire.asked('GET', '/pilot/v1/consents'), [])


class RolesWithdrawn(Base):
    """D-3 b through the core: a client whose roles are all off, or whose process_consents is off, sends
    'consents/1;roles=' on its next request after GET /info, once; the server forgets its roles at once."""

    def declared_before(self):
        self.set_records(consent_roles_declared='setoff,issuer,creditor')
        self.wire.roles = {'setoff', 'issuer', 'creditor'}

    def headers_after_info(self):
        seen, out = False, []
        for method, route, caps in self.wire.log:
            if seen:
                out.append((method, route, caps))
            seen = seen or (method, route) == ('GET', '/pilot/v1/info')
        return out

    def test_process_consents_off_withdraws_the_roles_once(self):
        self.declared_before()
        ctx = Ctx(**SANDBOX)                                    # a policy, but process_consents off
        self.call(ctx, 'valun_status')
        after = self.headers_after_info()
        self.assertEqual(after[0][2], 'consents/1;roles=', after)
        self.assertEqual([c for _m, _r, c in after[1:]], [None] * (len(after) - 1))
        self.assertEqual(self.wire.roles, set(), 'the server forgot them at once')
        self.assertEqual(self.records()['consent_roles_declared'], '')
        self.wire.log.clear()
        self.call(ctx, 'valun_status')
        self.assertEqual([c for _m, _r, c in self.wire.log], [None] * len(self.wire.log), 'no header any more')

    def test_every_role_off_withdraws_them_too(self):
        self.declared_before()
        ctx = Ctx(process_consents=True, **{'consents.setoff': False, 'consents.as_issuer': False,
                                           'consents.as_new_creditor': False})
        self.call(ctx, 'valun_inbox')
        self.call(ctx, 'valun_status')
        self.assertEqual([c for _m, _r, c in self.wire.log if c is not None], ['consents/1;roles='])
        self.assertEqual(self.wire.roles, set())
        self.assertEqual(self.records()['consent_roles_declared'], '')

    def test_roles_on_are_declared_and_recorded(self):
        self.call(Ctx(process_consents=True, **dict(SANDBOX, **{'consents.as_new_creditor': False})), 'valun_status')
        self.assertEqual(self.wire.roles, {'setoff', 'issuer'})
        self.assertEqual(self.records()['consent_roles_declared'], 'setoff,issuer')
        self.assertNotIn('consents/1;roles=', [c for _m, _r, c in self.wire.log])

    def test_status_says_that_off_declares_no_role(self):
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            plugin._status(Ctx(**SANDBOX))
        self.assertIn('none signed by itself (process_consents off), and your client declares no consent role, so the '
                      'server proposes none that would wait for it', out.getvalue())
        self.assertEqual(self.wire.log, [], 'status asks the server nothing')

    def test_never_declared_nothing_is_withdrawn(self):
        self.call(Ctx(**SANDBOX), 'valun_status')
        self.assertEqual([c for _m, _r, c in self.wire.log], [None] * len(self.wire.log))
        self.assertIsNone(self.wire.roles)


# The core-CLI commands the vendored core names only in its docstrings (never in a text for the agent or the owner).
DOCSTRING_ONLY = ('`valun call`', '`valun config approvals`', '`valun sandbox seller`')


class HermesCommands(Base):
    def test_valun_status_names_this_profiles_hermes_commands(self):
        out = self.call(Ctx(process_consents=True, **SANDBOX), 'valun_status')
        how = out['consent_policy_in_force']['how_to_change']
        self.assertIn('`hermes -p valun-test config set plugins.entries.valun.settings.consents.<role> true|false`', how)
        self.assertIn('`hermes -p valun-test config set plugins.entries.valun.settings.consents.max_PC N`', how)
        self.assertIn('`hermes -p valun-test config set plugins.entries.valun.settings.process_consents true`', how)
        self.assertIn('`hermes -p valun-test valun status`', how)
        self.assertNotIn('`valun ', json.dumps(out), 'no core-CLI command anywhere in the result')

    def test_a_home_that_is_no_named_profile_gets_plain_hermes_commands(self):
        ctx = Ctx(process_consents=True, **SANDBOX)
        ctx.profile_name = None
        how = self.call(ctx, 'valun_status')['consent_policy_in_force']['how_to_change']
        self.assertIn('`hermes config set plugins.entries.valun.settings.consents.max_PC N`', how)

    def test_core_error_texts_name_this_profiles_commands(self):
        ctx = Ctx()
        out = tools.hermes_result(ctx, va.error_payload(vc.ValunError('attempt_outcome_unknown', 'may have applied')))
        self.assertIn('`hermes -p valun-test valun attempts list`, then `hermes -p valun-test valun attempts resolve`',
                      out['what_this_means'])
        out = tools.hermes_result(ctx, va.error_payload(vc.ValunError('not_connected', 'not enrolled'), 'run `valun join` '
                                                                                                          '(or `valun enroll`) in a terminal'))
        self.assertIn('`hermes -p valun-test valun join` (or `hermes -p valun-test valun enroll`)', out['what_this_means'])
        out = self.call(Ctx(participant_id=''), 'valun_inbox')            # not connected: the plugin's own hint
        self.assertEqual(out['error'], 'not_connected')
        self.assertIn('`hermes -p valun-test valun join`', out['what_this_means'])
        self.assertNotIn('`valun ', json.dumps(out))
        out = tools.hermes_result(ctx, va.error_payload(vc.ValunError(
            'unknown_attempt', 'your records hold no attempt #3 of this deal; see `valun attempts list`; nothing was changed')))
        self.assertEqual(out['message'], 'your records hold no attempt #3 of this deal; see `hermes -p valun-test valun '
                                         'attempts list`; nothing was changed')

    def test_the_doctor_names_a_bad_cap_and_its_fix(self):
        home = os.path.join(self.dir, 'home')
        os.makedirs(home)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {'HERMES_HOME': home}), mock.patch('sys.stdout', out):
            code = plugin._doctor(Ctx(process_consents=True, **dict(SANDBOX, **{'consents.max_PC': -5})))
        self.assertEqual(code, 1)
        self.assertIn('  FIX   consents.max_PC is -5, not a whole number of PC from 0 to 100000 or none, so your client signs '
                      'no consent above 0 PC until it is fixed. Fix: `hermes -p valun-test config set '
                      'plugins.entries.valun.settings.consents.max_PC 50` (or none for no cap).', out.getvalue())

    def test_every_core_command_in_the_core_texts_is_rewritten(self):
        """Every `valun ...` command the vendored core names (a new one fails here until hermes_words maps it)."""
        found = set()
        for name in sorted(os.listdir(os.path.join(ROOT, 'valun_core'))):
            if name.endswith('.py'):
                with open(os.path.join(ROOT, 'valun_core', name), encoding='utf-8') as f:
                    src = f.read()
                src = re.sub(r"""['"]\s*\n\s*['"]""", '', src)        # adjacent string literals, joined
                found |= set(re.findall(r'`valun [^`]*`', src))
        mapped = {core for core, *_rest in tools.CORE_COMMANDS}
        self.assertEqual(sorted(found - mapped - set(DOCSTRING_ONLY)), [])
        for core in mapped:
            self.assertNotIn('`valun ', tools.hermes_words(Ctx(), f'see {core}.'), core)

    def test_the_hermes_commands_named_exist(self):
        """Each Hermes command hermes_words and how_to_change name: a `hermes valun` subcommand the plugin's parser takes,
        or `hermes config set` of a setting plugin.yaml declares."""
        with open(os.path.join(ROOT, 'plugin.yaml'), encoding='utf-8') as f:
            manifest = f.read()
        schema = set(re.findall(r'^  ([a-z_][a-zA-Z_.]*):\s*$', manifest.split('config_schema:', 1)[1], re.M))
        ctx = Ctx()
        texts = [tools.how_to_change(ctx)] + [tools.hermes_words(ctx, core) for core, *_r in tools.CORE_COMMANDS]
        commands = {c for t in texts for c in re.findall(r'`(hermes [^`]*)`', t)}
        self.assertTrue(commands)
        for cmd in commands:
            words = cmd.split()
            self.assertEqual(words[:3], ['hermes', '-p', 'valun-test'], cmd)
            if words[3] == 'config':
                self.assertEqual(words[4], 'set', cmd)
                key = words[5][len('plugins.entries.valun.settings.'):]
                self.assertTrue(words[5].startswith('plugins.entries.valun.settings.'), cmd)
                keys = [f'consents.{r}' for r in tools.CONSENT_ROLES] if key == 'consents.<role>' else [key]
                for k in keys:
                    self.assertIn(k, schema, cmd)
            else:
                self.assertEqual(words[3], 'valun', cmd)
                argv = words[4:]
                if argv[:2] == ['attempts', 'resolve']:
                    argv += ['vd_0000000000000001', '#1', '--applied']
                p = argparse.ArgumentParser(prog='hermes valun')
                plugin._setup_cli(p, None)
                p.parse_args(argv)                           # exits (fails the test) on a command that does not exist


class PluginStorage(unittest.TestCase):
    """Inside Hermes (agent.secret_scope importable) without plugins.plugin_storage, the store is records_unreadable:
    its records never fall back to memory there. Outside Hermes they live in memory (the tests)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='valun-plugin-test-')
        self.net = mock.patch.object(vc.urllib.request, 'urlopen', side_effect=AssertionError('no request expected'))
        self.net.start()

    def tearDown(self):
        self.net.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def modules(self, hermes, storage):
        mods = {'plugins': None, 'plugins.plugin_storage': None, 'agent': None, 'agent.secret_scope': None}
        if hermes:
            pkg = types.ModuleType('agent')
            pkg.__path__ = []
            scope = types.ModuleType('agent.secret_scope')
            scope.get_secret = lambda name: None
            mods.update({'agent': pkg, 'agent.secret_scope': scope})
        if storage:
            pkg = types.ModuleType('plugins')
            pkg.__path__ = []
            ps = types.ModuleType('plugins.plugin_storage')
            ps.plugin_data_dir = lambda name: (os.makedirs(os.path.join(self.dir, name), exist_ok=True),
                                               os.path.join(self.dir, name))[1]
            mods.update({'plugins': pkg, 'plugins.plugin_storage': ps})
        return mock.patch.dict(sys.modules, mods)

    def store(self, **config):
        with mock.patch.object(tools, 'data_dir', tools.plugin_data_folder):
            return tools.HermesStore(Ctx(**config))

    def test_inside_hermes_without_plugin_storage_nothing_is_signed(self):
        with self.modules(hermes=True, storage=False):
            s = self.store()
            self.assertEqual(s.unreadable.code, 'records_unreadable')
            self.assertIn('plugins.plugin_storage', str(s.unreadable))
            with self.assertRaises(vc.ValunError):
                s.update(lambda state: None)
            with mock.patch.object(tools, 'data_dir', tools.plugin_data_folder), \
                    mock.patch.object(tools, 'secret_value', lambda n: {'VALUN_SIGNING_SECRET': S_SECRET.hex(),
                                                                        'VALUN_AGENT_TOKEN': AGENT_TOKEN}.get(n)):
                out = json.loads(tools.make_handlers(Ctx())['valun_respond']({'deal_id': 'vd_0000000000000001',
                                                                              'action': 'decline', 'reason': 'x'}))
                gate = plugin._gate(Ctx(), 'valun_propose', {'counterparty': 'x', 'role': 'payer', 'title': 't',
                                                             'description': 'd', 'deliverable': 'r', 'price': 1})
            self.assertEqual(out['error'], 'records_unreadable')
            self.assertEqual(gate['action'], 'block')
        self.assertEqual(os.listdir(self.dir), [])

    def test_outside_hermes_the_records_live_in_memory(self):
        with self.modules(hermes=False, storage=False):
            s = self.store()
        self.assertIsNone(s.unreadable)
        self.assertIsNone(s.path)

    def test_with_plugin_storage_the_records_are_in_its_folder(self):
        with self.modules(hermes=True, storage=True):
            s = self.store()
            s.update(lambda state: state.update(journal_seq=3))
        self.assertIsNone(s.unreadable)
        self.assertEqual(s.path, os.path.join(self.dir, 'valun', tools.RECORDS_FILE))


class ConsentCap(unittest.TestCase):
    """consents.max_PC, as `valun config consents.max_PC` takes it: a whole number of PC up to 100000, or none."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='valun-plugin-test-')
        self.patch = mock.patch.object(tools, 'data_dir', lambda: self.dir)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def policy(self, **config):
        return tools.HermesStore(Ctx(process_consents=True, **dict(SANDBOX, **config))).state['consent_policy']

    def test_the_cap_is_the_owners_setting(self):
        self.assertEqual(self.policy(**{'consents.max_PC': 12})['max_PC'], 12)
        self.assertEqual(self.policy(**{'consents.max_PC': '25'})['max_PC'], 25)
        self.assertEqual(self.policy(**{'consents.max_PC': 0})['max_PC'], 0)
        self.assertEqual(self.policy(**{'consents.max_PC': 100000})['max_PC'], 100000)
        self.assertEqual(self.policy()['max_PC'], tools.CONSENT_MAX_PC, 'unset: the default cap')

    def test_none_removes_the_cap(self):
        for value in (None, 'none', 'None'):       # `hermes config set ... none` stores null
            self.assertNotIn('max_PC', self.policy(**{'consents.max_PC': value}), value)

    def test_a_bad_value_fails_closed(self):
        for value in (-1, 100001, 2.5, True, 'abc', '1e3', '²', [5]):
            self.assertEqual(self.policy(**{'consents.max_PC': value})['max_PC'], 0, value)
            cap, problem = tools.consent_cap(Ctx(**{'consents.max_PC': value}))
            self.assertEqual(cap, 0)
            self.assertIn('signs no consent above 0 PC', problem)

    def test_the_cap_alone_overrides_a_kept_policy(self):
        tools.HermesStore(Ctx(process_consents=True)).update(
            lambda state: state.update(consent_policy={'setoff': True, 'as_issuer': False, 'as_new_creditor': False, 'max_PC': 7}))
        self.assertEqual(tools.HermesStore(Ctx(process_consents=True)).state['consent_policy']['max_PC'], 7)
        s = tools.HermesStore(Ctx(process_consents=True, **{'consents.max_PC': 3}))
        self.assertEqual(s.state['consent_policy'], {'setoff': True, 'as_issuer': False, 'as_new_creditor': False, 'max_PC': 3})

    def test_status_names_a_bad_value_and_its_fix(self):
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            plugin._status(Ctx(process_consents=True, **dict(SANDBOX, **{'consents.max_PC': 'abc'})))
        self.assertIn('max_PC 0', out.getvalue())
        self.assertIn("consents.max_PC is 'abc'", out.getvalue())
        self.assertIn('`hermes -p valun-test config set plugins.entries.valun.settings.consents.max_PC 50`', out.getvalue())
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            plugin._status(Ctx(process_consents=True, **dict(SANDBOX, **{'consents.max_PC': 20})))
        self.assertIn('max_PC 20', out.getvalue())
        self.assertNotIn('consents.max_PC is', out.getvalue())

    def test_the_manifest_declares_the_setting(self):
        with open(os.path.join(ROOT, 'plugin.yaml'), encoding='utf-8') as f:
            manifest = f.read()
        block = re.search(r'^  consents\.max_PC:\n((?:    .*\n)+)', manifest, re.M)
        self.assertIsNotNone(block)
        self.assertIn('type: int', block.group(1))


if __name__ == '__main__':
    unittest.main()
