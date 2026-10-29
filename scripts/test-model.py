#!/usr/bin/env python3
"""Run Swift model checks against temporary stores and a fake Terminal bridge."""
from pathlib import Path
import subprocess
import json
import sys

root = Path(__file__).resolve().parents[1]
build = root / ".build"
build.mkdir(exist_ok=True)
(build / 'runtime.json').write_text(json.dumps({'python': sys.executable}))
executable = build / "model-tests"
subprocess.run([
    "xcrun", "swiftc", "-parse-as-library", "-target", "arm64-apple-macosx13.0",
    "-module-cache-path", str(build / "module-cache"),
    str(root / "Sources/Model.swift"), str(root / "Sources/TerminalBridge.swift"),
    str(root / "Sources/ClaudeTrust.swift"),
    str(root / "Sources/StartupPrompt.swift"),
    str(root / "Sources/TerminalInput.swift"),
    str(root / "Sources/LocalWebBridge.swift"),
    str(root / "Sources/RuntimeSupport.swift"),
    str(root / "tests/ModelTests.swift"), "-o", str(executable),
], check=True)
subprocess.run([str(executable)], check=True)
