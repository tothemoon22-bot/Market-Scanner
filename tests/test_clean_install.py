"""Does the package install and import from its *declared* dependencies alone?

Every other test in this suite runs inside the development environment, where
``fastapi`` and ``uvicorn`` happened to be present because something else had
pulled them in. ``pyproject.toml`` never declared either. So the whole suite was
green while ``pip install -e .`` on a clean box died on
``dashboard/app.py:23  from fastapi import FastAPI`` -- which is exactly how it
was found: on the deploy, by hand.

That is not a two-code-path bug. It is *one* code path in two environments, and
no test that shares the developer's interpreter can see it. This test therefore
builds a real virtualenv, installs the project into it from the declared
metadata only, and imports the shipped code with that interpreter.

Three things are checked, and each catches a different failure:

1. **Every shipped module imports.** Catches an undeclared dependency that is
   imported at module scope -- the ``fastapi`` case.
2. **Every third-party module named anywhere in shipped code imports.** Catches
   an undeclared dependency imported inside a function, which check 1 cannot
   see: ``uvicorn`` is imported at ``dashboard/app.py:156``, inside ``main()``,
   so ``import dashboard.app`` succeeds and the server dies on startup instead.
   The list is derived by walking the AST, not hand-maintained, so a new import
   anywhere in shipped code is covered the day it lands.
3. **uvicorn can resolve a WebSocket protocol.** Catches removal of a
   dependency that no first-party module imports but the runtime still needs.
   See the comment on ``websockets`` in ``pyproject.toml``.

**The probe runs from a temporary directory, not from the repository root.**
The first version of this test ran with ``cwd=REPO`` on the reasoning that the
deployment does the same -- ``WorkingDirectory=/opt/market-scanner`` in the
systemd unit. That reasoning imported the bug it was written to find. Running
from the repository root puts the repository on ``sys.path``, so
``import dashboard`` succeeded *via the working directory* and the test could
not see that ``[tool.setuptools.packages.find]`` packaged only ``src``. A test
built to catch environment divergence, written inside the environment whose
assumption it needed to test, is the same failure as the suite it replaced --
one layer further in.

So the probe chdirs somewhere with no relationship to the source tree. What it
proves is narrower and actually true: the *installed distribution* provides the
packages. Note the limit of that claim -- ``dashboard/app.py`` still reads
``Path("monitor/baseline.json")`` and every ledger under ``data/monitor/`` is
equally relative, so ``WorkingDirectory`` remains load-bearing for **data**.
The packaging fix removed the import coupling and nothing more.

The test takes ~20s and needs a package index. Set
``MARKET_SCANNER_SKIP_CLEAN_INSTALL=1`` to skip it deliberately; it does not
skip itself on failure to reach the network, because a check that quietly
excuses itself is the failure mode this repository keeps finding.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHIPPED = ("dashboard", "scanner", "monitor", "src")
FIRST_PARTY = {"dashboard", "scanner", "monitor", "src", "market_scanner", "tests", "tools"}


def shipped_files() -> list[Path]:
    return sorted(p for pkg in SHIPPED for p in (REPO / pkg).rglob("*.py"))


def module_name(path: Path) -> str:
    parts = list(path.relative_to(REPO).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_function_scoped(path: Path, module: str) -> bool:
    """True if ``module`` is imported inside a function body in ``path``."""
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Import) and any(
                a.name.split(".")[0] == module for a in inner.names
            ):
                return True
    return False


def third_party_imports() -> dict[str, list[str]]:
    """{top-level module: ["relpath:lineno", ...]} for every non-stdlib import.

    Walks the whole AST, so imports nested inside functions are found too --
    that is the half of the problem a module-level import test misses.
    """
    found: dict[str, list[str]] = {}
    for path in shipped_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top in sys.stdlib_module_names or top in FIRST_PARTY:
                    continue
                found.setdefault(top, []).append(
                    f"{path.relative_to(REPO)}:{node.lineno}"
                )
    return found


# Runs inside the clean interpreter. Collects every failure rather than dying on
# the first, so one run reports the whole gap instead of one deploy's worth.
PROBE = """
import importlib, json, sys
modules, third_party = json.loads(sys.argv[1]), json.loads(sys.argv[2])
failures = []
for name in modules + third_party:
    try:
        importlib.import_module(name)
    except BaseException as exc:
        failures.append(f"{name}: {type(exc).__name__}: {exc}")
try:
    from uvicorn.protocols.websockets.auto import AutoWebSocketsProtocol
    if AutoWebSocketsProtocol is None:
        failures.append(
            "uvicorn resolved no WebSocket protocol: neither websockets nor "
            "wsproto is installed, so the server would reject /ws"
        )
except BaseException as exc:
    failures.append(f"uvicorn websocket auto-detect: {type(exc).__name__}: {exc}")
print(json.dumps(failures))
"""


@pytest.mark.slow
def test_declared_dependencies_are_sufficient(tmp_path: Path) -> None:
    if os.environ.get("MARKET_SCANNER_SKIP_CLEAN_INSTALL") == "1":
        pytest.skip("MARKET_SCANNER_SKIP_CLEAN_INSTALL=1 set deliberately")

    env_dir = tmp_path / "clean"
    venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
    python = env_dir / "bin" / "python"
    assert python.exists(), f"venv produced no interpreter at {python}"

    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet",
         "--disable-pip-version-check", "-e", str(REPO)],
        capture_output=True, text=True, timeout=900,
    )
    assert install.returncode == 0, (
        "pip install -e . failed in a clean environment:\n"
        f"{install.stdout}\n{install.stderr}"
    )

    imports = third_party_imports()
    modules = [module_name(p) for p in shipped_files()]
    assert modules, "found no shipped modules to import -- the walk is broken"

    # Somewhere with no relationship to the source tree. `python -c` puts the
    # working directory on sys.path, so running this from REPO would let the
    # repository itself satisfy the imports and hide whether the *installed
    # distribution* provides them.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    probe = subprocess.run(
        [str(python), "-c", PROBE, json.dumps(modules), json.dumps(sorted(imports))],
        capture_output=True, text=True, timeout=300, cwd=str(elsewhere),
    )
    assert probe.returncode == 0, (
        f"probe crashed:\n{probe.stdout}\n{probe.stderr}"
    )

    failures = json.loads(probe.stdout.strip().splitlines()[-1])
    if failures:
        sites = {
            name: imports.get(name, [])
            for name in imports
            if any(f.startswith(f"{name}:") for f in failures)
        }
        pytest.fail(
            "a clean install does not support the shipped code, imported from "
            f"{elsewhere}.\n"
            + "\n".join(f"  {f}" for f in failures)
            + ("\n\nimport sites:\n" + "\n".join(
                f"  {n}: {', '.join(s)}" for n, s in sites.items()) if sites else "")
            + "\n\nA missing third-party module means adding the distribution to "
              "[project].dependencies (the distribution name may differ from the "
              "module name). A missing *first-party* package -- dashboard, "
              "scanner, monitor, src -- means it is not covered by "
              "[tool.setuptools.packages.find], and only worked before because "
              "the working directory was on sys.path."
        )


def test_the_data_paths_are_still_cwd_relative() -> None:
    """The known residual, pinned so the claim cannot rot.

    Packaging fixed the *import* coupling to the working directory. It did not
    fix the *data* coupling: ``dashboard/app.py`` reads
    ``Path("monitor/baseline.json")`` and every ledger under ``data/monitor/``
    is equally relative, so ``WorkingDirectory=/opt/market-scanner`` is still
    load-bearing and one relocation still breaks the box.

    This test exists so that sentence stays true of the code. If someone makes
    the paths root-relative, this fails -- which is the prompt to delete it and
    update ``docs/NEGATIVE_RESULT.md``, not to weaken it.
    """
    from dashboard import app as dashboard_app
    from monitor import acknowledgments, archive, discontinuity, reviews
    from scanner import history

    still_relative = {
        "dashboard.app.BASELINE": dashboard_app.BASELINE,
        "monitor.archive.ARCHIVE_DIR": archive.ARCHIVE_DIR,
        "monitor.reviews.LEDGER_PATH": reviews.LEDGER_PATH,
        "monitor.acknowledgments.LEDGER_PATH": acknowledgments.LEDGER_PATH,
        "monitor.discontinuity.LEDGER_PATH": discontinuity.LEDGER_PATH,
        "scanner.history.HISTORY_PATH": history.HISTORY_PATH,
    }
    unexpected = {n: str(p) for n, p in still_relative.items() if p.is_absolute()}
    assert not unexpected, (
        f"these are no longer cwd-relative: {unexpected}. The residual documented "
        "in docs/NEGATIVE_RESULT.md has been fixed -- update the memo and delete "
        "this test rather than relaxing it."
    )


def test_audit_finds_the_imports_it_is_supposed_to() -> None:
    """The AST walk is the test's evidence; check it against known ground truth.

    If this walk silently stopped finding anything, the test above would pass
    vacuously -- the shape this repository files under "the check that cannot
    fail". ``uvicorn`` is the load-bearing case: function-scoped, so it proves
    the walk reaches nested nodes.
    """
    imports = third_party_imports()

    assert "fastapi" in imports
    assert any(s.startswith("dashboard/app.py:") for s in imports["fastapi"])

    # uvicorn is the load-bearing case: it is imported inside `main()`, so a
    # module-level import test finds `fastapi` and misses this one entirely.
    # Asserted on the *property* rather than on a line number -- the line has
    # moved twice, and a test that breaks whenever an unrelated edit shifts it
    # trains people to update the number without reading the claim.
    assert "uvicorn" in imports, "function-scoped imports are not being found"
    assert all(s.startswith("dashboard/app.py:") for s in imports["uvicorn"])
    assert _is_function_scoped(REPO / "dashboard" / "app.py", "uvicorn"), (
        "uvicorn is no longer imported inside a function. If it moved to module "
        "scope this assertion has lost its subject -- find another function-"
        "scoped import to pin the AST walk with, or the walk goes unverified."
    )

    assert "httpx" in imports
    assert len(imports["httpx"]) >= 8, imports["httpx"]

    # First-party and stdlib must not leak into the third-party set, or the
    # probe would try to pip-resolve them.
    for name in ("monitor", "scanner", "src", "dashboard", "json", "decimal"):
        assert name not in imports, f"{name} classified as third-party"
