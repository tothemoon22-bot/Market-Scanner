"""Push transport: ntfy.

**The dashboard does not solve notification transport.** PWA for browsing, ntfy
for pushing — its own app handles background delivery, which is the thing
foreground-only Web Notifications could never do for an instrument that will
almost never be open.

No credentials in the usual sense: an ntfy topic is a shared secret in a URL, so
it is read from the environment and never logged, never echoed in an error, and
never rendered by the dashboard. If the topic is unset the notifier is a no-op
that says so on the health panel rather than failing silently.

Configuration:

    NTFY_TOPIC   required to push at all
    NTFY_SERVER  defaults to https://ntfy.sh
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from monitor.alerts import FOOTER

DEFAULT_SERVER = "https://ntfy.sh"

#: Monthly, low priority, its own tag, so a quiet monitor is distinguishable
#: from a dead one without the heartbeat competing with real alerts.
HEARTBEAT_TAG = "heartbeat"
ALERT_TAG = "rotating_light"


@dataclass(frozen=True)
class NtfyConfig:
    topic: str
    server: str = DEFAULT_SERVER

    @property
    def configured(self) -> bool:
        return bool(self.topic)

    @property
    def url(self) -> str:
        return f"{self.server.rstrip('/')}/{self.topic}"

    def redacted(self) -> str:
        """Never print the topic: it is the only thing protecting the channel."""
        return f"{self.server.rstrip('/')}/<topic set>" if self.topic else "<not configured>"


def config_from_env(env: dict[str, str] | None = None) -> NtfyConfig:
    source = os.environ if env is None else env
    return NtfyConfig(
        topic=(source.get("NTFY_TOPIC") or "").strip(),
        server=(source.get("NTFY_SERVER") or DEFAULT_SERVER).strip(),
    )


def alert_body(trigger: str, baseline: str, current: str, memo_section: str) -> str:
    return (
        f"baseline: {baseline}\n"
        f"current:  {current}\n"
        f"see: docs/NEGATIVE_RESULT.md -> {memo_section}\n\n"
        f"{FOOTER}"
    )


async def push(
    client: httpx.AsyncClient,
    cfg: NtfyConfig,
    *,
    title: str,
    body: str,
    tag: str = ALERT_TAG,
    priority: str = "default",
) -> None:
    if not cfg.configured:
        raise RuntimeError("NTFY_TOPIC is not set; nothing to push to")
    await client.post(
        cfg.url,
        content=body.encode("utf-8"),
        headers={
            "Title": title,
            "Tags": tag,
            "Priority": priority,
        },
        timeout=15.0,
    )


async def push_alerts(client: httpx.AsyncClient, cfg: NtfyConfig, alerts: list[Any]) -> int:
    """One notification per fired alert. Returns how many were sent."""
    sent = 0
    for alert in alerts:
        await push(
            client,
            cfg,
            title=f"Kalshi scanner: {alert.trigger}",
            body=alert_body(alert.trigger, alert.baseline, alert.current, alert.memo_section),
            tag=ALERT_TAG,
            priority="high",
        )
        sent += 1
    return sent


async def push_heartbeat(client: httpx.AsyncClient, cfg: NtfyConfig, summary: str) -> None:
    """Monthly proof of life. Low priority, distinct tag, easy to mute."""
    await push(
        client,
        cfg,
        title="Kalshi scanner: still running, nothing fired",
        body=f"{summary}\n\n{FOOTER}",
        tag=HEARTBEAT_TAG,
        priority="min",
    )
