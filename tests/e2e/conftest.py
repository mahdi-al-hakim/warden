"""Fixtures for the browser tests: a real server subprocess and a Playwright page.

These run a full stack, so they live apart from the unit suite and are not collected by default.
Run them with: python -m pytest tests/e2e
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
E2E_STEP_DELAY = "0.15"  # small, so the approve run is quick and the revoke lands before step one


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="session")
def server():
    port = _free_port()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("WARDEN_GITHUB_") and k != "ANTHROPIC_API_KEY"}
    env.update(
        PYTHONPATH=str(SRC),
        WARDEN_MODE="replay",
        WARDEN_STEP_DELAY_SECONDS=E2E_STEP_DELAY,
        WARDEN_HOST="127.0.0.1",
        WARDEN_PORT=str(port),
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "warden.api"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/healthz", timeout=0.5)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("the server did not come up in time")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    errors: list[str] = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.console_errors = errors
    yield pg
    pg.close()
