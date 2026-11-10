# Reliability checks and the revocation benchmark

Run everything with:

```bash
python -m warden.eval          # readable summary, exits non-zero if anything failed
python -m warden.eval --json   # the full report as JSON
```

There are two halves: governance invariants checked across scenarios, and a benchmark that times how
fast a revoked agent is actually stopped.

## Governance invariants

Each scenario runs the same offboarding task under a different condition, then is checked against
invariants that must always hold. The scenarios are:

- a clean run where the high-risk step is approved,
- a kill switch before the run starts,
- a kill switch that lands while a step waits for a human,
- an approval that times out,
- an upstream tool that fails mid-run.

The invariants are:

- the audit chain still verifies,
- no action starts after a revoke,
- every executed action is in the audit,
- no action is both executed and blocked,
- the privileged break-glass account is never disabled,
- every executed account disable went through a human approval first.

A check that can't fail isn't really checking anything, so the test suite also forces each one to fail
on a deliberately broken run. For example, it tampers with an audit entry and confirms the chain check
catches it, and it forces the privileged account off outside the broker and confirms the protection
check catches that too.

The revoke guarantee is about starting work. The broker commits no new action once a revoke lands: it
re-checks revocation under the lock right before running, and the "no action starts after a revoke"
invariant keys off that commit point. A call already in flight to a real external system cannot be
recalled, so it may complete after the revoke, and the audit records the revoke and the completion in
their true order. A test drives exactly this race and confirms the completed call is not counted as a
new action started after the revoke.

## The revocation benchmark

The benchmark answers one question: after you revoke an agent, how long does the thing enforcing access
keep honoring the revoked credential?

The benchmark runs the real enforcement code, but drives a deterministic clock instead of waiting on
real time, so it is reproducible and instant. The numbers follow from the cache lifetime and the signal
latency rather than from wall-clock noise. The same enforcement code runs on a real clock in the test
suite, at a small scale, and gives the same shape. A revoke can land at any point in a cached
credential's lifetime, so each trial ages the cache by a different amount before revoking, and the
results are summarized across trials.

Each trial carries a positive control. Before the revoke it confirms the boundary would have allowed
the action. If the boundary already denied, there is no real stop to measure: the trial is flagged
and left out of the median and range, so a trial where the agent would have stopped anyway never
counts toward the numbers. The control is load-bearing for the offline validator, whose cached grant
can expire on its own before a revoke; for the live gateway the stop is immediate by construction, so
its control is trivially satisfied and its time to stop is zero.

Three enforcement points are checked:

- **Gateway, live check.** Asks the authority on every call, so it stops on the next call.
- **Offline validator, no signal.** Trusts a cached grant until it expires. Without a push it keeps
  honoring a revoked credential for the rest of that lifetime. With a 15 second credential, the median
  time to stop is about half the lifetime and the worst case is close to the full lifetime.
- **Offline validator, CAEP signal.** A receiver modeled on CAEP and the Shared Signals Framework
  pushes a signal on revoke that invalidates the cached grant. The time to stop drops to about the
  delivery latency.

This is the same shape as revocation lag in real systems that validate tokens offline. A cached
decision is fast and cheap, and the cost is that it does not know about a revocation until the cache
expires, or until a signal tells it to. That signal is the fix.
