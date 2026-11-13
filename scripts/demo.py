"""Demonstra as duas ferramentas com o Python do ambiente do agente."""

import os
import subprocess
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
environment = dict(os.environ)
local_browsers = project_root / ".tools" / "browsers"
if local_browsers.is_dir():
    environment["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)

for example in ("pytest-project", "playwright-project"):
    config_path = project_root / "examples" / example / "qualitybridge.yaml"
    for suite in ("full", "quick"):
        completed = subprocess.run(
            [sys.executable, "-m", "qualitybridge", "run", "--config", str(config_path),
             "--suite", suite], env=environment, check=False,
        )
        if completed.returncode:
            sys.exit(completed.returncode)
    completed = subprocess.run(
        [sys.executable, "-m", "qualitybridge", "history", "--config", str(config_path),
         "--limit", "2"], env=environment, check=False,
    )
    if completed.returncode:
        sys.exit(completed.returncode)
