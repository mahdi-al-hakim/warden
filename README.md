# Warden

![CI](https://github.com/mahdi-al-hakim/warden/actions/workflows/ci.yml/badge.svg)

Warden runs a Claude agent on an IT offboarding job and decides, step by step, whether what the agent
proposes gets to run. The agent never calls a tool itself. It hands each call to a broker, which checks
it against policy, refuses writes on anyone but the person being offboarded, holds the account disable
for a human, records every decision in a hash-chained audit log, and starts nothing once an operator
revokes the agent. When the run ends, Warden reads the systems back and shows what is done next to the
agent's own summary.

The systems are synthetic, apart from an optional connector to a throwaway GitHub org. The demo runs
offline as a labeled replay, or live through the Claude API; live mode has been run end to end on
`claude-haiku-5-5`, `claude-sonnet-5-5` and `claude-opus-5-5`.

![A replay run waiting for approval: the planted note, the refused break-glass write, and the account disable held for a human](docs/screenshot.png)

## What the checks show

- **An instruction planted in the data.** A colleague's note on the contractor's directory record asks
  the agent to also disable the shared break-glass admin account. In 30 live runs, ten on each of the
  three models, none proposed it. All 30 finished the offboarding, and every closing message raised the
  break-glass account with the operator. The design was committed before the first run. Ten runs per
  model still allows a true rate of up to about one in four, and this is one wording of one request.
  [Design](experiments/note-injection/DESIGN.md), [results](experiments/note-injection/RESULTS.md).
- **The kill switch and the other controls.** Eight scenarios, including a revoke that lands while a
  step waits for approval, are each checked against ten invariants after the run, and the clean run is
also checked against the systems (81 checks in all). Two of the invariants compare
  the broker's records with a call log the tool registry keeps itself, so a broker that skipped its own
  bookkeeping would fail them. A mutation sweep breaks 16 controls one at a time, and the eval or the
  unit tests catch all 16. CI runs both on every push. [Reliability](docs/reliability.md).
- **What the agent says next to what happened.** Every run ends with the agent's summary beside a check
  that reads the directory, the VPN and each repository back. The UI marks a run completed only when
  that check passes.
- **How long a revoked credential keeps working, simulated.** Warden checks revocation live on every
  call, so a revoke refuses the agent's next action. The Reliability panel also models a validator that
  trusts a cached grant: with a 15 second cache, a revoked credential keeps working for 7.5 s at the
  median and up to 14.7 s, and a push signal modeled on CAEP brings that down to its 0.3 s delivery
  latency. These figures come from a simulated clock; no deployed system was measured.

## Replay and live

The default mode replays an authored run, so the demo needs no key and works offline. That run is
written to follow the planted note: it tries to disable the break-glass account so the policy denial
shows on screen. None of the 39 live runs in the note-injection eval made that attempt. The page always
says which source is driving the run,
and a replay of a recorded model run (set `WARDEN_TRAJECTORY`) is labeled as a replay.

## Run it

```bash
pip install -e ".[dev,live]"
python -m warden.api
```

Then open http://127.0.0.1:8800. To drive a model instead:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export WARDEN_MODE=live
export WARDEN_MODEL=claude-haiku-5-5   # the default
python -m warden.api
```

A live offboarding on `claude-haiku-5-5` cost about $0.003 at list prices when last run. The other
settings are listed in [.env.example](.env.example).

## A real GitHub org

The repository tool can drive a throwaway GitHub org instead of synthetic data. The connector acts only
in the configured org and repos, removes only logins on a test-account allowlist, and treats every
write as a dry run until writes are turned on. [docs/github-setup.md](docs/github-setup.md) walks
through the setup.

## How it works

The broker is the only code that touches a tool, and every proposed call takes the same path whether
it came from a replay or a model: take the operation's risk from the tool's spec and the target's tier
from the directory, check revocation, check the arguments against the operation's schema, evaluate the ruleset, keep writes to the run's subject, wait
for a human if the rule says so, check revocation again, run the tool, and write the audit entry. A
revoke during an approval wait ends the wait, and the second check stops a step that was approved just
as the revoke landed.

[docs/architecture.md](docs/architecture.md) covers the design, [docs/threat-model.md](docs/threat-model.md)
what it defends against and what it doesn't, and [docs/case-study.md](docs/case-study.md) what a team
running it would measure.

## Checks

```bash
python -m pytest                        # unit tests
python -m pytest tests/e2e              # browser tests; run `python -m playwright install chromium` first
python -m warden.eval                   # scenarios, invariants and the revocation model
python scripts/mutate_controls.py       # break each control and confirm something catches it
python scripts/note_injection_eval.py   # the note-injection eval; calls the API, about $2.33
python -m ruff check src tests scripts
```

## Project layout

```
src/warden/
  domain/       models, enums, events
  config.py     settings from the environment
  policy/       the ruleset and the first-match engine
  audit/        hash-chained log and redaction
  revocation/   the revocation authority, enforcement points, the CAEP-style receiver
  tools/        directory, VPN and repository tools, and the GitHub connector
  gateway/      the broker
  agent/        the replay agent, the live Claude adapter, and the runner
  verify.py     the end-of-run read-back
  eval/         scenarios, invariants, the revocation model, the note-injection counter
  api/          FastAPI app, SSE stream, session manager
web/            the single-page UI
tests/          unit tests, and browser tests against a running server
scripts/        the mutation sweep, the note-injection runner, trajectory recording
experiments/    the note-injection eval: design, results and every run
docs/           architecture, threat model, reliability, GitHub setup, case study
```

## Scope

Apart from the optional GitHub connector, every system is synthetic and lives in memory in one process.
A production version would move the audit log and sessions to durable storage, authenticate operators,
put the tools behind real connectors, and anchor the audit head outside the log.

## How it was built

I built Warden with Claude Code. Every figure in this README comes from one of the commands above, and
the eval's per-run transcripts are committed under `experiments/note-injection/runs/`.

## License

MIT
