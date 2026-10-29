#!/usr/bin/env python3
"""Scan the exact public-source allowlist without printing matched secret values."""
import argparse
from pathlib import Path
import re
import os
import subprocess
import sys
from public_source import ROOT, files

PATTERNS = {
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'provider credential': re.compile(r'\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{32,}|gh[pousr]_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b'),
    'private tailnet address': re.compile(r'\b[a-z0-9-]+\.tail[a-z0-9]+\.ts\.net\b'),
}


def audit(root, *, staged=False):
    failures = []
    exported = set(files(root))
    if staged:
        git = os.environ.get('PILOT_GIT', 'git')
        result = subprocess.run([git, 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'],
                                cwd=root, capture_output=True, check=True)
        candidates = [root / name for name in result.stdout.decode('utf-8').split('\0') if name]
    else:
        # Scan new source directories too. The export allowlist must never hide findings.
        private_dirs = {'.git', '.runtime', '.local', '.venv', '.build', 'dist', 'work', '__pycache__'}
        candidates = [p for p in sorted(root.rglob('*')) if p.is_file()
                      and not any(part in private_dirs for part in p.relative_to(root).parts)]
    homes = {str(Path.home()) + '/', str(Path.home()) + '\\', Path.home().as_posix() + '/',
             str(Path.home()).replace('\\', '\\\\') + '\\\\'}
    for path in candidates:
        relative = path.relative_to(root)
        if path not in exported:
            failures.append((str(relative), 0, 'outside public source boundary or forbidden artifact'))
        if path.is_symlink():
            failures.append((str(relative), 0, 'symlink in publication')); continue
        data = (subprocess.check_output([git, 'show', ':' + relative.as_posix()], cwd=root)
                if staged else path.read_bytes())
        if path.suffix == '.png': continue  # Only repository-generated icons; no user screenshots.
        try: text = data.decode('utf-8')
        except UnicodeDecodeError:
            failures.append((str(relative), 0, 'unexpected binary')); continue
        for number, line in enumerate(text.splitlines(), 1):
            if any(home in line for home in homes): failures.append((str(relative), number, 'local home path'))
            for name, pattern in PATTERNS.items():
                if pattern.search(line): failures.append((str(relative), number, name))
        if path.suffix == '.md' and relative.parts[0] != 'vendor':
            for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
                if '://' in target or target.startswith(('#', 'mailto:')): continue
                target = target.split('#', 1)[0]
                if target and not (path.parent / target).exists():
                    failures.append((str(relative), 0, 'broken local documentation link'))
    for filename in ['LICENSE', 'README.md', 'SECURITY.md', 'THIRD_PARTY_NOTICES.md']:
        if not (root / filename).is_file(): failures.append((filename, 0, 'missing publication file'))
    return candidates, failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--staged', action='store_true', help='Audit all staged additions/modifications, including new directories')
    args = parser.parse_args()
    candidates, failures = audit(args.root.resolve(), staged=args.staged)
    for name, line, reason in failures: print(f'{name}:{line}: {reason}')
    print(f'Checked {len(candidates)} public files; {len(failures)} findings. Matched values are never printed.')
    sys.exit(bool(failures))
