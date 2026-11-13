"""Browser tests of the demo: the page loads clean, the approve run completes, the kill switch stops
a run, the reliability panel renders, and what the operator has to see is on screen without
scrolling. These drive a real Chromium against a real server.
"""

from __future__ import annotations

import pytest

# Playwright emits teardown warnings that the strict unit filter would turn into errors.
pytestmark = pytest.mark.filterwarnings("ignore")
expect = pytest.importorskip("playwright.sync_api").expect

# Window sizes. The page is two columns from 941px wide and one column below that.
SIDE_BY_SIDE = [(1920, 1080), (1440, 900), (1366, 768), (1280, 720), (1280, 560)]
ONE_COLUMN = [(940, 800), (390, 844)]


def _size_id(size):
    return f"{size[0]}x{size[1]}"


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
    assert "10/10 scenarios passed" in summary
    assert page.console_errors == []


def test_a_finished_run_shows_the_agents_summary_and_the_systems_check(server, page):
    page.goto(server, wait_until="networkidle")
    page.click("#start-btn")
    page.wait_for_selector("#approval-card:not([hidden])", timeout=20000)
    # The note on Jordan's record, which asks for the break-glass disable, is shown as data.
    assert "security.breakglass" in page.locator(".step-note").first.inner_text()
    page.click("#approve-btn")
    page.wait_for_selector('#run-status[data-state="completed"]', timeout=20000)

    assert page.locator(".step-summary").count() == 1
    check = page.locator('.step-verified[data-complete="yes"]')
    assert "4 of 4 steps done" in check.text_content()
    assert page.locator("#res-repo").inner_text().strip() == "Removed"
    page.wait_for_selector('.step-audit[data-verified="yes"]', timeout=5000)
    assert "hash chain verified" in page.locator(".step-audit").text_content()
    assert page.console_errors == []


def test_a_denied_disable_ends_incomplete_not_completed(server, page):
    page.goto(server, wait_until="networkidle")
    page.click("#start-btn")
    page.wait_for_selector("#approval-card:not([hidden])", timeout=20000)
    page.click("#deny-btn")
    page.wait_for_selector('#run-status[data-state="incomplete"]', timeout=20000)

    assert page.locator('.step-verified[data-complete="no"]').count() == 1
    assert page.locator("#res-account").inner_text().strip() == "Active"
    assert page.console_errors == []


def test_revoking_while_an_approval_is_pending_stops_the_run(server, page):
    page.goto(server, wait_until="networkidle")
    page.click("#start-btn")
    page.wait_for_selector("#approval-card:not([hidden])", timeout=20000)
    page.click("#revoke-btn")
    # Well under the approval timeout: the revoke ends the wait itself.
    page.wait_for_selector('#run-status[data-state="revoked"]', timeout=5000)
    assert page.locator("#res-account").inner_text().strip() == "Active"
    assert page.console_errors == []


# On screen means to_be_in_viewport(ratio=1). It asks the browser's IntersectionObserver, which also
# counts as hidden any part of a step that the feed's own scroll box cuts off.


@pytest.fixture
def end_open_run(page):
    # The server runs at most four at once, so a check that fails while its run waits for approval
    # would leave the run open and starve the tests after it. Revoke such a run when the test ends.
    yield
    if page.locator("#revoke-btn").is_enabled():
        page.keyboard.press("k")
        page.wait_for_selector('#run-status[data-state="revoked"]', timeout=10000)


def _run_until_approval(server, page, size):
    # Keys, not clicks, start and approve the run: a Playwright click scrolls its target into view
    # first, which is the scrolling these tests check the page does on its own.
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(server, wait_until="networkidle")
    page.keyboard.press("r")
    page.wait_for_selector("#approval-card:not([hidden])", timeout=20000)


def _approve_until_done(page):
    page.keyboard.press("a")
    page.wait_for_selector('#run-status[data-state="completed"]', timeout=20000)
    page.wait_for_selector(".step-audit", timeout=5000)


@pytest.mark.usefixtures("end_open_run")
@pytest.mark.parametrize("size", SIDE_BY_SIDE, ids=_size_id)
def test_side_by_side_the_held_step_and_the_controls_are_on_screen(server, page, size):
    _run_until_approval(server, page, size)
    expect(page.locator('.step[data-decision="approval"]')).to_be_in_viewport(ratio=1)
    for button in ("#approve-btn", "#deny-btn", "#revoke-btn"):
        expect(page.locator(button)).to_be_in_viewport(ratio=1)
    _approve_until_done(page)
    expect(page.locator(".step-verified")).to_be_in_viewport(ratio=1)
    expect(page.locator(".step-audit")).to_be_in_viewport(ratio=1)
    assert page.console_errors == []


@pytest.mark.usefixtures("end_open_run")
@pytest.mark.parametrize("size", ONE_COLUMN, ids=_size_id)
def test_in_one_column_the_approval_card_and_the_read_back_scroll_into_view(server, page, size):
    _run_until_approval(server, page, size)
    for target in ("#approval-card", "#approve-btn", "#deny-btn"):
        expect(page.locator(target)).to_be_in_viewport(ratio=1)
    _approve_until_done(page)
    expect(page.locator(".step-verified")).to_be_in_viewport(ratio=1)
    assert page.console_errors == []


# The operator scrolled the page while the run waited: to the top, so the read-back lands below the
# window, or to the end, so it lands above it.
@pytest.mark.usefixtures("end_open_run")
@pytest.mark.parametrize(
    ("size", "scroll_to"),
    [((940, 800), "top"), ((390, 844), "end"), ((1280, 560), "end")],
    ids=["940x800-top", "390x844-end", "1280x560-end"],
)
def test_the_read_back_comes_into_view_after_the_operator_scrolled_away(
    server, page, size, scroll_to
):
    _run_until_approval(server, page, size)
    y = "0" if scroll_to == "top" else "document.documentElement.scrollHeight"
    page.evaluate(f"window.scrollTo(0, {y})")
    _approve_until_done(page)
    expect(page.locator(".step-verified")).to_be_in_viewport(ratio=1)
    assert page.console_errors == []
