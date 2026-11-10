"""Browser tests of the demo: the page loads clean, the approve run completes, the kill switch stops
a run, and the reliability panel renders. These drive a real Chromium against a real server.
"""

from __future__ import annotations

import pytest

# Playwright emits teardown warnings that the strict unit filter would turn into errors.
pytestmark = pytest.mark.filterwarnings("ignore")


def test_initial_state_is_clean(server, page):
    page.goto(server, wait_until="networkidle")
    assert page.title() == "Warden"
    assert page.locator("#kill-note").is_hidden()
    assert page.locator("#approval-card").is_hidden()
    assert page.locator("#bars .bar-row").count() == 3
    assert page.locator("#res-breakglass").inner_text().strip() == "Protected"
    assert page.console_errors == []


def test_approve_flow_completes_and_updates_access(server, page):
    page.goto(server, wait_until="networkidle")
    page.click("#start-btn")
    page.wait_for_selector("#approval-card:not([hidden])", timeout=20000)
    # The over-reach on the privileged account is denied before the approval is even asked for.
    assert page.locator('.step[data-decision="blocked"]').count() >= 1
    page.click("#approve-btn")
    page.wait_for_selector('#run-status[data-state="completed"]', timeout=20000)

    assert page.locator("#res-account").inner_text().strip() == "Disabled"
    assert page.locator("#res-crm").inner_text().strip() == "Revoked"
    assert page.locator("#res-breakglass").inner_text().strip() == "Blocked"
    assert page.console_errors == []


def test_kill_switch_stops_the_run(server, page):
    page.goto(server, wait_until="networkidle")
    page.click("#start-btn")
    page.click("#revoke-btn")
    page.wait_for_selector('#run-status[data-state="revoked"]', timeout=20000)

    assert page.locator("#kill-note").is_visible()
    # Nothing was executed: every tracked resource is still active.
    assert page.locator("#res-account").inner_text().strip() == "Active"
    assert page.locator("#res-crm").inner_text().strip() == "Active"
    assert page.console_errors == []


def test_reliability_checks_run(server, page):
    page.goto(server, wait_until="networkidle")
    page.click("#eval-btn")
    page.wait_for_function(
        "document.querySelector('#eval-summary').textContent.includes('scenarios passed')",
        timeout=30000,
    )
    summary = page.inner_text("#eval-summary")
    assert "5/5 scenarios passed" in summary
    assert page.console_errors == []
