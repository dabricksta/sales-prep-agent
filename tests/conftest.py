from __future__ import annotations

import json

import pytest

from salesprep.config import REPO_ROOT, Config
from salesprep.crm import CRM, ApprovalQueue


@pytest.fixture
def config(tmp_path):
    """Isolated state under tmp_path; read-only fixtures and seed data come from the repo."""
    return Config(root=tmp_path, data_dir=REPO_ROOT / "data")


@pytest.fixture
def stores(config):
    crm = CRM(config.db_path)
    crm.seed(json.loads(config.seed_path.read_text(encoding="utf-8")))
    return crm, ApprovalQueue(config.db_path)
