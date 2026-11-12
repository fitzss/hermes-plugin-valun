"""0.5.0: the testnet network (valun-testnet-1, nanoERG of Ergo testnet: test coins with no monetary value) on the
valun 0.5.0 core. A Hermes profile keeps the network its join or enrollment named (the `network` setting, read into
the core's state by HermesStore), so its client signs for it after the joining session too; the consent cap is
PC-sized, in the network's unit (as `valun join` and `valun config consents.max_PC` do it); the plugin's own texts name
the unit; and `hermes valun ergo` settles on Ergo testnet (the owner, at a terminal; reserve and redeem send nothing
without --broadcast). On the pilot every text and cap is as in 0.4.5. Offline: a fake server, throwaway tokens."""
import json
import os
import unittest
from unittest import mock

import test_cli_040 as t

plugin, tools, va, vc = t.plugin, t.tools, t.va, t.vc
TESTNET = 'valun-testnet-1'
SCALE = 1_000_000
MANDATE_TESTNET = {k: (v * SCALE if isinstance(v, int) and not isinstance(v, bool) else v) for k, v in t.MANDATE.items()}


def testnet_routes(test):
    """join_routes for a testnet server: the join and the enrollment name valun-testnet-1, GET /info its unit."""
    routes = t.join_routes(test, owner={'participant': {'id': t.PID}, 'mandate': dict(MANDATE_TESTNET)},
                           put={'mandate': dict(MANDATE_TESTNET)}, enroll={'network': TESTNET, 'agent_token': 'agent-token-throwaway'})
    joined = dict(routes[('POST', '/pilot/v1/join-sandbox')], network=TESTNET)
    routes[('POST', '/pilot/v1/join-sandbox')] = joined
    routes[('GET', '/pilot/v1/info')] = dict(t.PINS, network=TESTNET, unit='nanoERG', features=['bound_notes/1', 'circulation/1'],
                                             novation=dict(t.INFO_NOVATION))
    return routes


class TestnetJoin(t.Base):
    def test_a_testnet_join_keeps_the_network_and_scales_the_consent_cap(self):
        ctx = t.Ctx()
        code, out = self.run_cli(ctx, t.Server(testnet_routes(self)), 'join', '--sandbox', '--handle', 's1', '--operator', 'Tester',
                                 '--server', t.SERVER, '--approvals', 'none')
        self.assertEqual(code, 0, out)
        self.assertEqual(ctx.config['network'], TESTNET)
        self.assertEqual(ctx.config['consents.max_PC'], 50 * SCALE, 'PC-sized: 50 PC is 50,000,000 nanoERG')
        self.assertIn('(at most 5000000 nanoERG (0.005 tERG) per deal, 40000000 nanoERG (0.04 tERG) owed in total)', out)
        self.assertIn('up to 50000000 nanoERG (0.05 tERG) each', out)
        self.assertNotIn(' PC', out.replace('max_PC', ''))
        # The next session: the core reads the network from the profile's settings and signs for the testnet.
        self.secrets = {'VALUN_SIGNING_SECRET': t.SECRET, 'VALUN_AGENT_TOKEN': 'agent-token-throwaway'}
        c = vc.Client(tools.HermesStore(ctx))
        self.assertEqual((c.network, c.unit, c.scale), (TESTNET, 'nanoERG', SCALE))
        self.assertEqual(tools.configured_policy(ctx)['max_PC'], 50 * SCALE)
        self.assertIsNone(tools.consent_cap(ctx)[1], 'the scaled cap is valid on the testnet')

    def test_a_pilot_join_is_as_before(self):
        ctx = t.Ctx()
        code, out = self.run_cli(ctx, t.Server(t.join_routes(self)), 'join', '--sandbox', '--handle', 's1', '--operator', 'Tester',
                                 '--server', t.SERVER, '--approvals', 'none')
        self.assertEqual(code, 0, out)
        self.assertEqual((ctx.config['network'], ctx.config['consents.max_PC']), ('valun-pilot-1', 50))
        self.assertIn('(at most 5 PC per deal, 40 PC owed in total)', out)
        self.assertIn('up to 50 PC each', out)

    def test_an_enrollment_on_the_testnet_keeps_its_network(self):
        ctx = t.Ctx()
        routes = {('POST', '/pilot/v1/enroll'): {'network': TESTNET, 'agent_token': 'agent-token-throwaway'}}
        with mock.patch.object(plugin.vc.Client, 'enroll', lambda self, pid, code: {'participant_id': pid, 'pubkey': '02' + 'ab' * 32,
                                                                                     'network': TESTNET}):
            code, out = self.run_cli(ctx, t.Server(routes), 'enroll', '--server', t.SERVER, '--participant', t.PID,
                                     '--code-from-stdin', stdin='enroll-code-throwaway\n')
        self.assertEqual(code, 0, out)
        self.assertEqual(ctx.config['network'], TESTNET)


class Settings(unittest.TestCase):
    def test_the_store_reads_the_network_and_none_means_the_pilot(self):
        with mock.patch.object(tools, 'data_dir', lambda: None):
            self.assertEqual(tools.HermesStore(t.Ctx(network=TESTNET)).state['network'], TESTNET)
            self.assertNotIn('network', tools.HermesStore(t.Ctx()).state, 'a profile from before 0.5: the pilot')
        self.assertEqual((tools.network(t.Ctx()), tools.unit(t.Ctx()), tools.scale(t.Ctx())), ('valun-pilot-1', 'PC', 1))
        self.assertEqual(tools.network(t.Ctx(network='valun-future-9')), 'valun-pilot-1', 'texts only: the core refuses it')

    def test_the_consent_cap_is_pc_sized_in_the_networks_unit(self):
        tn = lambda v: t.Ctx(network=TESTNET, **{'consents.max_PC': v})
        self.assertEqual(tools.consent_cap(tn('50000000')), (50_000_000, None))
        self.assertEqual(tools.consent_cap(tn(100_000 * SCALE)), (100_000 * SCALE, None))
        cap, problem = tools.consent_cap(tn(100_000 * SCALE + 1))
        self.assertEqual(cap, 0)
        self.assertIn('not a whole number of nanoERG from 0 to 100000000000 or none', problem)
        pilot = lambda v: t.Ctx(**{'consents.max_PC': v})
        self.assertEqual(tools.consent_cap(pilot(100000)), (100000, None))
        cap, problem = tools.consent_cap(pilot(100001))
        self.assertEqual(cap, 0)
        self.assertIn('not a whole number of PC from 0 to 100000 or none, so your client signs no consent above 0 PC', problem)

    def test_how_to_change_names_the_unit_and_its_limit(self):
        self.assertIn('(N: whole nanoERG up to 100000000000, or none for no cap)', tools.how_to_change(t.Ctx(network=TESTNET)))
        self.assertIn('(N: whole PC up to 100000, or none for no cap)', tools.how_to_change(t.Ctx()))

    def test_consent_and_decision_lines_name_the_unit(self):
        item = {'kind': 'setoff', 'id': 'vs_0000000000000001', 'your_roles': ['member'], 'context': {'amount': 1_000_000},
                'summary': {'your_net_change_PC': 0}}
        self.assertIn(', 1000000 nanoERG (your net change 0 nanoERG)', plugin._consent_line(item, 'nanoERG'))
        self.assertIn(', 1000000 PC (your net change 0 PC)', plugin._consent_line(item))
        report = {'signed': [{'kind': 'setoff', 'id': 'vs_1', 'role': 'member', 'amount': 1_000_000, 'your_net_change_PC': 0}]}
        self.assertIn('1000000 nanoERG (your net change 0 nanoERG)', plugin._decisions(report, 'nanoERG')[0])


class Ergo(t.Base):
    def setUp(self):
        super().setUp()
        self.secrets = {'VALUN_SIGNING_SECRET': t.SECRET, 'VALUN_AGENT_TOKEN': 'agent-token-throwaway'}
        self.ctx = t.Ctx(participant_id=t.PID, server_url=t.SERVER, network=TESTNET)

    def test_the_commands_parse(self):
        a = t.parse('ergo', 'redeem', 'sandbox-exact', '3000000', '--broadcast')
        self.assertEqual((a.valun_command, a.valun_ergo, a.valun_issuer, a.valun_amount, a.valun_broadcast),
                         ('ergo', 'redeem', 'sandbox-exact', 3_000_000, True))
        a = t.parse('ergo', 'reserve', '50000000')
        self.assertEqual((a.valun_ergo, a.valun_nano, a.valun_broadcast), ('reserve', 50_000_000, False))
        for action in ('address', 'chain', 'reserves'):
            self.assertEqual(t.parse('ergo', action).valun_ergo, action)

    def test_redeem_without_broadcast_is_a_dry_run_and_never_prints_the_signed_transaction(self):
        calls = []

        def redeem(c, issuer, amount, broadcast=False):
            calls.append((c.network, issuer, amount, broadcast))
            return {'tx_id': 'ab' * 32, 'amount': amount, 'signed': {'id': 'ab' * 32, 'inputs': ['...']}}
        with mock.patch.object(vc.Client, 'redeem', redeem):
            code, out = self.run_cli(self.ctx, t.Server({}), 'ergo', 'redeem', 'sandbox-exact', '3000000')
            self.assertEqual(code, 0, out)
            shown = json.loads(out)
            self.assertNotIn('signed', shown)
            self.assertEqual(shown['dry_run'], 'checked and signed here, NOT submitted: add --broadcast to submit it')
            code, out = self.run_cli(self.ctx, t.Server({}), 'ergo', 'redeem', 'sandbox-exact', '3000000', '--broadcast')
            self.assertEqual(code, 0, out)
            self.assertNotIn('dry_run', json.loads(out))
        self.assertEqual(calls, [(TESTNET, 'sandbox-exact', 3_000_000, False), (TESTNET, 'sandbox-exact', 3_000_000, True)])

    def test_address_chain_and_a_refusal(self):
        with mock.patch.object(vc.Client, 'ergo_address', lambda c: '3W' + 'x' * 49):
            code, out = self.run_cli(self.ctx, t.Server({}), 'ergo', 'address')
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)['network'], TESTNET)
        with mock.patch.object(vc.Client, 'chain', lambda c: (_ for _ in ()).throw(vc.ValunError('feature_unavailable', 'not on chain'))):
            code, out = self.run_cli(self.ctx, t.Server({}), 'ergo', 'chain')
        self.assertEqual(code, 1)
        self.assertIn('chain failed (feature_unavailable): not on chain', out)

    def test_not_connected_says_how_to_join_and_an_agent_session_is_refused(self):
        code, out = self.run_cli(t.Ctx(), t.Server({}), 'ergo', 'address')
        self.assertEqual(code, 1)
        self.assertIn('not connected to Valun yet', out)
        with mock.patch.dict(os.environ, {'HERMES_SESSION_ID': 'x'}):
            code, out = self.run_cli(self.ctx, t.Server({}), 'ergo', 'redeem', 'sandbox-exact', '3000000', '--broadcast')
        self.assertEqual(code, 1)
        self.assertIn('Refusing', out)


if __name__ == '__main__':
    unittest.main()
