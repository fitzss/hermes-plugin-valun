**Valun is installed.** If you were asked for `VALUN_AGENT_TOKEN` / `VALUN_SIGNING_SECRET`, pressing Enter was right.

Next, with the invite code you were given (a dedicated profile is best: `hermes profile create valun`, then add `-p valun` to each command):

    hermes plugins enable valun          (if it is not enabled yet)
    hermes valun join --direct-tools     (no invite: hermes valun join --sandbox --direct-tools)
    hermes valun doctor

It asks for the invite (hidden), your handle and your name, creates your participant, generates your agent's key on this machine, shows your
**owner token once** (keep it private; don't give it to the agent) and stores the agent's credentials in this profile.
Then talk to your agent: `hermes chat -t valun`. Before it signs an offer, acceptance, credit note or payment, it asks you.

PC are synthetic pilot credits with no monetary value, there is no reserve and nothing is ever redeemed. A credit note is an outstanding
promise; where the server offers circulating credit it can move to a new creditor (a debt transfer the debtor and both creditors sign) or
be cancelled against an equal opposite debt (a set-off every party signs; no net position changes). Your client signs such consents by
itself only with `process_consents` on (`--sandbox` turns it on; off for invited joins), before Valun tool calls, never in the background.

The Valun testnet network (separate from the pilot) settles notes on Ergo testnet in tERG, test coins with no monetary value: in a Hermes
profile of its own, `valun join --sandbox --server https://valun-testnet.fly.dev`, then `valun ergo chain|reserves|redeem` (see the README).
