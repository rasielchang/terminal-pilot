"""Explicit source distribution boundary, shared by audit and export."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP = {'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CONTRIBUTING.md', 'SECURITY.md',
       'CHANGELOG.md', 'VERIFICATION.md', 'WINDOWS.md', 'VOICE.md', 'requirements.txt', '.gitignore'}
DIRECTORIES = {'Sources', 'scripts', 'tests', 'web', 'docs', 'vendor', 'postman', 'packaging', '.github'}
EXCLUDED = {'__pycache__', '.DS_Store', '.env', 'runtime.json', 'web-auth.json', 'preferences.json', 'tools.json'}


def files(root=ROOT):
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if relative.parts[0] not in DIRECTORIES and relative.as_posix() not in TOP: continue
        if any(part in EXCLUDED or part.startswith('.env.') for part in relative.parts): continue
        if path.suffix in {'.pyc', '.log', '.sqlite3', '.sqlite', '.db', '.p12', '.p8', '.key'}: continue
        if path.is_symlink(): raise ValueError(f'Source distribution must not contain symlinks: {relative}')
        if path.is_file(): yield path
