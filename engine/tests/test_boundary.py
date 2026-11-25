"""Spec §2.1: the engine library has no knowledge of the database."""

import pkgutil
import subprocess
import sys

import coglossa

# Modules that may use the database; everything else in coglossa is library code.
DATABASE_MODULES = (
    "coglossa.store",
    "coglossa.service",
    "coglossa.catalog",
    "coglossa.access",  # who sees which sets: queries the store (CGL-26)
    "coglossa.review",
    "coglossa.review_summary",  # reads the review view's stored data (CGL-112)
    "coglossa.worker",
    "coglossa.doctor",
    "coglossa.cli_ops",
    "coglossa.cli",  # imports cli_ops
    "coglossa.testing.postgres",
    "coglossa.testing.pytest_db",
    "coglossa.testing.seed",
)


def _is_database_module(name: str) -> bool:
    return any(name == d or name.startswith(d + ".") for d in DATABASE_MODULES)


LIBRARY = sorted(
    m.name
    for m in pkgutil.walk_packages(coglossa.__path__, "coglossa.")
    if not _is_database_module(m.name)
)
FORBIDDEN = ("sqlalchemy", "alembic", "psycopg", "procrastinate")


def test_library_modules_do_not_import_database_packages():
    code = (
        "import importlib, sys\n"
        f"for m in {LIBRARY!r}: importlib.import_module(m)\n"
        f"bad = sorted(m for m in sys.modules if m.split('.')[0] in {FORBIDDEN!r})\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "", f"library imports database packages: {result.stdout}"


def test_discovery_finds_the_library():
    for name in ("coglossa.export.docx", "coglossa.ingest.pdf", "coglossa.schema.views"):
        assert name in LIBRARY
    assert "coglossa.catalog" not in LIBRARY
