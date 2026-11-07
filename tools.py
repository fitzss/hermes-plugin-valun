"""Hermes adapter over the framework-neutral Valun core (valun_core, vendored from valun-open).

Each handler returns a JSON string and never raises (Hermes plugin contract). The agent's signing key and token come
only from Hermes's secret store (agent.secret_scope.get_secret); they are never returned, logged or written by these
handlers. The plugin's own data directory holds only non-secret state: its record of credit notes it has issued (used
to check the server's credit-note templates).
"""
import json
import os
import threading
import time

try:
    from .valun_core import agent as va, client as vc  # inside the Hermes plugin package
except ImportError:  # standalone (tests)
    from valun_core import agent as va, client as vc

DEFAULT_SERVER = 'https://valun-desk.fly.dev'
SETUP_HINT = 'run `hermes valun join` (or `hermes valun enroll`) in a terminal'
# Re-exported for the plugin's CLI, hook and tests.
limits_body, loosens, recently_active, terms_text = va.limits_body, va.loosens, va.recently_active, va.terms_text
LIMIT_FIELDS, HONEST_LABELS, SETTLEMENT_NOTE, NOT_SETTLED = va.LIMIT_FIELDS, va.HONEST_LABELS, va.SETTLEMENT_NOTE, va.NOT_SETTLED


class HermesStore:
    """Non-secret settings from the plugin's Hermes config; secrets from Hermes's secret store; the credit-note
    record in the plugin data directory."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.state = {'server': (ctx.get_config('server_url', default=DEFAULT_SERVER) or DEFAULT_SERVER).rstrip('/'),
                      'participant_id': ctx.get_config('participant_id', default='') or ''}
        self._ledger_path = None
        try:
            from plugins.plugin_storage import plugin_data_dir
            self._ledger_path = os.path.join(str(plugin_data_dir('valun')), 'issued-notes.json')
            if os.path.exists(self._ledger_path):
                with open(self._ledger_path, encoding='utf-8') as f:
                    self.state['issued'] = json.load(f).get('issued', {})
        except Exception:
            self.state.setdefault('issued', {})

    def secret(self):
        from agent.secret_scope import get_secret
        return get_secret('VALUN_SIGNING_SECRET') or None

    def token(self):
        from agent.secret_scope import get_secret
        return get_secret('VALUN_AGENT_TOKEN') or None

    def save(self):
        if self._ledger_path:
            tmp = self._ledger_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'issued': self.state.get('issued', {})}, f, indent=1)
            os.replace(tmp, self._ledger_path)

    def save_credentials(self, secret_hex, token):
        raise vc.ValunError('not_here', 'credentials are stored only by `hermes valun join` / `enroll`, run by the operator')


def _approvals_text(ctx):
    if ctx.get_config('confirm_commitments', default=True):
        return ('Before you sign an offer, counter-offer, acceptance or credit note, Hermes asks your owner in this '
                'conversation and shows exactly what you would sign; wait for the answer.')
    return 'Hermes does not ask your owner before you sign (confirm_commitments is off).'


def agent_for(ctx):
    return va.ValunAgent(HermesStore(ctx), approvals=_approvals_text(ctx), setup_hint=SETUP_HINT,
                         check_link_hosts=bool(ctx.get_config('check_link_hosts', default=True)))


# Approvals bound to what the owner saw. The approval hook pins an acceptance to the terms hash (and a delivery
# acceptance to the deliverable hash) current when it asks the owner, and the handler signs with those pinned
# arguments once approved: if the offer or delivery changed in between, the core refuses to sign instead of signing
# the newer version. Kept in memory only (no secrets in it), keyed by the exact call; old entries are pruned.
_BOUND, _BOUND_LOCK, _BOUND_KEEP_S, _BOUND_MAX = {}, threading.Lock(), 24 * 3600, 256


def _call_key(name, args):
    return json.dumps([name, args or {}], sort_keys=True, ensure_ascii=False, default=str)


def bind_for_approval(ctx, name, args):
    """For the approval hook: the arguments the owner approves (pinned hashes added), remembered for the handler."""
    args = dict(args or {})
    bound = agent_for(ctx).bind(name, args)
    if bound != args:
        with _BOUND_LOCK:
            now = time.monotonic()
            for k in [k for k, (t, _b) in _BOUND.items() if now - t > _BOUND_KEEP_S]:
                del _BOUND[k]
            while len(_BOUND) >= _BOUND_MAX:
                del _BOUND[min(_BOUND, key=lambda k: _BOUND[k][0])]
            _BOUND[_call_key(name, args)] = (now, bound)
    return bound


def _approved_args(name, args):
    with _BOUND_LOCK:
        hit = _BOUND.pop(_call_key(name, args), None)
    return hit[1] if hit else args


def make_handlers(ctx):
    def wrap(name):
        def handler(args, **kwargs):
            result = agent_for(ctx).call_safe(name, _approved_args(name, dict(args or {})))   # never raises
            return json.dumps(result, ensure_ascii=False, default=str)
        return handler
    return {name: wrap(name) for name in va.TOOL_NAMES}


def limits_loosen(ctx, args):
    """For the approval hook: would this valun_set_limits call loosen a limit recorded earlier? Unknown -> True (ask)."""
    return agent_for(ctx).is_commitment('valun_set_limits', args)


def describe_commitment(ctx, tool_name, args):
    """Text for Hermes's approval prompt: exactly what the agent is about to sign, and what it means."""
    return agent_for(ctx).describe_commitment(tool_name, args)
