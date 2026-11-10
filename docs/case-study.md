# Case study: governing an agent that offboards people

## The problem

When someone leaves a company, their access has to be pulled from a dozen systems fast. It is tedious,
it spans tools that do not talk to each other, and a missed step is a real security hole. It is exactly
the kind of work an AI agent could own end to end.

The reason companies do not hand it to one is trust. An agent that can disable accounts can also
disable the wrong account, get talked into an over-reach, or keep running after someone tries to stop
it. Giving a model that much reach without a way to see and stop what it does is a non-starter for any
security team. So the agent sits on the sidelines and a person keeps doing the toil.

## What Warden does about it

Warden runs the agent on the real task but puts every action through a control plane first. The agent
proposes each step; the control plane checks it against policy, holds the high-risk ones for a human,
writes each decision and outcome to a tamper-evident log, and lets an operator revoke the agent so the
next thing it tries is blocked. The agent does the work. The company keeps control and a provable record.

In the demo the agent offboards a contractor across a directory, VPN, and source control. It correctly
revokes routine access, it gets stopped when it over-reaches toward a privileged break-glass account,
and the final account-disable waits for a human to sign off. Point the repository tool at a real
GitHub org and enable writes, and the agent removes the contractor from real repos, which you can
verify on GitHub.

## How you would know it is working

A demo that looks good is not evidence. These are the measures that would tell a customer whether
Warden is actually helping, and each one is something the system already produces:

- **Toil removed.** Share of offboarding steps the agent completes without a human touching them. In
  the demo that is every routine revoke; only the one high-risk step needs a person.
- **Mistakes caught.** Over-reaches the policy layer blocks that would otherwise have gone through. The
  break-glass denial is one, and the audit log counts them.
- **Time to stop.** Seconds from an operator pulling the kill switch to the agent being unable to act.
  The reliability benchmark measures this, and shows the gap a cached credential opens and the signal
  that closes it.
- **Record completeness.** Share of executed actions that appear in the audit with a verifying hash
  chain. The eval asserts this holds on every run, and the negative tests prove the check has teeth.
- **Human load.** How often the agent interrupts a person for approval. Too often and it is not saving
  work; too rarely and the policy is too loose. It is a dial, and the ruleset sets it.

The point of stating these up front is that adopting an agent is a measurement problem before it is a
model problem. Warden is built so those numbers fall out of the run rather than being asserted after
the fact.

## What it is not

The company systems in the demo are synthetic except for the optional GitHub connector, and that is
disclosed on screen. State is in memory and scoped to one process. A production version would move the
audit log and sessions to durable storage, put the tools behind real connectors, and anchor the audit
head outside the log. Those limits are deliberate, so the governance path stays small enough to read
and test on its own. See [architecture.md](architecture.md) and [threat-model.md](threat-model.md).
