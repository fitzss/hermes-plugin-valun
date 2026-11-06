"""Offline checks of the plugin's limit logic: which valun_set_limits calls need the owner's approval (loosening)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools  # noqa: E402


class LimitsBody(unittest.TestCase):
    def test_maps_arguments_to_server_fields(self):
        body = tools.limits_body({'max_per_deal_as_payer': 20, 'min_price_as_payee': 15, 'goals': 'buy one brief',
                                  'clear': ['max_total_owed', 'not_a_field']})
        self.assertEqual(body, {'max_issue_per_deal': 20, 'min_price_as_payee': 15, 'max_issue_outstanding': None, 'notes': 'buy one brief'})

    def test_empty(self):
        self.assertEqual(tools.limits_body({}), {})


class Loosens(unittest.TestCase):
    cur = {'max_issue_per_deal': 20, 'min_price_as_payee': 15, 'notes': 'x'}

    def test_first_time_is_never_looser(self):
        self.assertFalse(tools.loosens({}, {'max_issue_per_deal': 25, 'min_price_as_payee': 0}))

    def test_caps(self):
        self.assertTrue(tools.loosens(self.cur, {'max_issue_per_deal': 21}))
        self.assertFalse(tools.loosens(self.cur, {'max_issue_per_deal': 20}))
        self.assertFalse(tools.loosens(self.cur, {'max_issue_per_deal': 10}))

    def test_floors(self):
        self.assertTrue(tools.loosens(self.cur, {'min_price_as_payee': 14}))
        self.assertFalse(tools.loosens(self.cur, {'min_price_as_payee': 30}))

    def test_clearing(self):
        self.assertTrue(tools.loosens(self.cur, {'max_issue_per_deal': None}))
        self.assertFalse(tools.loosens(self.cur, {'max_issue_outstanding': None}), 'nothing recorded, nothing loosened')

    def test_goals_alone_need_no_approval(self):
        self.assertFalse(tools.loosens(self.cur, {'notes': 'new goals'}))


if __name__ == '__main__':
    unittest.main()


class RecentlyActive(unittest.TestCase):
    def test_activity(self):
        from datetime import datetime, timedelta, timezone
        iso = lambda h: (datetime.now(timezone.utc) - timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        self.assertTrue(tools.recently_active(iso(1)))
        self.assertFalse(tools.recently_active(iso(30)))
        self.assertFalse(tools.recently_active(None))


class NewIsNotIdle(unittest.TestCase):
    def test_joined_recently_counts_as_active(self):
        from datetime import datetime, timedelta, timezone
        iso = lambda h: (datetime.now(timezone.utc) - timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        self.assertTrue(tools.recently_active(None, joined_at=iso(2)))
        self.assertFalse(tools.recently_active(None, joined_at=iso(48)))
