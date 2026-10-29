#!/usr/bin/env python3
"""Run the existing offline regression suites; stop immediately on a failure."""
from pathlib import Path
import os
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'}
model_checks = [[sys.executable, 'scripts/test-model.py']] if sys.platform == 'darwin' else []
if not model_checks:
    print('NOT RUN: Swift model checks require macOS and Xcode; this is not a passing result.', flush=True)
for command in [[sys.executable, 'scripts/check-public.py'],
                [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                *model_checks,
                *[['node', str(p.relative_to(root))] for p in sorted((root / 'tests').glob('test_*.js'))]]:
    subprocess.run(command, cwd=root, env=env, check=True)
