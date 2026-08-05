/* Renders scanner state. Nothing here invents a value.
 *
 * The single rule: if the payload does not carry a number, the panel renders an
 * explicit NO DATA state saying why. `0 opportunities` and `scanner offline`
 * must never look alike -- zero is rendered as a numeral in the normal
 * typography, absence is rendered as a dashed NO DATA chip.
 *
 * There are no placeholder constants in this file. Search it for a literal
 * price, count, or percentage and you will find only axis labels and CSS-ish
 * geometry; every displayed quantity arrives over the wire.
 */

const $ = (id) => document.getElementById(id);
const NO_DATA = (why) => `<span class="nodata" title="${esc(why || "")}">no data</span>`;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
function num(v, digits = 0) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits }) : null;
}
/* Trailing-zero trim only. The wire carries Decimal strings like "6.0000";
   showing "6" is formatting, not a change of precision. */
function cents(v) {
  if (v === null || v === undefined || v === "") return null;
  const s = String(v);
  return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
}
function age(seconds) {
  if (seconds === null || seconds === undefined) return null;
  const s = Math.floor(seconds);
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.floor(s / 60)}m`;
  if (s < 172800) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}
function clock(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toISOString().slice(11, 19);
}

/* The client flips to offline on its own clock well before the server's
   heartbeat window, so a dead scanner is visible inside the 60s gate even
   though pushes arrive only every couple of seconds. */
const CLIENT_SILENCE_LIMIT_S = 25;

let SEGMENT = "tick_structure";
let lastPayload = null;
let lastMessageAt = null;

/* ---------------------------------------------------------------- hero --- */
function renderHero(p) {
  const funnel = p.funnel;
  const scanned = funnel ? funnel[0].count : null;
  const actionable = funnel ? funnel[funnel.length - 1].count : null;

  if (actionable === null) {
    $("hero-count").innerHTML = NO_DATA("no sweep has completed yet");
    $("hero-sub").textContent = "waiting for the first full sweep";
  } else {
    $("hero-count").innerHTML =
      `<span class="num">${num(actionable)}</span> ACTIONABLE ` +
      `<span class="slash">/</span> <span class="scanned num">${num(scanned)}</span> SCANNED`;
    $("hero-sub").textContent =
      `${num(funnel[1].count)} two-sided · ${num(funnel[3].count)} verified partitions · ` +
      `sweep ${age(p.metrics_age_seconds)} ago`;
  }

  const triggers = p.triggers || [];
  const measurable = triggers.filter((t) => t.proximity_pct !== null);
  const closest = measurable.length
    ? measurable.reduce((a, b) => (Number(a.proximity_pct) >= Number(b.proximity_pct) ? a : b))
    : null;

  $("hero-closest").innerHTML = closest
    ? `<b>${esc(closest.label)}</b> — ${closest.value === null ? NO_DATA(closest.no_data_reason)
        : esc(closest.value) + " " + esc(closest.unit)} — ` +
      `<span class="num ${Number(closest.proximity_pct) >= 20 ? "warn" : ""}">` +
      `${closest.proximity_pct}%</span> to fire`
    : NO_DATA("no trigger has a measurable distance yet");

  const w = p.proximity_watch;
  if (!w || w.peak_proximity_pct === null || w.peak_proximity_pct === undefined) {
    $("hero-watch").innerHTML = NO_DATA("no proximity measured yet");
  } else if (w.days_since_within_20 === null) {
    $("hero-watch").innerHTML =
      `no trigger has come within 20% of firing in ` +
      `<b class="num">${num(w.observed_days, 2)}</b> days observed ` +
      `<span class="dim">(peak ${w.peak_proximity_pct}%)</span>`;
  } else {
    $("hero-watch").innerHTML =
      `<b class="num">${num(w.days_since_within_20, 2)}</b> days since a trigger was within 20% of firing`;
  }
}

/* -------------------------------------------------------------- funnel --- */
function renderFunnel(p) {
  const el = $("funnel");
  if (!p.funnel) {
    el.innerHTML = `<div class="nodata-block"><b>no data</b>
      No sweep has completed. The funnel appears once the scanner has counted
      the exchange once.</div>`;
    $("funnel-meta").textContent = "";
    return;
  }
  const top = p.funnel[0].count || 1;
  let dead = false;
  el.innerHTML = p.funnel.map((s) => {
    const isDead = s.count === 0 && !dead;
    if (s.count === 0) dead = true;
    const pct = Math.max(0.4, (s.count / top) * 100);
    return `<div class="stage ${s.count === 0 ? "dead" : ""}">
      <div class="top">
        <span class="name">${esc(s.label)}</span>
        <span class="count">${num(s.count)} <span class="unit">${esc(s.unit)}</span></span>
      </div>
      <div class="bar"><i style="width:${pct}%"></i></div>
      ${s.note ? `<div class="note">${esc(s.note)}</div>` : ""}
      ${isDead ? `<div class="note bad">— terminates here —</div>` : ""}
    </div>`;
  }).join("");
  const t = p.funnel.find((s) => s.count === 0);
  $("funnel-meta").textContent = t ? `dies at: ${t.label.toLowerCase()}` : "no termination";
}

/* ------------------------------------------------------------ triggers --- */
function renderTriggers(p) {
  const el = $("triggers");
  if (!p.triggers) {
    el.innerHTML = `<div class="nodata-block"><b>no data</b>
      Triggers are evaluated against the committed baseline after each full
      sweep. None has run yet.</div>`;
    return;
  }
  el.innerHTML = p.triggers.map((t) => {
    const prox = t.proximity_pct;
    const has = prox !== null && prox !== undefined;
    const pctNum = has ? Number(prox) : 0;
    const cls = t.fired ? "fired" : pctNum >= 20 ? "near" : "";
    return `<div class="trig ${cls}">
      <div class="head">
        <span class="name">${esc(t.label)}</span>
        <span class="pct">${has ? `${prox}%` : NO_DATA(t.no_data_reason)}</span>
      </div>
      <div class="cond">fires when ${esc(t.condition)}</div>
      <div class="vals">now ${t.value === null ? "—" : esc(t.value)} ${esc(t.unit)}
        · baseline ${t.baseline === null ? "—" : esc(t.baseline)}
        · threshold ${esc(t.threshold)}</div>
      ${has ? `<div class="bar"><i style="width:${Math.min(100, Math.max(1, pctNum))}%"></i></div>` : ""}
      ${renderSuppressed(t, p)}
    </div>`;
  }).join("");
}

/* Suppressed detections are recorded, never hidden. A rising count is a signal
   even when nothing individually clears the floor -- that is the guard against
   a threshold quietly masking a real change. */
function renderSuppressed(t, p) {
  const d = t.detail || {};
  if (d.suppressed_now === undefined) return "";
  const w = (p.below_par || {}).window;
  const windowText = w && w.count !== undefined
    ? `${w.count} suppressed in the last ${w.window_days}d`
    : NO_DATA("suppression ledger not yet written");
  return `<div class="vals dim" title="${esc(SUPPRESSED_TOOLTIP)}">
    ${d.suppressed_now} suppressed now · ${windowText}</div>
    ${renderBandMaturation(p)}`;
}

/* Cold-start progress, shown rather than implied. A band under the threshold is
   not a narrow band, it is an absent one, and the count against the threshold
   is the only honest way to say how far off it is. */
function renderBandMaturation(p) {
  const bands = p.bands;
  if (!bands || !Object.keys(bands).length) {
    return `<div class="vals dim">bands ${NO_DATA("no partition history recorded yet")}</div>`;
  }
  const chips = Object.entries(bands).map(([name, b]) => {
    const established = b.state === "KNOWN";
    /* An observation is a sweep. Rows and events are shown in the tooltip so
       breadth cannot be mistaken for time: eleven contracts seen once is one
       observation, not eleven. */
    return `<span class="chip ${established ? "good" : "warn"}"
      title="${esc(name)}: ${b.observations} sweep(s), ${b.rows} row(s) across ${b.events} contract(s)${
        established ? " — band established" : ` — needs ${b.needs} more sweep(s)`}">
      ${esc(name)} ${b.observations}/${b.threshold}${established ? " ✓" : ""}</span>`;
  }).join("");
  return `<div class="vals dim">band maturation
    <span class="dim">(sweeps, not rows)</span> ${chips}</div>`;
}

/* ------------------------------------------------------------ tripwire --- */
function renderTripwire(p) {
  const el = $("tripwire");
  const tw = p.tripwire;
  if (!tw) {
    el.innerHTML = `<div class="nodata-block"><b>no data</b>
      The intersection is computed from the full sweep. None has completed.</div>`;
    $("tripwire-meta").textContent = "";
    return;
  }
  $("tripwire-meta").textContent =
    `${tw.n_markets} markets · ${tw.n_verified_partitions} partitions`;
  if (!tw.partitions || !tw.partitions.length) {
    el.innerHTML = `<div class="nodata-block"><b>no verified partitions</b>
      ${tw.n_markets} markets sit in the intersection, none of which currently
      forms a verified-exhaustive partition.</div>`;
    return;
  }
  el.innerHTML = `<table><thead><tr>
      <th>Event</th><th class="r">Legs</th><th class="r">Cost</th>
      <th class="r">vs par</th><th class="r">Cap</th></tr></thead><tbody>` +
    tw.partitions.map((x) => {
      const gap = Number(x.cost_cents) - 100;
      return `<tr class="${x.below_par ? "flag" : ""}">
        <td>${esc(x.event)}</td>
        <td class="r">${x.legs}</td>
        <td class="r">${esc(x.cost_cents)}¢</td>
        <td class="r ${gap < 0 ? "bad" : "good"}">${gap >= 0 ? "+" : ""}${gap.toFixed(2)}¢</td>
        <td class="r">${esc(x.capacity_contracts)}</td></tr>`;
    }).join("") + `</tbody></table>
    <div class="thresholds">Fires if any of these prices below par. Capacity is
    contracts at <b>size ≥ 1</b>; fractional quotes are not liquidity.</div>`;
}

/* -------------------------------------------------------------- spread --- */
const BUCKETS = [[1, 1], [2, 2], [3, 4], [5, 8], [9, 16], [17, 32], [33, 100]];

function renderSpread(p) {
  const el = $("spread");
  const m = p.metrics;
  if (!m) {
    el.innerHTML = `<div class="nodata-block"><b>no data</b>
      Spread statistics come from the full sweep.</div>`;
    $("spread-seg").innerHTML = "";
    $("spread-meta").textContent = "";
    return;
  }
  const segs = m.spread_by_segment || {};
  $("spread-seg").innerHTML = Object.keys(segs).map((k) =>
    `<button data-seg="${esc(k)}" aria-pressed="${k === SEGMENT}">${esc(k.replace(/_/g, " "))}</button>`
  ).join("");
  $("spread-seg").querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => { SEGMENT = b.dataset.seg; render(lastPayload); }));

  const groups = segs[SEGMENT] || {};
  const wide = m.spread_exchange_wide;
  $("spread-meta").textContent =
    `exchange median ${cents(wide.median)}¢ · p10 ${cents(wide.p10)}¢`;

  const rows = Object.entries(groups)
    .sort((a, b) => b[1].n - a[1].n)
    .map(([name, s]) => `<div class="kv"><span class="k">${esc(name)}
        <span class="dim">n=${num(s.n)}</span></span>
      <span class="v num wrap">p10 ${cents(s.p10)}¢ · med <b>${cents(s.median)}¢</b>
        · &lt;1¢ ${s.sub_1c_share_pct}%</span></div>`)
    .join("");

  el.innerHTML = rows + `
    <div class="thresholds">
      Required mispricing for a taker-side basket, from the fee model plus half a
      spread per leg at the exchange median of ${cents(wide.median)}¢:
      <b>2 legs 10.5¢</b> · <b>4 legs 19.3¢</b> · <b>10 legs 36.3¢</b>.
      The observed basket-cost distribution does not reach them.
    </div>`;
}

/* --------------------------------------------------------------- ticks --- */
function renderTicks(p) {
  const el = $("ticks");
  const m = p.metrics;
  if (!m || !m.tick_structure) {
    el.innerHTML = `<div class="nodata-block"><b>no data</b>
      Tick structure comes from the full sweep.</div>`;
    return;
  }
  const seg = (m.spread_by_segment || {}).tick_structure || {};
  el.innerHTML = Object.entries(m.tick_structure).map(([name, v]) => {
    const s = seg[name];
    return `<div class="kv">
      <span class="k">${esc(name)}<br><span class="dim">${num(v.n)} markets · ${v.share_pct}%</span></span>
      <span class="v num">${s ? `med ${s.median}¢<br><span class="dim">&lt;1¢ ${s.sub_1c_share_pct}%</span>`
        : NO_DATA("fewer than 30 two-sided markets in this segment")}</span></div>`;
  }).join("");
}

/* ---------------------------------------------------------- partitions --- */
function renderPartitions(p) {
  const el = $("partitions");
  if (!p.partitions) {
    el.innerHTML = `<div class="nodata-block"><b>no data</b>
      Partitions are verified during the full sweep.</div>`;
    $("partitions-meta").textContent = "";
    return;
  }
  $("partitions-meta").textContent = `${p.partitions.length} fee-free`;
  if (!p.partitions.length) {
    el.innerHTML = `<div class="nodata-block"><b>none present</b>
      No fee-free series currently lists a verified-exhaustive partition.</div>`;
    return;
  }
  el.innerHTML = `<table><thead><tr>
      <th>Event</th><th class="r">Cost</th><th class="r">vs par</th>
      <th class="r">Cap</th><th class="r">Ann.</th></tr></thead><tbody>` +
    p.partitions.map((x) => {
      const gap = Number(x.cost_cents) - 100;
      return `<tr class="${x.below_par ? "flag" : ""}">
        <td>${esc(x.event)}</td>
        <td class="r">${esc(x.cost_cents)}¢</td>
        <td class="r ${gap < 0 ? "bad" : ""}">${gap >= 0 ? "+" : ""}${gap.toFixed(2)}¢</td>
        <td class="r ${Number(x.capacity_contracts) < 1 ? "dim" : ""}">${esc(x.capacity_contracts)}</td>
        <td class="r">${x.annualized_pct === null
          ? `<span class="dim">—</span>` : esc(x.annualized_pct) + "%"}</td></tr>`;
    }).join("") + `</tbody></table>
    <div class="thresholds">Annualized return is shown only for verified
    partitions priced below par. Unverified structures never get one.</div>`;
}

/* -------------------------------------------------------------- health --- */
function renderHealth(p) {
  const el = $("health");
  const rows = [];
  const sources = p.sources || {};
  if (!Object.keys(sources).length) {
    rows.push(`<div class="nodata-block"><b>no sources</b>
      No ingest source has reported yet.</div>`);
  }
  for (const [name, s] of Object.entries(sources)) {
    const a = age(s.age_seconds);
    rows.push(`<div class="kv"><span class="k">${esc(name)}
        ${s.last_error ? `<br><span class="bad" style="font-size:10px">${esc(s.last_error)}</span>` : ""}</span>
      <span class="v"><span class="${s.healthy ? "good" : "bad"}">${s.healthy ? "OK" : "STALE"}</span>
        ${a === null ? NO_DATA("never succeeded") : `<span class="dim">${a} ago</span>`}
        <br><span class="dim">${num(s.successes)} ok · ${num(s.failures)} fail</span></span></div>`);
  }
  const sweep = p.sweep || {};
  rows.push(`<div class="kv"><span class="k">Sweeps completed</span>
    <span class="v num">${num(sweep.count)}</span></div>`);
  rows.push(`<div class="kv"><span class="k">Last sweep duration</span>
    <span class="v num">${sweep.last_duration_seconds === null || sweep.last_duration_seconds === undefined
      ? NO_DATA("no sweep has completed") : sweep.last_duration_seconds.toFixed(0) + "s"}</span></div>`);
  rows.push(cadenceRow("Full sweep cadence",
    "exchange-wide stats; time constant is days", sweep,
    "needs two sweeps to measure"));
  rows.push(cadenceRow("Tracked-subset cadence",
    "fee-free series the findings rest on", p.tracked_loop || {},
    "tracked loop has not completed two cycles"));
  const tl = p.tracked_loop || {};
  rows.push(`<div class="kv"><span class="k">Tracked cycle duration
      <br><span class="dim">work per cycle, not the gap between cycles</span></span>
    <span class="v num">${tl.last_cycle_seconds === null || tl.last_cycle_seconds === undefined
      ? NO_DATA("tracked loop has not completed a cycle") : tl.last_cycle_seconds.toFixed(1) + "s"}
      ${p.tracked_age_seconds !== null && p.tracked_age_seconds !== undefined
        ? `<span class="dim">· ${age(p.tracked_age_seconds)} ago</span>` : ""}</span></div>`);
  const inv = p.invariant || {};
  rows.push(`<div class="kv"><span class="k">Invariant violations
      <br><span class="dim">bid_YES + bid_NO &gt; 100¢ — our book is wrong</span></span>
    <span class="v num ${inv.violations ? "bad" : "good"}">${num(inv.violations ?? 0)}</span></div>`);
  const ntfy = p.ntfy;
  rows.push(`<div class="kv"><span class="k">Push transport (ntfy)</span>
    <span class="v">${!ntfy ? NO_DATA("no sweep has completed")
      : ntfy.configured ? `<span class="good">CONFIGURED</span>`
        : `<span class="warn">NOT CONFIGURED</span>`}
      <br><span class="dim">${ntfy ? esc(ntfy.target) : ""}</span></span></div>`);
  rows.push(rateLimitRow(p));
  rows.push(suppressedLedgerRow(p));
  rows.push(`<div class="kv"><span class="k">Uptime</span>
    <span class="v num">${age(p.uptime_seconds) ?? NO_DATA("scanner has not started")}</span></div>`);
  rows.push(rssRow(p));
  el.innerHTML = rows.join("");
}

/* Achieved against configured, separately labelled. Drift between them is the
   early signal of throttling or backpressure, so it is shown as its own number
   rather than left for the reader to subtract. */
function cadenceRow(label, note, block, whyNoData) {
  const achieved = block.achieved_interval_seconds;
  const configured = block.configured_interval_seconds;
  if (achieved === null || achieved === undefined) {
    return `<div class="kv"><span class="k">${esc(label)}
      <br><span class="dim">${esc(note)}</span></span>
      <span class="v">${NO_DATA(whyNoData)}</span></div>`;
  }
  const drift = block.drift_seconds;
  const hasDrift = drift !== null && drift !== undefined;
  /* 10% of the configured interval: beyond that the loop is not keeping the
     cadence it was given. Below it, scheduling jitter. */
  const off = hasDrift && configured ? Math.abs(drift) > configured * 0.1 : false;
  return `<div class="kv"><span class="k">${esc(label)}
      <br><span class="dim">${esc(note)}</span></span>
    <span class="v num">${Math.round(achieved)}s achieved
      <br><span class="dim">${configured === null || configured === undefined
        ? NO_DATA("configured interval not published")
        : Math.round(configured) + "s configured"}
        ${hasDrift ? `· <span class="${off ? "warn" : "dim"}">${drift >= 0 ? "+" : ""}${Math.round(drift)}s drift</span>` : ""}
        ${block.samples ? `· ${num(block.samples)} samples` : ""}</span></span></div>`;
}

function rateLimitRow(p) {
  const rl = p.rate_limit;
  if (!rl) {
    return `<div class="kv"><span class="k">Rate-limit responses (429)</span>
      <span class="v">${NO_DATA("scanner has not reported rate-limit state")}</span></div>`;
  }
  const partial = rl.timestamps_retained < rl.lifetime;
  return `<div class="kv"><span class="k">Rate-limit responses (429)
      <br><span class="dim">any 429 is a health event, not routine</span></span>
    <span class="v num ${rl.lifetime ? "warn" : "good"}">${num(rl.lifetime)} lifetime
      <br><span class="dim">${num(rl.last_24h)} in the last 24h${
        partial ? ` · <span class="warn">only ${num(rl.timestamps_retained)} timestamps retained</span>` : ""}</span></span></div>`;
}

/* Flat is expected. Accumulating means the $25 floor is masking a real change —
   that is a read-the-ledger event, not a raise-the-floor event. */
const SUPPRESSED_TOOLTIP =
  "Flat is expected. Accumulating means the $25 floor is masking a real change " +
  "— that is a read-the-ledger event, not a raise-the-floor event.";

function suppressedLedgerRow(p) {
  const w = (p.below_par || {}).window;
  if (!w || w.daily === null || w.daily === undefined) {
    return `<div class="kv"><span class="k">Suppressed detections</span>
      <span class="v">${NO_DATA("suppression ledger not yet written")}</span></div>`;
  }
  return `<div class="kv" title="${esc(SUPPRESSED_TOOLTIP)}">
    <span class="k">Suppressed detections
      <br><span class="dim">rolling ${num(w.window_days)}d · flat is expected</span></span>
    <span class="v num">${num(w.count)}
      <br>${sparkline(w.daily)}
      <br><span class="dim">${w.max_dollar_value === null
        ? "no capacity×edge recorded"
        : `max $${esc(w.max_dollar_value)} · median $${esc(w.median_dollar_value)}`}</span></span></div>`;
}

/* Days before the ledger existed are drawn as gaps, not as zeros. A sparkline
   that renders "not observed" and "observed nothing" identically is asserting
   an observation it never made. */
function sparkline(daily) {
  const peak = Math.max(1, ...daily.map((d) => d.count));
  const bars = daily.map((d) => {
    if (!d.observed) return `<i class="unobserved" title="${esc(d.day)}: before the ledger existed"></i>`;
    const h = d.count === 0 ? 2 : Math.round((d.count / peak) * 100);
    return `<i style="height:${h}%" class="${d.count ? "" : "zero"}" title="${esc(d.day)}: ${d.count}"></i>`;
  }).join("");
  return `<span class="spark" aria-label="suppressed detections per day">${bars}</span>`;
}

function rssRow(p) {
  const proc = p.process || {};
  if (proc.rss_mb === null || proc.rss_mb === undefined) {
    return `<div class="kv"><span class="k">Resident memory</span>
      <span class="v">${NO_DATA("RSS is not readable on this platform")}</span></div>`;
  }
  return `<div class="kv"><span class="k">Resident memory
      <br><span class="dim">growth here precedes the box dying</span></span>
    <span class="v num">${num(proc.rss_mb, 1)} MB</span></div>`;
}

/* ----------------------------------------------------------- reference --- */
function renderReference(p) {
  const el = $("reference");
  const ref = p.reference || {};
  if (!Object.keys(ref).length) {
    el.innerHTML = `<div class="nodata-block"><b>no data</b>
      Neither reference feed has returned. There is no failover to Binance.US —
      it is a different exchange, and substituting it would corrupt the series.</div>`;
    return;
  }
  el.innerHTML = Object.entries(ref).map(([source, assets]) =>
    `<div class="kv"><span class="k">${esc(source)}</span><span class="v dim">
      ${esc((Object.values(assets)[0] || {}).source_host || "")}</span></div>` +
    Object.entries(assets).map(([asset, v]) =>
      `<div class="kv"><span class="k">&nbsp;&nbsp;${esc(asset)}</span>
        <span class="v num">${esc(v.mid)}<span class="dim"> mid</span></span></div>`).join("")
  ).join("");
}

/* ----------------------------------------------------------------- log --- */
function renderLog(p) {
  const el = $("log");
  const events = p.events || [];
  $("log-meta").textContent = `${events.length}`;
  if (!events.length) {
    el.innerHTML = `<div class="nodata-block"><b>no events</b>
      Nothing has been logged since the scanner started.</div>`;
    return;
  }
  el.innerHTML = events.map((e) =>
    `<div class="line"><span class="t">${clock(e.at)}</span>
      <span class="k ${esc(e.kind)}">${esc(e.kind)}</span>
      <span>${esc(e.message)}</span></div>`).join("");
}

/* ------------------------------------------------------------- chrome --- */
function renderChrome(p) {
  const silence = lastMessageAt === null ? null : (Date.now() - lastMessageAt) / 1000;
  const offlineByClock = silence !== null && silence > CLIENT_SILENCE_LIMIT_S;
  const offline = !p.online || offlineByClock;

  const banners = [];
  if (offline) {
    /* Report whichever gap is LONGER. The heartbeat age inside the payload
       stopped advancing the moment the payload did, so trusting it alone
       understates the outage -- a frozen number describing a freeze. */
    const frozen = p.heartbeat_age_seconds;
    const worst = Math.max(silence ?? 0, frozen ?? 0);
    banners.push(`<div class="banner offline">Scanner offline — no data for
      ${worst ? age(worst) : "an unknown period"}.
      Figures below are frozen at that moment and are not current.</div>`);
  }
  if (p.snapshot_source) {
    banners.push(`<div class="banner snapshot">Snapshot mode —
      ${esc(p.snapshot_source)}. Real measured data, not live.</div>`);
  }
  $("banners").innerHTML = banners.join("");

  const m = p.metrics;
  $("strip").innerHTML = [
    `<span>${offline ? `<b class="bad">OFFLINE</b>` : `<b>LIVE</b>`}</span>`,
    `<span class="sep">·</span>`,
    `<span>markets <b>${m ? num(m.universe.n_markets) : "—"}</b></span>`,
    `<span>two-sided <b>${m ? num(m.universe.n_two_sided) : "—"}</b></span>`,
    `<span>median spread <b>${m ? cents(m.spread_exchange_wide.median) + "¢" : "—"}</b></span>`,
    `<span>fee-free <b>${m ? m.fee_free.n_markets : "—"}</b></span>`,
    `<span>sweeps <b>${p.sweep ? num(p.sweep.count) : "—"}</b></span>`,
    `<span>uptime <b>${age(p.uptime_seconds) ?? "—"}</b></span>`,
  ].join("");
  document.body.dataset.offline = offline ? "1" : "0";
}

function render(p) {
  if (!p) return;
  lastPayload = p;
  renderChrome(p);
  renderHero(p);
  renderFunnel(p);
  renderTriggers(p);
  renderTripwire(p);
  renderSpread(p);
  renderTicks(p);
  renderPartitions(p);
  renderHealth(p);
  renderReference(p);
  renderLog(p);
}

/* Notification transport is NOT this dashboard's job. Foreground-only Web
 * Notifications were removed rather than left as a dead path: an instrument
 * that will almost never be open cannot be alerted by a page that must be open.
 * Pushing is ntfy's job, server-side -- see scanner/notify.py and docs/DEPLOY.md.
 * PWA for browsing, ntfy for pushing.
 */

/* Re-render on a timer as well as on push, so ages and the offline banner keep
   moving when the socket has gone quiet. A dashboard that renders stale numbers
   cheerfully is the failure this exists to prevent. */
setInterval(() => render(lastPayload), 2000);

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${proto}://${location.host}/ws`);
  socket.onmessage = (ev) => {
    lastMessageAt = Date.now();
    render(JSON.parse(ev.data));
  };
  socket.onclose = () => setTimeout(connect, 3000);
  socket.onerror = () => socket.close();
}

fetch("/api/state").then((r) => r.json()).then((p) => {
  lastMessageAt = Date.now();
  render(p);
}).catch(() => render(null));
connect();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
