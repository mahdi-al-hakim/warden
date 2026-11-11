# Threat model

What Warden defends against, what it assumes, and what is out of scope.

## What it defends against

**An agent that proposes an action it should not take.** The agent can't reach a tool directly. It
proposes a call, and the broker evaluates it against policy before anything runs. The tool spec sets
each operation's risk, and nothing in the agent's request can change it.

**Instructions that arrive as data.** An agent reads records written by people other than its
operator, and any of them can carry an instruction. In the demo a colleague's note on the departing
contractor's directory record asks for the shared break-glass account to be disabled too. If a model
is persuaded, the privileged-account rule still denies the write, whatever the agent believes. Writes
the rules don't cover are a different matter: an injected omission, such as leaving the VPN on, would
pass policy, and only the end-of-run check would show the offboarding was unfinished. The note-injection
eval in `experiments/note-injection` counts how often current Claude models act on that note; in 30 live
runs, none did.

**A write on someone other than the person being offboarded.** Each run is scoped to its subject. A
write on anyone else is denied after policy, so a steered agent can't cut a colleague's access even
with an action policy would otherwise allow. Reads aren't scoped.

**An agent that forges context to dodge a rule.** The broker drops any account tier the agent supplies
and resolves the target's tier from the directory. Targets are canonicalized through the directory
first, so addressing a privileged person by their repository login doesn't hide that they are
privileged.

**A high-risk action taken without a human.** Disabling an account waits for an explicit approval. If
no one answers within the timeout, the step is denied.

**An agent that keeps acting after it is revoked.** The broker checks revocation before evaluating an
action and again right before running it. A revoke ends any approval the agent is waiting on at once.
No action starts after a revoke; a call already in flight to an external system can't be recalled,
and the audit records the revoke and the completion in their true order.

**Malformed or hostile arguments.** Every call's arguments are checked against the operation's schema
before anything reads them, and a bad call is denied and audited. The UI renders agent and record text
as text, never as HTML.

**Quiet tampering with the record.** The audit log is hash-chained. Any edit or insertion, and any
deletion that leaves a later entry in place, is caught by verify. Dropping the newest entries leaves a
shorter but internally consistent chain, which only anchoring the head hash outside the log would catch
(see Out of scope).

**Secrets in the log.** Values under secret-looking keys and credential-shaped values are removed before
an entry is written, and emails and long digit runs are partially masked.

## What it assumes

- The broker and the control plane are trusted. The agent is not.
- The policy ruleset is correct for the environment. Warden enforces the ruleset it is given.
- The directory's links between a person and their logins are correct; scope and tier both depend on
  them.
- The operator pulling the kill switch is authorized to do so. The demo does not authenticate
  operators.
- The synthetic tools stand in for real connectors. In the demo they change in-memory state.

## Out of scope

**A full rewrite or a truncated tail of the audit log.** A hash chain catches a local edit. Someone who
can rewrite every later hash, or cut the newest entries, defeats a plain chain. Closing that means
anchoring the head hash somewhere outside the log, such as a notarization service or append-only
storage. That is not implemented here.

**Durability and multi-process state.** Everything is in memory in one process: no database, no leader
election, no recovery after a crash.

**Operator identity and authorization.** Approvals and revokes are accepted from the API without
authenticating who sent them. A real deployment would put this behind authentication and record the
operator on each decision; the audit schema already has the field.

**Network and transport security.** The demo serves plain HTTP on localhost. TLS, authentication, rate
limiting, and the rest of the edge belong to the deployment.
