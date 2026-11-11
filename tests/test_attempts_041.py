"""0.4.1 (unreleased): `hermes valun attempts list | resolve` show a payment attempt the Valun server ended before it ever
reached the tracker as ended, not as MAY HAVE APPLIED, as the core CLI's `valun attempts` does since valun-open c2e0847
(valun-app readiness R-22). The core's attempts_overview() and resolution_plan() read GET /transfers/{id} for a transfer
attempt the records classify 'unknown' (it may have applied) whose server id they hold; a state the server sets only
before it ever submits a transfer to the tracker (vc.NEVER_APPLIED_TRANSFER) is shown as ended, with no resolution
advised; any other state, a view that is not this attempt, or a read that fails, shows it as before. Offline: a fake
server, throwaway keys, temporary folders; the records' classification is given (payment_attempts), the rest is the
core's."""
import copy
import os
import unittest
from unittest import mock

import test_cli_040 as t

plugin, va, vc = t.plugin, t.va, t.vc
PK = va.vb.public_key(bytes.fromhex(t.SECRET)).hex()               # this profile's key (throwaway)
X_PK = va.vb.public_key(va.vb.generate_secret()).hex()             # the issuer of the note paid with (throwaway)
M_PK = va.vb.public_key(va.vb.generate_secret()).hex()             # the payee (throwaway)
EDGE = f'{X_PK}>{PK}'                                                 # my claim on the issuer
DEAL, PAID_DEAL = 'vd_' + 'a' * 16, 'vd_' + 'c' * 16
SID = 'vt_0123456789abcdef'
T = 1762650000999
INFO = dict(t.PINS, network='valun-pilot-1', unit='PC', features=['bound_notes/1', 'circulation/1'],
            novation=dict(t.INFO_NOVATION))
WHY = {'void_rejected': 'your owner rejected it, before anyone else signed',
       'cancelled': 'your client cancelled it while it collected signatures'}
STILL_MAY = ': MAY HAVE APPLIED: your client pays this deal no more until you resolve it'
RESOLVE_ADVICE = '`hermes -p valun-test valun attempts resolve DEAL ATTEMPT --applied`'


def rows(paid=False):
    """R-22: my transfer attempt for DEAL (paying with X's note: my claim 3 -> 1 PC), with the server id my client
    recorded, that the records cannot prove dead ('unknown'), because a later payment of PAID_DEAL with the same note
    moved my claim 3 -> 1, the total it would have left. paid: DEAL itself is recorded as paid too."""
    return [{'deal_id': DEAL, 'ref': '#1', 'attempt_id': SID, 'route': 'transfer', 'edge': EDGE, 'prior': 3, 'total': 1,
             'timestamp': T, 'outcome': None, 'state': 'unknown', 'paid': paid, 'paid_by': None},
            {'deal_id': PAID_DEAL, 'ref': '#1', 'attempt_id': 'vt_' + 'c' * 16, 'route': 'transfer', 'edge': EDGE,
             'prior': 3, 'total': 1, 'timestamp': T + 60_000, 'outcome': None, 'state': 'applied', 'paid': True,
             'paid_by': '#1'}]


def view(state, **changes):
    """GET /pilot/v1/transfers/SID: the server's view of that attempt (me as payer), in `state`."""
    v = {'id': SID, 'kind': 'transfer', 'state': state, 'timestamp': T, 'your_roles': ['payer'],
         'deal': {'deal_id': DEAL}, 'edges': [{'edge': EDGE, 'new_total': 1}, {'edge': f'{X_PK}>{M_PK}', 'new_total': 2}]}
    v.update(changes)
    return v


class EndedAtServer(t.Base):
    def setUp(self):
        super().setUp()
        self.secrets = {'VALUN_SIGNING_SECRET': t.SECRET, 'VALUN_AGENT_TOKEN': 'agent-token-throwaway'}
        self.ctx = t.Ctx(participant_id=t.PID, server_url=t.SERVER, process_consents=True,
                         **{'consents.setoff': True, 'consents.as_issuer': True, 'consents.as_new_creditor': True})

    def run_attempts(self, transfer, *argv, paid=False, answers=()):
        """`hermes valun attempts ...` with the records' rows(paid) and GET /transfers/SID answering `transfer` (a view,
        or a ValunError to raise). Returns (exit code, output, server)."""
        server = t.Server({('GET', '/pilot/v1/info'): INFO, ('GET', f'/pilot/v1/transfers/{SID}'): transfer})
        with mock.patch.object(vc.Client, 'payment_attempts', side_effect=lambda **kw: copy.deepcopy(rows(paid))), \
                mock.patch.object(vc.Client, 'sync_edges', return_value={'edges': {}, 'doubtful': []}), \
                mock.patch('builtins.input', side_effect=list(answers)):
            code, out = self.run_cli(self.ctx, server, 'attempts', *argv, tty=bool(answers))
        return code, out, server

    def line(self, out):
        """The listed line of DEAL's attempt."""
        lines = out.splitlines()
        at = lines.index(f'deal {DEAL}' + (': recorded as paid' if f'deal {DEAL}: recorded as paid' in lines else ''))
        self.assertTrue(lines[at + 1].startswith('  #1 paying with the note of '), lines[at + 1])
        self.assertIn('your claim 3 -> 1 PC', lines[at + 1])
        return lines[at + 1]

    def test_every_state_the_server_ends_a_transfer_in_before_the_tracker_has_its_words(self):
        self.assertEqual(set(plugin.ENDED_AT_SERVER_WHY), set(vc.NEVER_APPLIED_TRANSFER))

    def test_an_attempt_the_server_ended_is_listed_as_ended_with_no_resolution_advised(self):
        for state, why in WHY.items():
            code, out, server = self.run_attempts(view(state), 'list')
            self.assertEqual(code, 0, out)
            self.assertTrue(self.line(out).endswith(
                f': ended by the server ({state}: {why}), which says it never reached the tracker, so it did not apply; '
                'your client cannot check that on the tracker, so it still pays this deal no more by itself'), out)
            self.assertNotIn('MAY HAVE APPLIED', out)
            self.assertNotIn('attempts resolve', out)                 # no resolution advised (in any form)
            self.assertIn(('GET', f'/pilot/v1/transfers/{SID}'), server.paths())

    def test_an_ended_attempt_of_a_paid_deal_has_nothing_to_resolve(self):
        code, out, _server = self.run_attempts(view('void_rejected'), 'list', paid=True)
        self.assertEqual(code, 0, out)
        self.assertTrue(self.line(out).endswith(
            f": ended by the server (void_rejected: {WHY['void_rejected']}), which says it never reached the tracker; the "
            'deal is recorded as paid: nothing to resolve'), out)
        self.assertNotIn('attempts resolve', out)

    def test_a_server_state_that_proves_nothing_still_says_may_have_applied(self):
        # States that can follow a tracker call or are no proof (failed, outcome_unknown, inconsistent), an owner's
        # decision (resolved_not_applied), a view that is not this attempt of mine, and a read that fails.
        cases = [view(s) for s in ('failed', 'outcome_unknown', 'resolved_not_applied', 'inconsistent')] + [
            view('void_rejected', timestamp=T + 1000), view('void_rejected', your_roles=['payee']),
            view('void_rejected', deal={'deal_id': PAID_DEAL}), view('void_rejected', edges=[{'edge': EDGE, 'new_total': 2}]),
            vc.ValunError('unavailable', 'try again later', 503)]
        for transfer in cases:
            code, out, server = self.run_attempts(transfer, 'list')
            self.assertEqual(code, 0, out)
            self.assertTrue(self.line(out).endswith(STILL_MAY), (transfer, out))
            self.assertIn(RESOLVE_ADVICE, out)
            self.assertNotIn('ended by the server', out)
            self.assertIn(('GET', f'/pilot/v1/transfers/{SID}'), server.paths(), 'the server was asked')

    def test_resolving_an_attempt_the_server_ended_says_so(self):
        # The owner may still record a decision (in the client's records only): told that the server ended it before it
        # reached the tracker, and, for --applied, that the client would then never pay the deal again.
        code, out, server = self.run_attempts(view('void_rejected'), 'resolve', DEAL, '#1', '--applied', answers=['n'])
        self.assertEqual(code, 3, out)
        self.assertIn('  The server ended this transfer as void_rejected before it ever reached the tracker: on its word '
                      'the payment did not apply, and the server has nothing to resolve. Your client cannot check that on '
                      'the tracker, so it still counts it as a payment that may have applied.', out)
        self.assertIn('  The server says this payment never applied: recorded as applied, your client never pays this deal '
                      'again.', out)
        this = [x for x in out.splitlines() if x.endswith('<- this one')]
        self.assertEqual(len(this), 1, out)
        self.assertIn(f"ended by the server (void_rejected: {WHY['void_rejected']}), ", this[0])
        self.assertIn('Nothing was changed.', out)
        self.assertEqual([p for m, p in server.paths() if m != 'GET'], [])
        code, out, server = self.run_attempts(view('void_rejected'), 'resolve', DEAL, '#1', '--not-applied', answers=['n'])
        self.assertEqual(code, 3, out)
        self.assertIn('  The server ended this transfer as void_rejected before it ever reached the tracker', out)
        self.assertNotIn('recorded as applied, your client never pays', out)
        self.assertEqual([p for m, p in server.paths() if m != 'GET'], [])
        # failed can follow a tracker call: the plan says, as before, that the server holds it ended and that the
        # client still counts it as a payment that may have applied, with no word that it never applied.
        code, out, _server = self.run_attempts(view('failed'), 'resolve', DEAL, '#1', '--applied', answers=['n'])
        self.assertEqual(code, 3, out)
        self.assertIn('  The server holds this transfer as failed: it ended there, so the server has nothing to resolve; '
                      'your client still counts it as a payment that may have applied.', out)
        self.assertNotIn('never reached the tracker', out)
        self.assertNotIn('The server says this payment never applied', out)
        this = [x for x in out.splitlines() if x.endswith('<- this one')]
        self.assertTrue(this[0].endswith(STILL_MAY + '   <- this one'), this)


if __name__ == '__main__':
    unittest.main()
