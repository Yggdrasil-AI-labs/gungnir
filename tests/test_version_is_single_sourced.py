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
"""
from __future__ import annotations

import pathlib
import tomllib

import gungnir

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_does_not_hardcode_a_version():
    project = _pyproject()["project"]
    assert "version" not in project, (
        "pyproject.toml must not carry its own version literal; it drifted "
        "from gungnir/__version__.py once already")
    assert "version" in project.get("dynamic", []), (
        "the version must be declared dynamic so it comes from the package")


def test_pyproject_points_at_the_package_attribute():
    dynamic = _pyproject()["tool"]["setuptools"]["dynamic"]
    assert dynamic["version"] == {"attr": "gungnir.__version__.__version__"}


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
