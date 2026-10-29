#!/usr/bin/env python3
"""Export only audited public sources; never include personal state or build artifacts."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import zipfile
from public_source import ROOT, files

spec = importlib.util.spec_from_file_location('public_audit', ROOT / 'scripts/check-public.py')
audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=ROOT / 'dist/open-source/terminal-pilot')
args = parser.parse_args()
output = args.output.resolve()
if output == ROOT or ROOT.is_relative_to(output): raise SystemExit('Choose a separate export directory')
candidates, failures = audit.audit(ROOT)
if failures: raise SystemExit('Publication audit failed. Run scripts/check-public.py; no files exported.')
if output.exists(): raise SystemExit(f'Export destination already exists: {output}. Use a new directory.')
output.mkdir(parents=True)
manifest = {}
for path in candidates:
    relative = path.relative_to(ROOT)
    target = output / relative; target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    manifest[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
archive = output.with_suffix('.zip')
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
    for relative in manifest: z.write(output / relative, output.name + '/' + relative)
output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(f'Exported {len(manifest)} audited source files: {output}\nSource archive: {archive}')
