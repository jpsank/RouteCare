"""Shared pytest configuration for the solver service.

- Forces single-worker CP-SAT for deterministic test runs.  Multi-worker
  parallel search produces different near-optimal solutions across runs
  even with a fixed seed, which would make test outcomes flaky.
- Registers the `slow` marker for heavy benchmark scenarios (scale ≥100
  instances, warm-start stability, adversarial multi-round).  Those are
  skipped by default; run them explicitly with `--run-slow`.

Usage:
    pytest                    # fast tests only
    pytest --run-slow         # everything
    pytest -m slow            # only slow tests
    pytest -k scale           # filter by name
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    # Force deterministic single-worker CP-SAT for the entire session.
    os.environ.setdefault("SOLVER_NUM_WORKERS", "1")
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect by default; use --run-slow to opt in)",
    )


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow", action="store_true", default=False,
        help="Run tests marked as slow",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="slow: use --run-slow to enable")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
