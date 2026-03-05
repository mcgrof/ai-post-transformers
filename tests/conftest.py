"""Shared pytest fixtures for paper-feed tests."""

import sys
from pathlib import Path

import pytest
import yaml

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(scope="session")
def project_root():
    return Path(__file__).parent.parent


@pytest.fixture(scope="session")
def config(project_root):
    with open(project_root / "config.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def regression_cases(project_root):
    with open(project_root / "tests" / "regression_cases.yaml") as f:
        return yaml.safe_load(f)["cases"]


@pytest.fixture(scope="session")
def editorial_scorer(config):
    """Shared EditorialScorer instance (expensive to init)."""
    from editorial_scorer import EditorialScorer
    return EditorialScorer(config, podcasted_ids=set())
