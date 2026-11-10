# Threat model

This note says what Warden defends against, what it assumes, and what is out of scope. It is meant to
be clear about the edges and not claim more than the code actually does.

## What it defends against

**An agent that proposes an action it should not take.** The agent cannot reach a tool directly. It
proposes a call, and the broker evaluates it against policy before anything runs. In the demo the agent
tries to disable a privileged break-glass account, and policy denies it. The tool spec sets each
operation's risk, and the agent's request cannot change it.

**An agent that forges context to dodge a rule.** The broker drops any account tier the agent
supplied, then resolves the target account's tier from the directory itself and stamps that on the
action. The agent's own tier never reaches policy, whether or not the target resolves to a user.

**A high-risk action taken without a human.** Actions above a threshold wait for an explicit approval.
If no one answers within the timeout, the default is to deny.

**An agent that keeps acting after it is revoked.** The broker checks revocation before evaluating an
action and again right before executing it, under a per-session lock. A revoke that lands during an
approval wait still blocks the action, even if a human then approves it. The guarantee is that no
action *starts* after a revoke; a call already in flight to a real external system cannot be recalled,
and the audit records the revoke and the completion in their true order.

**Quiet tampering with the record.** The audit log is hash-chained. Any edit or insertion, and any
deletion that leaves a later entry in place, is caught by verify. Dropping the most recent entries
leaves a shorter but internally consistent chain, so that case, like a full rewrite, is caught only by
anchoring the head hash outside the log (see Out of scope).

**Secrets in the log.** Values under known secret keys are removed before an entry is written, and
emails and long digit runs are partially masked.

## What it assumes

- The broker and the control plane are trusted. The agent is not.
- The policy ruleset is correct for the environment. Warden enforces the ruleset it is given.
- The operator pulling the kill switch is authorized to do so. The demo does not model operator
  authentication.
- Target identifiers are the directory's canonical keys. The broker resolves an account's tier by
  looking the identifier up in the directory, so the privileged-account protection assumes the
  identifier is already canonical. The synthetic directory matches keys exactly; a real directory
  would canonicalize an identifier before resolving it, the same way its own lookups do.
- The synthetic tools stand in for real connectors. In the demo they change in-memory state.

## Out of scope

**A full-chain rewrite of the audit log.** A hash chain catches a local edit. An attacker who can
rewrite every later hash would defeat a plain chain. Closing that gap means anchoring the head hash
somewhere outside the log, such as a notarization service or append-only storage. That anchoring is not
implemented here and is called out in the audit module itself.

**Durability and multi-process state.** Everything is in memory and scoped to one process. There is no
database, no leader election, and no recovery after a crash. A real deployment would move audit,
sessions, and tool state to durable storage.

**Operator identity and authorization.** Approvals and revokes are accepted from the API without
authenticating who sent them. A real deployment would put this behind authentication and record the
operator identity on the action, which the audit schema already has a field for.

**Network and transport security.** The demo serves plain HTTP on localhost. TLS, auth, rate limiting,
and the rest of the edge belong to the deployment, not to this code.

**Prompt injection into the agent.** Warden controls what the agent is allowed to do. It does not try
to control what the model decides to attempt. If a model is talked into proposing a harmful action,
policy and approval are the backstop. The design assumes the agent can be wrong or steered, so the
control sits at the boundary where actions take effect.
