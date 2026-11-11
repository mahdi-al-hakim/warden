# Case study: an agent that offboards people

## The problem

When someone leaves, their access has to come out of several systems quickly, and a missed step leaves
a working account behind. The work is repetitive and spread across tools that don't share state, which
makes it a natural job for an agent. Teams hold back because an agent that can disable accounts can
disable the wrong one, follow an instruction it found in a record, or keep going after someone tries to
stop it.

## What Warden does

Every step the agent proposes goes through a broker before it runs. The ruleset decides whether it can
run, writes are limited to the person being offboarded, the account disable waits for a person, every
decision goes into a hash-chained audit log, and a revoke refuses the agent's next action. At the end
of the run Warden reads the systems back, so the operator sees what was done alongside what the agent
says it did.

In the demo the agent offboards a contractor across a directory, a VPN and source control. The authored
replay also tries to disable a privileged break-glass account, so the policy denial is visible; in the
note-injection eval no live Claude run made that attempt. With the GitHub connector on and writes
enabled, the agent removes the contractor from real repositories in a throwaway org.

## What a team would measure

Each of these comes from data Warden already records.

- **Steps done without a person.** The share of offboarding steps that ran without an approval. In the
  demo every routine revoke does, and one step waits for a person.
- **Refusals.** Writes the ruleset or the subject scope refused, counted from the audit log.
- **Unfinished runs.** Runs where the end-of-run check found access left behind, whatever the agent's
  summary said.
- **Time to stop.** How long a revoked agent can still act. Warden checks revocation live, so the next
  action is refused; the reliability panel models how much longer a cached credential would keep
  working.
- **Audit coverage.** Every executed action in a verifying audit chain. The eval checks this in every
  scenario.
- **Approval load.** How often the agent stops for a person. Too often and it saves little work; too
  rarely and the rules are too loose. The ruleset sets it.

## Limits

Apart from the optional GitHub connector, the systems are synthetic, and the page says so. State is in
memory in one process. A production version would need durable storage for the audit log and sessions,
operator authentication, real connectors, and an audit head anchored outside the log. See
[architecture.md](architecture.md) and [threat-model.md](threat-model.md).
