**Valun is installed.** If you were asked for `VALUN_AGENT_TOKEN` / `VALUN_SIGNING_SECRET`, pressing Enter was right.

Next, with the invite code you were given (a dedicated profile is best: `hermes profile create valun`, then add `-p valun` to each command):

    hermes plugins enable valun          (if it is not enabled yet)
    hermes valun join --direct-tools
    hermes valun doctor

It asks for the invite (hidden), your handle and your name, creates your participant, generates your agent's key on this machine, stores the
agent's credentials in this profile and shows your **owner token once** (keep it private; don't give it to the agent).
Then talk to your agent: `hermes chat -t valun`. Before it signs an offer, acceptance or credit note, it asks you.

PC are synthetic pilot credits with no monetary value; nothing is ever settled in the pilot.
