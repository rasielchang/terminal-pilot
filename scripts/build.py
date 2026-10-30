#!/usr/bin/env python3
"""Build a local development app or a portable, self-contained macOS preview."""
import argparse
import json
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
from bundle_runtime import bundle, sign

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".build"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=ROOT / "dist/Terminal Pilot.app",
                    help="Build a separate app bundle without modifying the running installation")
parser.add_argument('--portable', action='store_true', help='Bundle hash-locked Python, Node and Python dependencies')
parser.add_argument('--identity', default='-', help='Developer ID Application identity; default is local ad-hoc signing')
options = parser.parse_args()
APP = options.output.expanduser().resolve()
CONTENTS = APP / "Contents"
RESOURCES = CONTENTS / "Resources"
for directory in [BUILD, CONTENTS / "MacOS", RESOURCES]:
    directory.mkdir(parents=True, exist_ok=True)

sources = sorted((ROOT / "Sources").glob("*.swift"))
executable = CONTENTS / "MacOS/TerminalPilot"
if not executable.exists() or any(p.stat().st_mtime > executable.stat().st_mtime for p in sources):
    subprocess.run(["xcrun", "swiftc", "-O", "-parse-as-library", "-target", "arm64-apple-macosx13.0",
                    "-module-cache-path", str(BUILD / "module-cache"), *map(str, sources),
                    "-o", str(executable)], check=True)
shutil.copy2(ROOT / "scripts/bridge.py", RESOURCES / "bridge.py")
shutil.copy2(ROOT / "scripts/web_server.py", RESOURCES / "web_server.py")
for name in ["pilot_http.py", "web_push.py", "tailscale_web.py", "voice_service.py", "voice_provider.py", "voice_keychain.py", "runtime_paths.py", "brain_service.py", "brain_targets.py", "brain_work.py", "brain_context.py", "brain_provider.py", "brain_codex.py", "codex_live.py", "codex_approvals.py", "codex_managed.py"]:
    shutil.copy2(ROOT / "scripts" / name, RESOURCES / name)
shutil.copy2(ROOT / 'scripts/codex_socket.js', RESOURCES / 'codex_socket.js')
shutil.copytree(ROOT / 'vendor/ws', CONTENTS / 'vendor/ws', dirs_exist_ok=True)
shutil.copytree(ROOT / "web", RESOURCES / "web", dirs_exist_ok=True)
runtime = {} if options.portable else {"python": sys.executable}
(RESOURCES / "runtime.json").write_text(json.dumps(runtime))
for name in ['LICENSE', 'THIRD_PARTY_NOTICES.md']:
    if (ROOT / name).exists(): shutil.copy2(ROOT / name, RESOURCES / name)
if options.portable: bundle(CONTENTS)
plist = {
    "CFBundleIdentifier": "local.rasiel.TerminalPilot",
    "CFBundleName": "Terminal Pilot", "CFBundleDisplayName": "Terminal Pilot",
    "CFBundleExecutable": "TerminalPilot", "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": "0.13.0", "CFBundleVersion": "40",
    "PilotReleaseChannel": "Preview",
    "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True,
    "NSAppleEventsUsageDescription": "管理 Terminal 視窗，並讓本地網頁讀取與回覆你選擇的 agent 工作。",
    "NSPrincipalClass": "NSApplication",
    "CFBundleIconFile": "Pilot.icns",
}
(CONTENTS / "Info.plist").write_bytes(plistlib.dumps(plist))
subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(BUILD / "module-cache"),
                str(ROOT / "scripts/icon.swift"), "-o", str(BUILD / "icon")], check=True)
subprocess.run([str(BUILD / "icon"), str(BUILD / "icon.png")], check=True)
for size, name in [(180, "apple-touch-icon.png"), (192, "icon-192.png"), (512, "icon-512.png")]:
    subprocess.run(["sips", "-z", str(size), str(size), str(BUILD / "icon.png"), "--out", str(ROOT / "web" / name)],
                   check=True, stdout=subprocess.DEVNULL)
    shutil.copy2(ROOT / "web" / name, RESOURCES / "web" / name)
iconset = BUILD / "Pilot.iconset"
iconset.mkdir(exist_ok=True)
for size in [16, 32, 128, 256, 512]:
    for scale in [1, 2]:
        suffix = "@2x" if scale == 2 else ""
        subprocess.run(["sips", "-z", str(size * scale), str(size * scale), str(BUILD / "icon.png"),
                        "--out", str(iconset / f"icon_{size}x{size}{suffix}.png")],
                       check=True, stdout=subprocess.DEVNULL)
# Encode PNG-backed ICNS entries directly; iconutil depends on services unavailable in some build sandboxes.
entries = [("icp4", "icon_16x16.png"), ("icp5", "icon_32x32.png"),
           ("ic07", "icon_128x128.png"), ("ic08", "icon_256x256.png"),
           ("ic09", "icon_512x512.png"), ("ic10", "icon_512x512@2x.png"),
           ("ic11", "icon_16x16@2x.png"), ("ic12", "icon_32x32@2x.png"),
           ("ic13", "icon_128x128@2x.png"), ("ic14", "icon_256x256@2x.png")]
body = b""
for kind, filename in entries:
    data = (iconset / filename).read_bytes()
    body += kind.encode("ascii") + struct.pack(">I", len(data) + 8) + data
(RESOURCES / "Pilot.icns").write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
for cache in RESOURCES.rglob("__pycache__"):
    shutil.rmtree(cache)
sign(APP, options.identity)
print(APP)
