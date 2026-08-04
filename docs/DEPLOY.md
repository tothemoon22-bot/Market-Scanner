# Running the scanner and dashboard

Read-only instrument. No credentials, no authenticated endpoints, no
order-placing code. The process refuses to start if a Kalshi credential is
present in its environment — see `scanner/guard.py`.

## Locally

```bash
.venv/bin/python -m dashboard.app --host 127.0.0.1 --port 8000
```

Against a committed snapshot instead of the live exchange, which is what the
tests and the screenshots use:

```bash
.venv/bin/python -m dashboard.app --snapshot monitor/snapshots/20260803T070632Z_t0
```

Snapshot mode is real measured data, not synthetic — but it is not live, so the
UI carries a persistent amber banner saying so.

## Cadences, and why

| Loop | Interval | What it costs |
| --- | --- | --- |
| Full sweep | 900s | 17-75s of paced requests; ~77 pages plus series metadata |
| Tracked subset | 15s | 14 requests, ~2s, the fee-free series the findings rest on |
| Reference spot | 1s | 6 requests to two public hosts |

The full-sweep interval is a choice, not a limit. Measured sweep durations span
17s to 75s depending on how much of the series metadata is already warm and how
often the exchange makes us back off. The dashboard reports
the **achieved** interval and last duration rather than the configured one, so a
box that cannot keep up says so.

## Hosting

A single always-on instance is enough — a $5/mo Lightsail box or equivalent. One
process serves both the scanner and the dashboard.

```ini
# /etc/systemd/system/kalshi-scanner.service
[Unit]
Description=Kalshi scanner and dashboard
After=network-online.target

[Service]
Type=simple
User=scanner
WorkingDirectory=/opt/market-scanner
# Deliberately empty: the guard exits non-zero if a Kalshi credential is set.
Environment=
ExecStart=/opt/market-scanner/.venv/bin/python -m dashboard.app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=10
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/market-scanner/data

[Install]
WantedBy=multi-user.target
```

Put Caddy or nginx in front for HTTPS — a PWA will not install over plain HTTP,
and the service worker will not register. With Caddy that is two lines:

```
scanner.example.com {
  reverse_proxy 127.0.0.1:8000
}
```

`GET /api/health` returns 503 when the scanner has gone quiet, so it works
directly as a supervisor or uptime-monitor check.

## The weekly baseline job stays where it is

The committed-baseline job runs on GitHub Actions, not on this box, and that
separation is deliberate: the archive has to survive the instance. This host is
the live view; the Actions job is the durable record. **Do not consolidate
them.** A dashboard that renders stale data cheerfully is the same silent death
as a disabled cron, and the two failures should not share a single point.

## Phone alerts — what works, and what does not

**Works:** the dashboard installs as a PWA, and when a trigger *transitions*
into the fired state the page raises a Notification. Only transitions notify; a
trigger that is already firing does not re-notify on every push, because a
notification that repeats is a notification that gets dismissed unread.

**Does not work yet:** delivery while the app is fully closed. That needs Web
Push, which needs a VAPID key pair and a push endpoint, neither of which exists
here. Implementing it means generating VAPID keys on the host, storing
subscriptions, and posting to the browser vendor's push service. That is a
hosting task with a key to manage, so it is left undone rather than half-built.

Until then the honest reading is: the dashboard alerts you when you look at it,
or when it is open in the background. It does not wake your phone.

## What the dashboard does not have, and why

The scanner **cannot** use Kalshi's WebSocket. The handshake requires
authentication — `wss://external-api-ws.kalshi.com/trade-api/ws/v2` and the
elections host both return HTTP 401 without credentials, verified directly. The
brief asked for live books over WS *and* for no authenticated endpoints; those
cannot both hold. The no-credentials constraint won, because it is the one the
whole project has been built and grep-tested against, and because a scanner that
holds a key to read faster is a different object with a different risk profile.

The consequence is that the tracked subset refreshes every ~15 seconds rather
than on every book update. For an instrument whose job is to notice a structural
change over weeks, that is not the binding limitation.
