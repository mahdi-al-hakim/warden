"""Tests for the audit log: chaining, tamper detection, and redaction."""

from __future__ import annotations

import importlib
import random
import time

from warden.audit import AuditLog, redact

# The package re-exports the function under the module's name, so reach the module explicitly.
redaction = importlib.import_module("warden.audit.redact")


def _log_with_three() -> AuditLog:
    log = AuditLog()
    log.append(session_id="ses_1", kind="action.executed", actor="agent_x", data={"op": "revoke"})
    log.append(session_id="ses_1", kind="approval.resolved", actor="operator_a", data={"ok": True})
    log.append(session_id="ses_1", kind="action.executed", actor="agent_x", data={"op": "disable"})
    return log


def test_appends_chain_and_verify_passes():
    log = _log_with_three()
    events = log.events
    assert [e.seq for e in events] == [0, 1, 2]
    assert events[0].prev_hash != events[1].prev_hash
    assert events[1].prev_hash == events[0].hash
    result = log.verify()
    assert result.ok is True
    assert result.checked == 3


def test_edit_to_content_is_detected():
    log = _log_with_three()
    # Simulate an attacker editing a stored event's data without recomputing its hash.
    log._events[1].data["ok"] = False
    result = log.verify()
    assert result.ok is False
    assert result.first_bad_seq == 1
    assert "content" in result.reason


def test_edited_hash_is_detected():
    log = _log_with_three()
    log._events[2].hash = "0" * 64
    result = log.verify()
    assert result.ok is False
    assert result.first_bad_seq == 2


def test_deleting_an_entry_breaks_the_chain():
    log = _log_with_three()
    del log._events[1]
    result = log.verify()
    assert result.ok is False
    # The former third entry now sits at index 1 but still carries seq 2.
    assert result.first_bad_seq in (1, 2)


def test_actor_is_recorded():
    log = AuditLog()
    ev = log.append(session_id="s", kind="approval.resolved", actor="operator_a", data={})
    assert ev.actor == "operator_a"


def test_secrets_are_redacted_and_emails_masked():
    log = AuditLog()
    ev = log.append(
        session_id="s",
        kind="action.executed",
        data={"token": "sk-live-123", "user_email": "jordan.lee@example.com", "note": "ok"},
    )
    assert ev.data["token"] == "[redacted]"
    assert ev.data["user_email"] == "j***@example.com"
    assert ev.data["note"] == "ok"
    # The redacted event still verifies, because the hash is taken over the stored (safe) content.
    assert log.verify().ok is True


def test_redact_is_recursive_and_masks_long_digits():
    out = redact({"nested": {"api_key": "abc", "phone": "15551234567"}, "list": [{"secret": "x"}]})
    assert out["nested"]["api_key"] == "[redacted]"
    assert out["nested"]["phone"].endswith("4567")
    assert out["nested"]["phone"].startswith("***")
    assert out["list"][0]["secret"] == "[redacted]"


def test_redacts_secret_keys_by_fragment_not_exact_name():
    out = redact(
        {
            "access_token": "abc",
            "client_secret": "xyz",
            "private_key": "-----BEGIN-----",
            "aws_secret_access_key": "k",
            "user_id": "jordan.lee",
            "max_tokens": 1024,
        }
    )
    assert out["access_token"] == "[redacted]"
    assert out["client_secret"] == "[redacted]"
    assert out["private_key"] == "[redacted]"
    assert out["aws_secret_access_key"] == "[redacted]"
    # Benign keys are left alone, including one that contains a secret fragment.
    assert out["user_id"] == "jordan.lee"
    assert out["max_tokens"] == 1024


def test_redacts_credential_shaped_values_under_harmless_keys():
    out = redact(
        {
            "note": "the key is sk-ant-abc123DEF456ghi789 keep it safe",
            "header": "Authorization: Bearer abcDEF123456xyz",
            "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ4In0.sig12345",
        }
    )
    assert "sk-ant" not in out["note"]
    assert "[redacted]" in out["note"]
    assert "[redacted]" in out["header"]
    assert "eyJ" not in out["jwt"]


def test_redacts_aws_and_github_tokens_under_harmless_keys():
    out = redact(
        {
            "note": "token github_pat_11ABCDE0000fGHIJKLMNO in a stack trace",
            "log": "aws creds AKIAIOSFODNN7EXAMPLE leaked in a traceback",
            "header": "x-gh-token: ghu_abcDEF123456xyz",
        }
    )
    assert "github_pat_" not in out["note"]
    assert "[redacted]" in out["note"]
    assert "AKIA" not in out["log"]
    assert "[redacted]" in out["log"]
    assert "ghu_" not in out["header"]


def test_empty_log_verifies():
    assert AuditLog().verify().ok is True


def test_token_counts_are_kept_and_nested_secrets_are_masked():
    out = redact({
        "usage": {"input_tokens": 1041, "output_tokens": 79, "cache_read_input_tokens": 0},
        "batches": [["sk-ant-abc123DEF456ghi789", "ok"], ("ghp_abcdefghijk",)],
    })
    assert out["usage"] == {"input_tokens": 1041, "output_tokens": 79,
                            "cache_read_input_tokens": 0}
    assert out["batches"][0][0] == "[redacted]" and out["batches"][0][1] == "ok"
    assert out["batches"][1][0] == "[redacted]"


# Redaction runs on the server's event loop, so its cost is a liveness property of the kill switch.
# A long string with no spaces used to take about seven seconds in the email pattern alone, because
# re.sub retried a match from every position in the run. These pin the speed, the size limit, and
# that the faster code masks exactly what the plain regex substitution would.

_ADVERSARIAL = {
    "letters": "a" * 100_000,
    "digits": "1" * 100_000,
    "dotted": "a." * 50_000,
    "after an at": "x@" + "a." * 50_000,
    "before an at": "a" * 100_000 + "@",
    "jwt starts": "eyJ-" * 25_000,
}


def test_redaction_is_linear_on_adversarial_input():
    for name, text in _ADVERSARIAL.items():
        started = time.perf_counter()
        redaction._mask_text(text)
        elapsed = time.perf_counter() - started
        # Linear code takes milliseconds on 100 KB; the quadratic version took seconds per pass.
        assert elapsed < 1.0, f"{name}: {elapsed:.2f}s on 100 KB"


def test_a_value_over_the_limit_is_replaced_by_its_size():
    huge = "a" * (redaction.MAX_AUDIT_TEXT + 1)
    started = time.perf_counter()
    out = redact({"note": huge, "nested": [huge]})
    assert time.perf_counter() - started < 0.5
    assert out["note"] == f"[{len(huge)} characters not kept]"
    assert out["nested"] == [f"[{len(huge)} characters not kept]"]
    at_limit = "a" * redaction.MAX_AUDIT_TEXT
    assert redact({"note": at_limit})["note"] == at_limit


def test_the_scanner_character_sets_match_the_regex_classes():
    """The linear scanners copy two character classes out of the patterns; a later edit to one
    without the other would make them disagree, so compare them over every ASCII character."""
    import re

    def first_class(pattern: str) -> str:
        start = pattern.index("[")
        return pattern[start : pattern.index("]", start) + 1]

    local = re.compile(first_class(redaction._EMAIL.pattern))
    segment = re.compile(first_class(redaction._JWT_VALUE.pattern.split("eyJ", 1)[1]))
    for code in range(128):
        char = chr(code)
        assert (char in redaction._LOCAL_CHARS) == bool(local.fullmatch(char)), repr(char)
        assert (char in redaction._SEGMENT_CHARS) == bool(segment.fullmatch(char)), repr(char)


def test_fast_masking_matches_a_plain_regex_substitution():
    def plain_emails(text: str) -> str:
        return redaction._EMAIL.sub(lambda m: redaction._mask_email(m.group(0)), text)

    def plain_jwts(text: str) -> str:
        return redaction._JWT_VALUE.sub("[redacted]", text)

    atoms = ["a", "Z", "9", ".", "-", "_", "@", "%", "+", " ", "eyJ", "abcdefgh", "x.com", "\n"]
    rng = random.Random(20261113)
    samples = [
        "a@x.com-b@y.com",
        "a@b-c@d.com",
        "foo.bar@x.com, baz@y.org",
        "x-eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
    ] + ["".join(rng.choice(atoms) for _ in range(rng.randint(1, 60))) for _ in range(20_000)]
    for text in samples:
        assert redaction._mask_emails(text) == plain_emails(text), repr(text)
        assert redaction._mask_jwts(text) == plain_jwts(text), repr(text)


def test_adjacent_addresses_are_both_masked():
    assert redact({"note": "a@x.com-b@y.com"})["note"] == "a***@x.com-***@y.com"
