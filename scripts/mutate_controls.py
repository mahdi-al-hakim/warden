"""Break each control in turn and check that the eval or the unit tests notice.

Every mutation is applied to a throwaway copy of the repository, never to the working tree. A
mutation counts as caught when `python -m warden.eval` or the unit tests fail on the mutated copy.
A mutation whose target text is no longer in the code makes the sweep fail, so it cannot go stale
quietly.

    python scripts/mutate_controls.py           # print the table
    python scripts/mutate_controls.py --check   # also exit 1 if any mutation survives
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    control: str
    path: str
    old: str
    new: str


MUTATIONS = [
    Mutation(
        "Revocation is checked before an action runs",
        "src/warden/gateway/broker.py",
        """            if not self.gateway.is_allowed(agent_id):
                return self._blocked(session, agent_id, action)
            # Commit point.""",
        """            # Commit point.""",
    ),
    Mutation(
        "A revoke ends a pending approval at once",
        "src/warden/gateway/broker.py",
        """            if pending_agent == agent_id and not future.done():
                future.set_result((False, None))""",
        """            pass""",
    ),
    Mutation(
        "Writes are scoped to the run's subject",
        "src/warden/gateway/broker.py",
        """        if self._out_of_subject_scope(session, action):""",
        """        if False:""",
    ),
    Mutation(
        "Privileged accounts are protected by policy",
        "src/warden/policy/rules.py",
        """        risk_tiers=(RiskTier.MEDIUM, RiskTier.HIGH),
        arg_match=(("account_tier", "privileged"),),""",
        """        risk_tiers=(RiskTier.MEDIUM, RiskTier.HIGH),
        arg_match=(("account_tier", "never-matches"),),""",
    ),
    Mutation(
        "The agent cannot set its own account tier",
        "src/warden/gateway/broker.py",
        """        resolved_args = {k: v for k, v in arguments.items() if k != "account_tier"}
        if problem is None:
            tier = self.registry.resolve_account_tier(resolved_args)
            if tier is not None:""",
        """        resolved_args = dict(arguments)
        if problem is None:
            tier = self.registry.resolve_account_tier(resolved_args)
            if tier is not None and "account_tier" not in resolved_args:""",
    ),
    Mutation(
        "High-risk steps wait for a human",
        "src/warden/gateway/broker.py",
        "await self._await_approval(session, action, agent_id)",
        '(True, "nobody", False)',
    ),
    Mutation(
        "Every executed action is written to the audit log",
        "src/warden/gateway/broker.py",
        """        self.audit.append(
            session_id=session.session_id,
            kind=EventType.ACTION_EXECUTED,""",
        """        (lambda **_: None)(
            session_id=session.session_id,
            kind=EventType.ACTION_EXECUTED,""",
    ),
    Mutation(
        "The audit chain detects tampering",
        "src/warden/audit/log.py",
        """    def verify(self) -> VerifyResult:""",
        """    def verify(self) -> VerifyResult:
        return VerifyResult(ok=True, checked=len(self._events))

    def _unused_verify(self) -> VerifyResult:""",
    ),
    Mutation(
        "Arguments are checked before anything reads them",
        "src/warden/tools/registry.py",
        """        found = self._tools.get(tool)
        if found is None or operation not in found.operations:
            return None
        if not isinstance(arguments, dict)""",
        """        return None
        found = self._tools.get(tool)
        if found is None or operation not in found.operations:
            return None
        if not isinstance(arguments, dict)""",
    ),
    Mutation(
        "A repository login is judged as the person it belongs to",
        "src/warden/tools/access_ops.py",
        """        user_id = self.canonical_id(identifier)
        return self._users[user_id]["account_tier"] if user_id else "unknown\"""",
        """        user_id = identifier if identifier in self._users else None
        return self._users[user_id]["account_tier"] if user_id else "unknown\"""",
    ),
    Mutation(
        "The end-of-run check reads the systems",
        "src/warden/verify.py",
        """        "complete": all(c["status"] == DONE for c in checks),""",
        """        "complete": True,""",
    ),
    Mutation(
        "A revoked agent is not asked for another step",
        "src/warden/agent/runner.py",
        """        if not broker.gateway.is_allowed(session.agent_id):
            session.status = SessionStatus.REVOKED""",
        """        if False:
            session.status = SessionStatus.REVOKED""",
    ),
    Mutation(
        "Hitting the step limit is a failure",
        "src/warden/agent/runner.py",
        """    session.status = SessionStatus.FAILED
    await finish(f"Run stopped: the agent reached the {max_steps}-step limit.", failed=True)""",
        """    session.status = SessionStatus.COMPLETED
    await finish("Reached the step limit.")""",
    ),
    Mutation(
        "A model turn that ran out of tokens is not a finish",
        "src/warden/agent/claude.py",
        """        if stop == "max_tokens":""",
        """        if stop == "never":""",
    ),
    Mutation(
        "Thinking blocks go back to the model unchanged",
        "src/warden/agent/claude.py",
        """        self._messages.append({"role": "assistant", "content": response.content})""",
        """        self._messages.append({"role": "assistant", "content": [
            b for b in response.content if getattr(b, "type", None) != "thinking"]})""",
    ),
    Mutation(
        "Secrets are redacted before they reach the audit log",
        "src/warden/audit/redact.py",
        """        if isinstance(key, str) and _is_secret_key(key):""",
        """        if False:""",
    ),
]


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("WARDEN_", "ANTHROPIC_"))}
    env["PYTHONPATH"] = str(cwd / "src")
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=600)


def check_one(m: Mutation) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "warden"
        for part in ("src", "tests", "pyproject.toml"):
            src = ROOT / part
            if src.is_dir():
                shutil.copytree(src, copy / part, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                copy.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, copy / part)
        target = copy / m.path
        text = target.read_text(encoding="utf-8")
        if m.old not in text:
            raise SystemExit(f"mutation target not found, update the sweep: {m.control}")
        target.write_text(text.replace(m.old, m.new, 1), encoding="utf-8")

        ev = _run([sys.executable, "-m", "warden.eval", "--json"], copy)
        failed_invariants: list[str] = []
        try:
            report = json.loads(ev.stdout)
            for s in report.get("scenarios", []):
                failed_invariants += [i["name"] for i in s.get("invariants", []) if not i["passed"]]
        except json.JSONDecodeError:
            failed_invariants = ["(eval crashed)"] if ev.returncode else []
        unit = _run([sys.executable, "-m", "pytest", "tests/unit", "-q", "-x",
                     "-p", "no:cacheprovider"], copy)
    caught_by = sorted(set(failed_invariants))
    if ev.returncode and not caught_by:
        caught_by = ["eval"]
    if unit.returncode:
        caught_by.append("unit tests")
    return {"control": m.control, "caught": bool(caught_by), "caught_by": caught_by}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if any mutation survives")
    args = parser.parse_args()
    rows = [check_one(m) for m in MUTATIONS]
    print("| Control broken | Caught by |")
    print("|---|---|")
    for r in rows:
        print(f"| {r['control']} | {', '.join(r['caught_by']) if r['caught'] else 'NOT CAUGHT'} |")
    survivors = [r for r in rows if not r["caught"]]
    print(f"\n{len(rows) - len(survivors)} of {len(rows)} mutations caught.")
    return 1 if (args.check and survivors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
