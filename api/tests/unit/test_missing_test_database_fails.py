"""Guards the ClinicQ trap: database tests that skip silently while the suite looks green."""

import os
import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2]


def test_pytest_fails_instead_of_skipping_when_test_database_url_is_unset() -> None:
    env = {k: v for k, v in os.environ.items() if k != "TEST_DATABASE_URL"}

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/test_config.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "TEST_DATABASE_URL is not set" in output
    assert "passed" not in output
    assert "skipped" not in output
