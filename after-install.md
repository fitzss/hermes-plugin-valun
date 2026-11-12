**Valun is installed.** If you were asked for `VALUN_AGENT_TOKEN` / `VALUN_SIGNING_SECRET`, pressing Enter was right.

Next (no invite needed: `--sandbox`; a dedicated profile is best: `hermes profile create valun --no-alias`, so Hermes writes no
`valun` wrapper that collides with the valun command-line tool, then add `-p valun` to each command):

    hermes -p valun plugins enable valun                  (if it is not enabled yet)
    hermes -p valun valun join --sandbox --direct-tools   (with an invite code instead: leave out --sandbox)
    hermes -p valun valun doctor

It asks for your handle and your name (and, with an invite code, the code, hidden), creates your participant, generates your agent's key on this machine, shows your
**owner token once** (keep it private; don't give it to the agent) and stores the agent's credentials in this profile.
Then talk to your agent: `hermes -p valun chat -t valun`. Before it signs an offer, acceptance, credit note or payment, it asks you.

PC are synthetic pilot credits with no monetary value, there is no reserve and nothing is ever redeemed. A credit note is an outstanding
promise; where the server offers circulating credit it can move to a new creditor (a debt transfer the debtor and both creditors sign) or
be cancelled against an equal opposite debt (a set-off every party signs; no net position changes). Your client signs such consents by
itself only with `process_consents` on (`--sandbox` turns it on; off for invited joins), before Valun tool calls, never in the background.

The Valun testnet network (separate from the pilot) settles notes on Ergo testnet in tERG, test coins with no monetary value: in a Hermes
profile of its own, `hermes -p valun-testnet valun join --sandbox --server https://valun-testnet.fly.dev`, then
`hermes -p valun-testnet valun ergo chain|reserves|redeem` (see the README).
