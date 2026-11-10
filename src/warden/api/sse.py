"""Format stream events as Server-Sent Events frames.

Frames carry the event JSON in the data field only, so the browser can use one onmessage handler and
switch on the event's own type. A comment frame serves as a keepalive during quiet stretches.
"""

from __future__ import annotations

import json

from ..domain import StreamEvent


def sse_data(event: StreamEvent) -> str:
    """One data frame. The event's type travels inside the JSON payload."""
    payload = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
    return f"data: {payload}\n\n"


def sse_comment(text: str) -> str:
    """A comment frame. Browsers ignore it; it keeps the connection from idling out."""
    return f": {text}\n\n"
