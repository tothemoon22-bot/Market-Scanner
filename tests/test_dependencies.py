"""Why each declared-but-unimported dependency is there, as assertions.

`websockets` is declared and imported by nothing. Deleting it as dead weight
would have been defensible from the imports alone, and would have silently
turned off the dashboard's live push: `uvicorn` requires only `click` and
`h11`, and `uvicorn/protocols/websockets/auto.py` sets `AutoWebSocketsProtocol`
to `None` when neither `websockets` nor `wsproto` is installed, after which the
server rejects the `/ws` upgrade. The page still loads. Strictly worse than the
undeclared-`fastapi` case, which at least crashed at import.

The general rule, and it is the one worth carrying off this project:

> **"Imported nowhere" is evidence of "unused" only for dependencies resolved
> by import.** Anything resolved at runtime -- protocol auto-detection, plugin
> registries, driver lookup by URL scheme, entry points -- is invisible to
> static analysis by construction. Ask how each dependency is *resolved* before
> concluding anything from where it appears.

So every declared dependency that nothing imports gets a row here and a status,
and the status is an assertion rather than a comment. A comment can be argued
with by the next person pruning dependencies; a failing test cannot.

`test_the_registry_covers_every_unimported_dependency` is the part that cannot
rot: add a dependency nothing imports and the suite demands a classification.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import requires
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHIPPED = ("dashboard", "scanner", "monitor", "src")

DEAD = "dead"
DORMANT = "dormant"
RUNTIME = "runtime-resolved"
TRANSITIVE = "transitive"

#: Every declared dependency that no first-party module imports, and why.
#: ``resolution`` is the question that matters: static analysis sees "import"
#: and nothing else.
REGISTRY: dict[str, dict[str, str]] = {
    "websockets": {
        "status": RUNTIME,
        "resolution": "uvicorn protocol auto-detection, at server startup",
        "why": (
            "uvicorn requires only click and h11. Without websockets or wsproto "
            "AutoWebSocketsProtocol is None and the /ws upgrade is rejected, so "
            "the dashboard's live push dies while the page still loads."
        ),
    },
    "aiosqlite": {
        "status": DORMANT,
        "resolution": "SQLAlchemy driver lookup from a sqlite+aiosqlite:// URL",
        "why": (
            "Same class as websockets and not transitively pulled in -- "
            "SQLAlchemy does not require aiosqlite; it resolves it from the URL "
            "scheme at connect time. Nothing constructs such a URL today and "
            "src/storage/ is empty, so it is dead *now*. It becomes invisibly "
            "load-bearing the moment the storage layer is used, which is what "
            "the guard below watches for."
        ),
    },
    "sqlalchemy": {
        "status": DEAD,
        "resolution": "import only",
        "why": (
            "Part 0 scaffolding for a storage layer that was never built. "
            "src/storage/ is an empty __init__.py and no module constructs an "
            "engine. Dead, and safe to conclude that from imports because "
            "SQLAlchemy is reached by import when it is reached at all."
        ),
    },
    "structlog": {
        "status": DEAD,
        "resolution": "import only",
        "why": (
            "Part 0 scaffolding for structured logging. The scanner logs into "
            "an in-memory ring on ScannerState instead; nothing configures or "
            "imports structlog anywhere."
        ),
    },
    "pydantic": {
        "status": TRANSITIVE,
        "resolution": "required by fastapi; installed regardless of this line",
        "why": (
            "Not load-bearing as a *direct* declaration -- fastapi requires "
            "pydantic, at a floor higher than ours, so removing our line changes "
            "nothing installed. Kept because it is harmless and because the "
            "guard below turns it load-bearing automatically if fastapi ever "
            "drops it."
        ),
    },
}


def declared() -> set[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    out = set()
    for spec in data["project"]["dependencies"]:
        name = re.split(r"[<>=!~\[; ]", spec.strip(), maxsplit=1)[0]
        out.add(name.strip().lower().replace("-", "_"))
    return out


def imported() -> set[str]:
    """Top-level third-party module names imported anywhere in shipped code."""
    first_party = {"dashboard", "scanner", "monitor", "src", "market_scanner"}
    found = set()
    for pkg in SHIPPED:
        for path in (REPO / pkg).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    if top not in sys.stdlib_module_names and top not in first_party:
                        found.add(top.lower())
    return found


def _extra_marked(requirement: str) -> bool:
    """True for a requirement that only applies under an extra.

    ``"aiosqlite; extra == 'aiosqlite'"`` is an *offer* -- installed only by
    ``sqlalchemy[aiosqlite]`` -- not a dependency. Reading the two as the same
    makes a package look transitively satisfied when nothing installs it.
    """
    marker = requirement.split(";", 1)[1] if ";" in requirement else ""
    return "extra" in marker


def shipped_text() -> dict[str, str]:
    return {
        str(p.relative_to(REPO)): p.read_text()
        for pkg in SHIPPED
        for p in (REPO / pkg).rglob("*.py")
    }


# --------------------------------------------------------------- the meta ---

def test_the_registry_covers_every_unimported_dependency() -> None:
    """The guard that cannot go stale.

    Declare something nothing imports and this fails until it is classified.
    Without it the registry describes whatever was true the day it was written,
    which is how a dependency list accumulates cargo in the first place.
    """
    unimported = declared() - imported()
    missing = unimported - set(REGISTRY)
    stale = set(REGISTRY) - unimported

    assert not missing, (
        f"declared but imported nowhere, and unclassified: {sorted(missing)}. "
        "Add a row to REGISTRY saying how it is resolved -- and do not assume "
        "'imported nowhere' means unused until you have checked whether it is "
        "resolved at runtime."
    )
    assert not stale, (
        f"REGISTRY rows no longer describe unimported dependencies: "
        f"{sorted(stale)}. Either the dependency is gone or something now "
        "imports it; either way the row is not describing the code."
    )


def test_every_row_states_how_the_dependency_is_resolved() -> None:
    for name, row in REGISTRY.items():
        assert row["status"] in {DEAD, DORMANT, RUNTIME, TRANSITIVE}, name
        assert row["resolution"].strip(), f"{name}: no resolution mechanism stated"
        assert len(row["why"]) > 80, f"{name}: reason too thin to act on"


# ------------------------------------------------------- the per-dep guards ---

def test_websockets_is_load_bearing_via_uvicorn_auto_detection() -> None:
    """The assertion the comment in pyproject.toml points at."""
    from uvicorn.protocols.websockets.auto import AutoWebSocketsProtocol

    assert AutoWebSocketsProtocol is not None, (
        "uvicorn resolved no WebSocket protocol. Neither websockets nor wsproto "
        "is installed, so the server would reject the /ws upgrade and the "
        "dashboard's live push would stop while the page still loaded."
    )
    # It is genuinely optional to uvicorn -- that is the whole trap. websockets
    # appears in uvicorn's metadata only behind `extra == "standard"`, and an
    # extra nobody installs satisfies nothing.
    hard = [r for r in (requires("uvicorn") or []) if not _extra_marked(r)]
    assert not any(r.lower().startswith("websockets") for r in hard), (
        "uvicorn now requires websockets unconditionally. Our declaration has "
        "become transitive; reclassify the REGISTRY row."
    )


def test_aiosqlite_and_sqlalchemy_are_dormant_because_storage_is_empty() -> None:
    """Both become load-bearing the day the storage layer is used, and
    ``aiosqlite`` becomes load-bearing *invisibly* -- SQLAlchemy resolves it
    from the URL scheme, so no import will ever appear to justify it."""
    storage = REPO / "src" / "storage"
    contents = [p for p in storage.rglob("*.py") if p.read_text().strip()]
    assert not contents, (
        f"src/storage/ now has content: {[str(p) for p in contents]}. "
        "sqlalchemy and aiosqlite are no longer dormant. Verify aiosqlite is "
        "declared -- SQLAlchemy does not require it and no import will show it "
        "-- and reclassify both REGISTRY rows."
    )

    # A driver URL is the other way it stops being dormant, storage layer or not.
    scheme = re.compile(r"(sqlite|postgresql|mysql)\+\w+://")
    hits = {p: scheme.search(t).group(0) for p, t in shipped_text().items() if scheme.search(t)}
    assert not hits, (
        f"a database URL now appears in shipped code: {hits}. The driver named "
        "after the '+' is resolved by scheme, never by import, so static "
        "analysis cannot tell you whether it is declared. Check it by hand."
    )

    # SQLAlchemy will not drag it in for us. Note the care needed reading this:
    # `aiosqlite` *does* appear in SQLAlchemy's metadata, but only behind
    # `extra == "aiosqlite"`, which nothing installs. An extra is an offer, not
    # a dependency, and treating the two as the same is how a package comes to
    # look transitively satisfied when it is not.
    hard = [r for r in (requires("sqlalchemy") or []) if not _extra_marked(r)]
    assert not any(r.lower().startswith("aiosqlite") for r in hard), (
        "SQLAlchemy now requires aiosqlite unconditionally; reclassify the row "
        "as transitive."
    )


def test_structlog_is_dead_and_import_analysis_settles_it() -> None:
    """Unlike the two above, nothing resolves structlog at runtime -- it is
    reached by import or not at all, so 'imported nowhere' *is* conclusive."""
    hits = {p: t for p, t in shipped_text().items() if re.search(r"\bstructlog\b", t)}
    assert not hits, (
        f"structlog now appears in {sorted(hits)}. It is classified dead; "
        "reclassify it."
    )


def test_pydantic_is_transitive_through_fastapi() -> None:
    fastapi_requires = requires("fastapi") or []
    pinned = [r for r in fastapi_requires if r.lower().startswith("pydantic")]
    assert pinned, (
        "fastapi no longer requires pydantic. Our direct declaration just became "
        "load-bearing rather than redundant -- reclassify the REGISTRY row and "
        "check the floor is still right."
    )


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_each_registered_dependency_is_actually_declared(name: str) -> None:
    assert name in declared(), (
        f"{name} is in REGISTRY but not in [project].dependencies. The registry "
        "is meant to explain declarations, not to outlive them."
    )
