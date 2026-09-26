"""Required artifacts, selected by the verification entrypoint."""
import os
from pathlib import Path


def required_artifact(name):
    value = os.environ.get(name)
    if not value or not Path(value).exists():
        raise RuntimeError(f"Required artifact {name} is missing. Run python -m tools.project verify.")
    return Path(value).resolve()


def fixture_root():
    return Path(os.environ.get("PROJECT_FIXTURE_ROOT", Path(__file__).resolve().parents[2]))
