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

The subprocess runs with ``cwd`` set to the repository root, because that is
what the deployment does: ``WorkingDirectory=/opt/market-scanner`` in the
systemd unit, and every documented invocation is ``python -m ...`` from there.
Note what that means -- ``[tool.setuptools.packages.find] include = ["src*"]``
installs *only* ``src``, so ``dashboard``, ``scanner`` and ``monitor`` are
importable solely because the working directory is on ``sys.path``. That is
latent, not live, and it is recorded in ``docs/NEGATIVE_RESULT.md`` rather than
changed here.

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

    probe = subprocess.run(
        [str(python), "-c", PROBE, json.dumps(modules), json.dumps(sorted(imports))],
        capture_output=True, text=True, timeout=300, cwd=str(REPO),
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
            "the declared dependencies do not support the shipped code.\n"
            + "\n".join(f"  {f}" for f in failures)
            + ("\n\nimport sites:\n" + "\n".join(
                f"  {n}: {', '.join(s)}" for n, s in sites.items()) if sites else "")
            + "\n\nAdd the missing distributions to [project].dependencies in "
              "pyproject.toml. Note the distribution name may differ from the "
              "module name."
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

    assert "uvicorn" in imports, "function-scoped imports are not being found"
    assert imports["uvicorn"] == ["dashboard/app.py:156"], imports["uvicorn"]

    assert "httpx" in imports
    assert len(imports["httpx"]) >= 8, imports["httpx"]

    # First-party and stdlib must not leak into the third-party set, or the
    # probe would try to pip-resolve them.
    for name in ("monitor", "scanner", "src", "dashboard", "json", "decimal"):
        assert name not in imports, f"{name} classified as third-party"
