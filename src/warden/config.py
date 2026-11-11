"""Runtime configuration, read from the environment with sensible defaults.

Most settings are read here, in one typed place, so a misconfigured deployment fails in an obvious
way instead of halfway through a run. The GitHub connector reads its own WARDEN_GITHUB_* variables,
and the API reads WARDEN_WEB_DIR.
"""

from __future__ import annotations

import os

from pydantic import BaseModel


class Settings(BaseModel):
    """All knobs the control plane needs. Built once at startup from the environment."""

    mode: str = "replay"
    anthropic_api_key: str | None = None
    model: str = "claude-haiku-5-5"
    # Thinking effort for live mode (low, medium, high, ...). Unset sends nothing, so models
    # that do not take the parameter still work.
    effort: str | None = None
    # A recorded trajectory to replay instead of the built-in synthetic one. Set this to a run you
    # captured from a real model (scripts/record_trajectory.py) so the demo plays back a real run.
    trajectory_path: str | None = None
    approval_timeout_seconds: float = 120.0
    demo_step_delay_seconds: float = 0.6
    host: str = "127.0.0.1"
    port: int = 8800

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings from WARDEN_* variables, falling back to the defaults above."""
        return cls(
            mode=os.getenv("WARDEN_MODE", "replay").strip().lower(),
            anthropic_api_key=(os.getenv("ANTHROPIC_API_KEY") or "").strip() or None,
            model=os.getenv("WARDEN_MODEL", "claude-haiku-5-5").strip(),
            effort=(os.getenv("WARDEN_EFFORT") or "").strip().lower() or None,
            trajectory_path=(os.getenv("WARDEN_TRAJECTORY") or "").strip() or None,
            approval_timeout_seconds=float(os.getenv("WARDEN_APPROVAL_TIMEOUT_SECONDS", "120")),
            demo_step_delay_seconds=float(os.getenv("WARDEN_STEP_DELAY_SECONDS", "0.6")),
            host=os.getenv("WARDEN_HOST", "127.0.0.1").strip(),
            port=int(os.getenv("WARDEN_PORT", "8800")),
        )

    @property
    def is_live(self) -> bool:
        """True when the control plane is configured to drive a real model instead of a replay."""
        return self.mode == "live"

    @property
    def live_ready(self) -> bool:
        """True only when live mode is set and a key is present, so the server can fall back."""
        return self.is_live and bool(self.anthropic_api_key)
