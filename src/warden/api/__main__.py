"""Serve the Warden app with uvicorn.

    python -m warden.api

Host and port come from WARDEN_HOST and WARDEN_PORT, defaulting to 127.0.0.1:8800.
"""

from __future__ import annotations

from ..config import Settings
from .app import create_app


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
