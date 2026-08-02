#!/usr/bin/env python3
"""Pre-commit secret scanner (hard constraint 4).

Scans the files handed to it by pre-commit, or every tracked file when run with
``--all``. Exits non-zero on a hit. Deliberately dependency-free so it works in
a bare checkout.

This is a backstop, not a guarantee. It will not catch a secret that looks like
ordinary prose. Keep credentials in `.env` (gitignored) or the OS keychain.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Files that are allowed to contain credential-shaped strings, because their
# whole purpose is to show the shape of a credential.
ALLOWLISTED_NAMES = {".env.example", "secret_scan.py"}

BINARY_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".db", ".sqlite", ".pyc"}

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    (
        "assigned credential literal",
        re.compile(
            r"(?i)\b(api[_-]?key|api[_-]?secret|access[_-]?token|private[_-]?key|password|passphrase)"
            r"\s*[:=]\s*['\"][^'\"\s${}<>]{12,}['\"]"
        ),
    ),
    (
        "Kalshi key id assignment",
        re.compile(
            r"(?i)kalshi[_-]?(api[_-]?)?key[_-]?id\s*[:=]\s*"
            r"['\"]?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        ),
    ),
]

FORBIDDEN_PATHS = (".env", "config/production.toml", "kalshi-production.pem")


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [Path(line) for line in out.splitlines() if line]


def scan(path: Path) -> list[str]:
    findings: list[str] = []
    name = path.name
    posix = path.as_posix()

    if posix in FORBIDDEN_PATHS or name == ".env":
        return [f"{posix}: this file must never be committed"]
    if name in ALLOWLISTED_NAMES or path.suffix in BINARY_SUFFIXES:
        return findings
    if not path.is_file():
        return findings

    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return findings

    for lineno, line in enumerate(text.splitlines(), start=1):
        if "secret-scan: allow" in line:
            continue
        for label, pattern in PATTERNS:
            if pattern.search(line):
                findings.append(f"{posix}:{lineno}: possible {label}")
                break
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--all", action="store_true", help="scan every tracked file")
    args = parser.parse_args()

    targets = tracked_files() if args.all or not args.files else args.files

    findings: list[str] = []
    for path in targets:
        findings.extend(scan(path))

    if findings:
        print("Secret scan failed:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        print(
            "\nIf this is a false positive, append '# secret-scan: allow' to the line.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
