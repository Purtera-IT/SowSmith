from __future__ import annotations

from pathlib import Path

import pytest

from scripts.make_demo_fixtures import create_demo_project


@pytest.fixture()
def demo_project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    return create_demo_project(root)
