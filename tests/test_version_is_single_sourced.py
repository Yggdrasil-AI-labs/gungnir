"""The package version must have exactly one source of truth.

2026-09-21: `gungnir/__version__.py` said 0.2.0 while `pyproject.toml` still
said 0.1.6. The code was right and the distribution metadata was wrong, so
`pip install --upgrade` was a no-op -- pip already had "0.1.6" and the new
tarball also called itself 0.1.6. The installed code changed underneath a
version number that did not, which is the exact failure Muninn's gungnir
version guard exists to catch, arriving from the packaging side instead.

pyproject now reads the version from the package attribute. This test holds
that arrangement in place: any reintroduced literal in pyproject fails here
rather than six weeks later on someone's feeder.

No tomllib. It is stdlib only from 3.11, and this library supports 3.10 and
is CI-tested on it. The first version of this file imported tomllib and
broke the 3.10 leg for two hours. The facts asserted here are textual, so
they are read with a small section-aware scan instead -- and where tomllib
IS available, the scan is checked against it, so the fallback cannot quietly
drift from the real parser.
"""
from __future__ import annotations

import pathlib
import re

import gungnir

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _section(name: str) -> list[str]:
    """The lines inside a [table], excluding the header."""
    out: list[str] = []
    in_it = False
    for line in PYPROJECT.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_it = stripped == f"[{name}]"
            continue
        if in_it:
            out.append(stripped)
    return out


def test_pyproject_does_not_hardcode_a_version():
    literals = [ln for ln in _section("project")
                if re.match(r"^version\s*=", ln)]
    assert not literals, (
        f"pyproject.toml carries its own version literal ({literals}); it "
        f"drifted from gungnir/__version__.py once already")


def test_the_version_is_declared_dynamic():
    dynamic = [ln for ln in _section("project")
               if re.match(r"^dynamic\s*=", ln)]
    assert dynamic and "version" in dynamic[0], (
        "the version must be declared dynamic so it comes from the package")


def test_pyproject_points_at_the_package_attribute():
    lines = _section("tool.setuptools.dynamic")
    assert any("gungnir.__version__.__version__" in ln for ln in lines), (
        f"[tool.setuptools.dynamic] must read the version from the package "
        f"attribute, got {lines}")


def test_the_scan_agrees_with_a_real_toml_parser():
    # Only runs where tomllib exists (3.11+). It stops the hand-rolled scan
    # above from drifting into agreeing with nothing.
    try:
        import tomllib
    except ModuleNotFoundError:
        return
    parsed = tomllib.loads(PYPROJECT)
    assert "version" not in parsed["project"]
    assert "version" in parsed["project"].get("dynamic", [])
    assert parsed["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "gungnir.__version__.__version__"}


def test_the_installed_metadata_matches_the_code():
    # Only meaningful once installed; skipped in a bare checkout.
    from importlib import metadata
    try:
        dist = metadata.version("gungnir")
    except metadata.PackageNotFoundError:
        return
    assert dist == gungnir.__version__, (
        f"installed distribution says {dist} but the code says "
        f"{gungnir.__version__}. Reinstall, or the two have drifted again.")
