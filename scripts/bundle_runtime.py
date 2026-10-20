"""Create a relocatable, hash-locked Python/Node runtime from official artifacts."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads(Path(__file__).with_name('runtime-lock.json').read_text())


def download(item, cache):
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / item['filename']
    if not target.exists():
        url = item.get('url')
        if not url:
            with urllib.request.urlopen(f"https://pypi.org/pypi/{item['package']}/{item['version']}/json", timeout=30) as response:
                metadata = json.load(response)
            asset = next(a for a in metadata['urls'] if a['filename'] == item['filename'])
            if asset['digests']['sha256'] != item['sha256']: raise ValueError('PyPI artifact hash changed')
            url = asset['url']
            if not url.startswith('https://files.pythonhosted.org/'): raise ValueError('Unexpected wheel origin')
        temporary = target.with_suffix(target.suffix + '.partial')
        try:
            with urllib.request.urlopen(url, timeout=90) as response, temporary.open('wb') as out:
                shutil.copyfileobj(response, out)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError(f"Checksum mismatch: {target.name}")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    if hashlib.sha256(target.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError(f"Checksum mismatch: {target.name}; remove the bad cached artifact and retry")
    return target


def extract_tar(archive, destination):
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter='data')


def bundle(contents, cache=ROOT / '.build/downloads'):
    # A standalone distribution is data plus individually signed executables,
    # not a macOS .framework bundle. Seal it under Resources as a complete unit.
    frameworks = contents / 'Resources/runtime'
    frameworks.mkdir(exist_ok=True)
    licenses = contents / 'Resources/ThirdPartyLicenses'
    licenses.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pilot-runtime-') as temp:
        temp = Path(temp)
        extract_tar(download(LOCK['python'], cache), temp)
        python = frameworks / 'python'
        if python.exists(): shutil.rmtree(python)
        shutil.move(str(temp / 'python'), python)
        # Keep the runtime's CPython license; full-build metadata supplies dependency notices.
        shutil.copy2(python / 'lib/python3.13/LICENSE.txt', licenses / 'Python-LICENSE.txt')
        full = download(LOCK['python_licenses'], cache)
        names = subprocess.check_output(['/usr/bin/tar', '-tf', str(full)], text=True).splitlines()
        notices = [n for n in names if n.startswith('python/licenses/') or n == 'python/PYTHON.json']
        if not notices: raise ValueError('Python distribution is missing license metadata')
        subprocess.run(['/usr/bin/tar', '-xf', str(full), '-C', str(temp), *notices], check=True)
        if (temp / 'python/licenses').exists(): shutil.copytree(temp / 'python/licenses', licenses / 'Python-dependencies', dirs_exist_ok=True)
        shutil.copy2(temp / 'python/PYTHON.json', licenses / 'Python-build.json')
        # No installers or development headers are needed on end-user machines.
        for path in [python / 'include', python / 'share', python / 'lib/python3.13/ensurepip']:
            if path.exists(): shutil.rmtree(path)
        # SwiftUI owns the UI; Tcl/Tk is unused and its versioned directories are
        # not macOS code bundles. Do not ship optional X11/Tk libraries.
        for pattern in ['lib/tcl*', 'lib/tk*', 'lib/itcl*', 'lib/thread*', 'lib/libtcl*',
                        'lib/libtk*', 'lib/python3.13/tkinter', 'lib/python3.13/idlelib',
                        'lib/python3.13/lib-dynload/_tkinter*']:
            for path in python.glob(pattern):
                if path.is_dir() and not path.is_symlink(): shutil.rmtree(path)
                else: path.unlink()
        site = python / 'lib/python3.13/site-packages'
        for path in site.glob('pip*'):
            if path.is_dir(): shutil.rmtree(path)
        for path in (python / 'bin').iterdir():
            if not path.name.startswith('python'): path.unlink()
        for item in LOCK['wheels']:
            with zipfile.ZipFile(download(item, cache)) as wheel:
                for member in wheel.namelist():
                    if Path(member).is_absolute() or '..' in Path(member).parts: raise ValueError('Unsafe wheel member')
                wheel.extractall(site)
        extract_tar(download(LOCK['node'], cache), temp)
        node_source = temp / 'node-v22.23.2-darwin-arm64'
        node = frameworks / 'node/bin'
        node.mkdir(parents=True, exist_ok=True)
        shutil.copy2(node_source / 'bin/node', node / 'node')
        shutil.copy2(node_source / 'LICENSE', licenses / 'Node-LICENSE.txt')
    for path in frameworks.rglob('__pycache__'): shutil.rmtree(path)
    for path in frameworks.rglob('*.pyc'): path.unlink()
    shutil.copy2(Path(__file__).with_name('runtime-lock.json'), licenses / 'runtime-lock.json')
    env = {**os.environ, 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('PYTHONPATH', None); env.pop('PYTHONHOME', None)
    subprocess.run([str(frameworks / 'python/bin/python3'), '-I', '-B', '-c',
                    'import ssl,sqlite3,ctypes,cryptography,cffi; from cryptography.hazmat.primitives.ciphers.aead import AESGCM; AESGCM.generate_key(bit_length=256)'],
                   env=env, check=True)


def macho_files(root):
    magic = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
    for path in root.rglob('*'):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:
                if stream.read(4) in magic: yield path


def sign(app, identity='-'):
    for path in sorted(macho_files(app), key=lambda p: len(p.parts), reverse=True):
        if path == app / 'Contents/MacOS/TerminalPilot': continue  # Signed with the outer bundle.
        args = ['codesign', '--force', '--sign', identity]
        if identity != '-':
            args += ['--options', 'runtime', '--timestamp']
            if path.name == 'node': args += ['--entitlements', str(ROOT / 'packaging/node.entitlements.plist')]
        subprocess.run([*args, str(path)], check=True, stdout=subprocess.DEVNULL)
    args = ['codesign', '--force', '--sign', identity]
    if identity != '-': args += ['--options', 'runtime', '--timestamp', '--entitlements', str(ROOT / 'packaging/app.entitlements.plist')]
    subprocess.run([*args, str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
