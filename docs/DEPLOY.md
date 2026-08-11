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
| Full sweep | 3600s | 17-75s of paced requests; ~77 pages plus series metadata |
| Tracked subset | 15s | 14 requests, ~2s, the fee-free series the findings rest on |
| Reference spot | 1s | 6 requests to two public hosts |
| Heartbeat push | 30d | one ntfy notification, priority `min` |

**The full sweep is hourly because the phenomenon is not faster than that.**
The exchange-wide statistics it computes — tick structure mix, fee types, the
fee-free universe, segment spread distributions — have a time constant of days
to weeks. Sweeping every 15 minutes bought no resolution and spent four times
the request budget against a rate limiter that can IP-block us, which would kill
the monitor outright for zero benefit. The tracked subset stays at 15s because
it is 14 requests rather than 77 pages.

The interval is a choice, not a limit. Measured sweep durations span 17s to 75s
depending on how much of the series metadata is already warm and how often the
exchange makes us back off. The dashboard reports the **achieved** interval and
last duration rather than the configured one, so a box that cannot keep up says
so — and it labels the two cadences separately, because one number covering both
would be a number the system does not measure.

429 responses are counted and shown on the health panel. A rate-limited scanner
that stays quiet is a scanner on its way to a block.

## Hosting

A single always-on instance is enough. One process serves both the scanner and
the dashboard.

### Sizing — measured

The sweep streams: each page is folded into bounded aggregates and discarded.
Four consecutive real sweeps of **84,240 markets**, RSS after each settles:

| | RSS |
| --- | --- |
| At rest, before the first sweep | **31 MB** |
| Peak during a sweep | **102–119 MB** |
| Settled after sweep 1 / 2 / 3 / 4 | 111 / 102 / 114 / **98 MB** |

Sweep-over-sweep deltas were −8, +12, −16 MB — allocator noise around ~106 MB,
with sweep 4 settling *below* sweep 1. No monotonic growth.

**A 1 GB instance is enough**, with roughly 8× headroom over the peak. Watch the
resident-memory row on the health panel: the figures above are what a healthy
process looks like, so sustained growth past them is the signal.

Before the rewrite this was 93 MB at rest and **576 MB** steady — the sweep
accumulated all ~77,000 market objects and then derived a second list from them,
and Python does not return that to the OS, so the peak became the floor. That
was a defect rather than a sizing requirement, and it was fixed rather than
provisioned around. See `monitor/aggregate.py`.

Four sweeps is not the 24-hour gate. Only the gate, with RSS at 0h/6h/12h/24h,
settles whether there is a slow leak.

### Headroom — a note, not a new sizing

The count-dependent part of memory is the aggregate's retained legs plus the two
ticker sets reconciliation holds. Measured by direct byte counting at four
market counts over a real snapshot:

| | MB per million markets |
| --- | --- |
| Aggregate retention | 212 |
| Reconciliation ticker sets | 192 |
| **Combined** | **404** |

Residuals ±1.8 MB against 21 MB of signal across a 4× range, so it is linear
here. Anchoring to the measured live peak — 110.8 MB at 84,681 markets — gives
34 MB count-dependent and 77 MB fixed:

| Market count | Projected peak RSS |
| --- | --- |
| 84,681 (current) | 111 MB *(measured, not projected)* |
| 127,022 (1.5×) | 128 MB |
| 169,362 (2×) | 145 MB |
| 423,405 (5×) | 248 MB |

**The 1 GB tier holds to roughly 1.7 million markets — 20× current** — taking
75% of RAM as the usable ceiling. The tier is not the binding constraint at any
plausible population.

RSS *deltas* cannot measure this slope, and it is worth recording why: across
the same 4× range the total moved 2.6 MB with ±0.9 MB residuals, so a fit to
RSS reads allocator arena reuse rather than retention, and extrapolates to
nonsense. Direct byte counting is the method; RSS is the anchor.

#### The growth is listing cadence, not expansion

Market count went 70,820 → 84,681 in 41.7 hours, **+19.6%, or 10.8% per day
compounded**. Taken at face value that reaches the 1 GB ceiling in ~29 days.

It should not be taken at face value, and the horizon decomposition says why.
Bucketing the 77,047 → 84,625 move by time to `close_time`:

| Horizon | Prior | Current | Net | Share of the 15,707 added |
| --- | --- | --- | --- | --- |
| <24h | 6,571 | 8,018 | +1,447 | 45.8% |
| 1–7d | 12,827 | 18,742 | +5,915 | 43.7% |
| 7–30d | 7,438 | 7,329 | −109 | 7.0% |
| 30d–1y | 34,189 | 34,488 | +299 | 3.4% |
| >1y | 16,022 | 16,048 | +26 | 0.2% |

**97.1% of the net growth is in markets that resolve within seven days**, and
89.5% of added markets are sub-7-day. Those cannot accumulate: they expire
inside the window, so the short-dated population is bounded by listing rate ×
horizon, and it plateaus. What compounds is the long-dated population, and that
grew **+325 markets, +0.6% over 8.2 hours — 1.9%/day**, not 10.8%. At that rate
the 1 GB ceiling is ~187 days away rather than 29, and it is one 8-hour
observation, so even that is soft.

The headline number and the compounding number differ by 5.7×, from the same
sweep pair. **A market count alone cannot tell them apart**, which is why the
distribution is now recorded per sweep rather than derived once.

#### The short-dated component is bounded, not exponential

Everything in the sub-7d window leaves it within seven days by construction, so
Little's Law gives a *hard* bound rather than a projection. `L = λW`, with
`W ≤ 7 days` definitional:

| | Value |
| --- | --- |
| Observed sub-7d population | 26,760 |
| Observed closure rate | 23,792 /day |
| Implied mean residence, `L / λ` | **1.12 days** |
| Ceiling at this closure rate, if `W` were the full 7 days | **166,545** |

The implied residence reproduces the observed population, which is the same as
saying **the component is already at its plateau**: the 8-hour window caught a
listing burst, not a trend. It cannot compound, because the outflow is
mechanically tied to the inflow one week later.

#### Projecting only what compounds

Long-dated (30d+) is the part that can grow without bound. Treating the sub-30d
population as a fixed 34,089 offset:

| | 1 GB @ 75% | 2 GB @ 75% |
| --- | --- | --- |
| Long-dated must reach | 1,676,205 | 3,575,887 |
| At the observed **1.91%/day** | **185 days** | 226 days |

For contrast, the naive whole-population rate over the same sweep pair is
**31.6%/day**, which reaches 1 GB in **11 days**. Same two sweeps, same
arithmetic, 11 days versus 185 — the entire difference is whether the growth is
counted in a component that can accumulate.

#### Observation count

**Every rate above rests on n = 1: a single 8.2-hour window.** The whole-
population rate computed from the *other* available pair (41.7 hours) is
10.8%/day rather than 31.6%/day, so the headline rate is not even stable across
the two pairs on record. A weekend sports-listing burst is indistinguishable
from expansion at this resolution, and neither derived date should be treated as
a forecast. They are recorded so the archive can overturn them.

**Still not resized.** The evidence points away from the flag rather than toward
it, and the trend panel records the bucket distribution every sweep so net
change *per horizon* is answerable from data. Watch the resident-memory row
meanwhile.

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

## Phone alerts

**The dashboard does not solve notification transport, and no longer pretends
to.** Foreground Web Notifications only fire while the page is open, which is
useless for an instrument that will almost never be open. Web Push would fix
that and needs a VAPID key pair, a subscription store and a browser-vendor push
endpoint — a hosting task with a key to manage. It is not built, and the partial
Notifications path that used to exist has been removed rather than left as dead
code.

Transport is [ntfy](https://ntfy.sh) instead. Its own app handles background
delivery, so the split is clean: **ntfy pushes, the PWA browses.**

```bash
# /etc/systemd/system/kalshi-scanner.service.d/ntfy.conf
[Service]
Environment=NTFY_TOPIC=<a long random string you choose>
Environment=NTFY_SERVER=https://ntfy.sh    # optional; this is the default
```

Subscribe the phone app to the same topic. The topic **is** the credential: an
ntfy topic is a shared secret in a URL, so pick something unguessable. It is
read from the environment, never logged, never included in an error message, and
never rendered by the dashboard — the health panel shows `configured` or
`not configured` and nothing else.

If `NTFY_TOPIC` is unset the notifier is a no-op that says so on the health
panel. It does not fail silently and it does not block the scanner.

| Push | Priority | Tag |
| --- | --- | --- |
| A fired trigger | `high` | `rotating_light` |
| Monthly proof of life | `min` | `heartbeat` |

The heartbeat exists so a quiet monitor is distinguishable from a dead one, and
carries its own tag so it can be muted without muting real alerts.

## What suppresses an alert, and what a suppression is not

A below-par verified partition pushes only if capacity × edge clears **$25**, or
if its annualized return clears 20%/yr — the second branch is deliberately
unfloored, because a genuinely high-return structure is news at any size.

The floor exists because the first live alert was worth **$0.30**. See
`docs/NEGATIVE_RESULT.md` § "First dynamics": these baskets moved a median of 5¢
over 33 hours against a below-par excursion of 2¢, so below par alone is noise.

**Suppression applies to the push, never to the record.** Every sub-floor
detection is written to `data/monitor/suppressed.jsonl` and shown on the trigger
board as a rolling 90-day count broken down by reason. A spike in that count is
a signal in its own right — which is the guard against a threshold quietly
hiding a real change.

Per-series oscillation bands work the same way, and the cold start is not faked.
A band needs 8 observations before it means anything; below that the series
reports `UNKNOWN`, the band branch is skipped entirely, and the dollar floor
carries the decision alone. The trigger board shows the state and how many more
observations a band needs, so an `UNKNOWN` is visibly not a verified range.

## What the dashboard does not have, and why

The scanner **cannot** use Kalshi's WebSocket: the handshake requires
authentication, verified as HTTP 401 on both production hosts. The brief asked
for live books over WS *and* for no authenticated endpoints; those cannot both
hold, and the no-credentials constraint takes precedence.

**REST polling is the permanent design, not a workaround.** The tracked subset
refreshes every ~15 seconds rather than on every book update, which is already
three to four orders of magnitude finer than a phenomenon whose time constant is
weeks. Full reasoning in
[`docs/venues/kalshi/README.md`](venues/kalshi/README.md) § "The WebSocket
requires authentication". Nobody should re-attempt this later.
