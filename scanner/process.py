"""Process self-measurement: resident memory, so growth is visible before it kills the box.

Dependency-free by design -- ``/proc/self/status`` on Linux, ``None`` anywhere
else. **``None`` is not zero.** A platform where RSS cannot be read renders NO
DATA rather than a plausible-looking number, which is the same rule every other
panel follows.

The reading is deliberately cheap and allocation-free: a memory gauge that
itself grows with each sample would share the failure mode of the thing it
measures.
"""

from __future__ import annotations

from pathlib import Path

STATUS = Path("/proc/self/status")


def rss_bytes(status: Path = STATUS) -> int | None:
    """Current resident set size in bytes, or None where it cannot be read."""
    try:
        text = status.read_text()
    except (OSError, ValueError):
        return None
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            # "VmRSS:   123456 kB"
            if len(parts) >= 3 and parts[2].lower() == "kb":
                try:
                    return int(parts[1]) * 1024
                except ValueError:
                    return None
            return None
    return None


def as_dict(status: Path = STATUS) -> dict[str, object]:
    rss = rss_bytes(status)
    return {
        "rss_bytes": rss,
        "rss_mb": None if rss is None else round(rss / (1024 * 1024), 1),
        "source": str(status) if rss is not None else None,
    }
