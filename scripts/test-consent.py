#!/usr/bin/env python3
"""Run only the native scope-consent checks; never present an approval dialog."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
build = root / '.build'
build.mkdir(exist_ok=True)
executable = build / 'consent-checks'
subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-target', 'arm64-apple-macosx13.0',
    '-module-cache-path', str(build / 'module-cache'),
    str(root / 'Sources/ContinuationConsent.swift'),
    str(root / 'tests/ContinuationConsentChecks.swift'),
    str(root / 'tests/ContinuationConsentRunner.swift'), '-o', str(executable)], check=True)
subprocess.run([str(executable)], check=True)
