"""Check the offboarding against the systems themselves, not against the agent's report.

An agent's closing message is a claim. This reads the directory, the VPN, and every repository the
repo tool can see, and records for each step whether the subject's access is actually gone. The run
ends with this attached, and the UI shows it beside the agent's own summary, so a run that skipped a
step never reads as finished. These reads are the control plane's own; they do not go through the
broker because they are not agent actions.
"""

from __future__ import annotations

from typing import Any

from .tools import ToolRegistry

DONE = "done"
NOT_DONE = "not_done"
UNKNOWN = "unknown"


def _check(
    check_id: str, label: str, status: str, detail: str = "", remaining: list[str] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {"id": check_id, "label": label, "status": status, "detail": detail}
    if remaining is not None:
        out["remaining"] = remaining
    return out


def verify_offboarding(registry: ToolRegistry, subject: str) -> dict[str, Any]:
    """Each offboarding step as done, not done, or unknown (a system could not be read)."""
    checks: list[dict[str, Any]] = []

    profile = registry.execute("directory", "lookup_user", {"user_id": subject})
    if profile.ok and profile.data:
        apps = list(profile.data.get("apps") or [])
        checks.append(
            _check(
                "app_access", "Application access revoked", NOT_DONE if apps else DONE,
                f"still granted: {', '.join(apps)}" if apps else "", remaining=apps,
            )
        )
    else:
        checks.append(_check("app_access", "Application access revoked", UNKNOWN,
                             profile.error or "directory unreadable"))

    vpn = registry.execute("vpn", "status", {"user_id": subject})
    if vpn.ok and vpn.data:
        on = vpn.data.get("enabled") is not False
        checks.append(_check("vpn", "VPN turned off", NOT_DONE if on else DONE,
                             "VPN access is still on" if on else ""))
    else:
        checks.append(_check("vpn", "VPN turned off", UNKNOWN, vpn.error or "VPN unreadable"))

    checks.append(_repo_check(registry, subject))

    if profile.ok and profile.data:
        enabled = profile.data.get("enabled") is not False
        checks.append(_check("account", "Account disabled", NOT_DONE if enabled else DONE,
                             "the account is still enabled" if enabled else ""))
    else:
        checks.append(_check("account", "Account disabled", UNKNOWN,
                             profile.error or "directory unreadable"))

    return {
        "subject": subject,
        "complete": all(c["status"] == DONE for c in checks),
        "checks": checks,
    }


def _repo_check(registry: ToolRegistry, subject: str) -> dict[str, Any]:
    label = "Repository access removed"
    listing = registry.execute("repo", "list_repos", {})
    if not listing.ok or not listing.data:
        return _check("repo_access", label, UNKNOWN, listing.error or "repositories unreadable")
    identities = registry.identities_of(subject)
    still: list[str] = []
    unreadable: list[str] = []
    for repo in listing.data.get("repos", []):
        members = registry.execute("repo", "list_members", {"repo": repo})
        if not members.ok or not members.data:
            unreadable.append(repo)
        elif identities & set(members.data.get("members", [])):
            still.append(repo)
    if still:
        return _check("repo_access", label, NOT_DONE, f"still a member of: {', '.join(still)}",
                      remaining=still)
    if unreadable:
        return _check("repo_access", label, UNKNOWN, f"could not read: {', '.join(unreadable)}")
    return _check("repo_access", label, DONE, remaining=[])
