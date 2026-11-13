"""Permit an existing Terminal-owned Codex wrapper only with unchanged dependencies."""
import hashlib
from pathlib import Path
import struct
import signing_policy as policy


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unsigned_digest(path):
    # codesign --remove-signature leaves different __LINKEDIT allocation sizes.
    # For the shipped thin arm64 format, compare every non-signature byte and
    # normalize only the signature command and its linkedit extent. Unknown
    # layouts fail closed; this never modifies either bundle.
    data = bytearray(path.read_bytes())
    if len(data) < 32 or data[:4] != b'\xcf\xfa\xed\xfe':
        raise ValueError('Unsupported native runtime format')
    cpu, _, _, commands, command_size = struct.unpack_from('<IIIII', data, 4)
    if cpu != 0x0100000c or 32 + command_size > len(data):
        raise ValueError('Unsupported native runtime header')
    offset = 32; signature = linkedit = None
    for _ in range(commands):
        if offset + 8 > 32 + command_size: raise ValueError('Invalid load command')
        kind, size = struct.unpack_from('<II', data, offset)
        if size < 8 or offset + size > 32 + command_size: raise ValueError('Invalid load command size')
        if kind == 0x1d:
            if signature is not None or size != 16: raise ValueError('Invalid signature command')
            start, length = struct.unpack_from('<II', data, offset + 8)
            signature = (offset, start, length)
        if kind == 0x19 and data[offset + 8:offset + 24].rstrip(b'\0') == b'__LINKEDIT':
            if linkedit is not None or size < 72: raise ValueError('Invalid linkedit command')
            linkedit = offset
        offset += size
    if offset != 32 + command_size or signature is None or linkedit is None:
        raise ValueError('Missing runtime signature layout')
    command, start, length = signature
    vm_size, file_start, file_size = struct.unpack_from('<QQQ', data, linkedit + 32)
    if (start < offset or start + length != len(data) or file_start > start
            or file_start + file_size != len(data)
            or vm_size not in {((file_size + page - 1) // page) * page for page in (4096, 16384)}):
        raise ValueError('Unexpected runtime signature extent')
    struct.pack_into('<II', data, command + 8, 0, 0)
    struct.pack_into('<Q', data, linkedit + 32, 0)
    struct.pack_into('<Q', data, linkedit + 48, start - file_start)
    return hashlib.sha256(data[:start]).hexdigest()


def verify_dependencies(installed, candidate):
    old = installed / 'Contents/Resources'
    new = candidate / 'Contents/Resources'
    for name in ('bridge.py', 'codex_managed.py', 'runtime_paths.py'):
        if digest(old / name) != digest(new / name):
            raise ValueError('Running Terminal wrapper code changed: ' + name)
    def entries(root):
        return {p.relative_to(root): p for p in root.rglob('*') if p.is_file() or p.is_symlink()}
    before, after = entries(old / 'runtime'), entries(new / 'runtime')
    if not before or before.keys() != after.keys():
        raise ValueError('Running Terminal runtime file set changed')
    from bundle_runtime import macho_files
    native = set(macho_files(old / 'runtime'))
    compared = 0
    for name, a in before.items():
        b = after[name]
        if a.is_symlink() or b.is_symlink():
            if not (a.is_symlink() and b.is_symlink() and a.readlink() == b.readlink()):
                raise ValueError('Running Terminal runtime link changed: ' + str(name))
        elif digest(a) != digest(b):
            if a not in native or unsigned_digest(a) != unsigned_digest(b):
                raise ValueError('Running Terminal runtime bytes changed: ' + str(name))
            compared += 1
    return {'files': len(before), 'nativeSignatureOnlyDifferences': compared}


def terminal_ancestor(pid):
    visited = set()
    while pid > 1 and pid not in visited:
        visited.add(pid)
        parent = policy.command('/bin/ps', '-p', str(pid), '-o', 'ppid=').stdout.strip()
        if not parent.isdigit(): return False
        pid = int(parent)
        executable = policy.command('/bin/ps', '-p', str(pid), '-o', 'comm=').stdout.strip()
        if executable.endswith('/Terminal.app/Contents/MacOS/Terminal'):
            return True
    return False


def is_independent_codex_wrapper(pid, executable, installed):
    python = installed / 'Contents/Resources/runtime/python/bin'
    path = Path(executable)
    if path.parent != python or path.name not in ('python3', 'python3.13'):
        return False
    args = policy.command('/bin/ps', '-p', str(pid), '-o', 'args=').stdout.strip()
    # ps displays argv without shell escaping; match the exact known invocation.
    prefix = str(path) + ' -E -s -B ' + str(installed / 'Contents/Resources/bridge.py') + ' launch codex'
    if not (args == prefix or args.startswith(prefix + ' ')):
        return False
    return terminal_ancestor(pid)


def preflight_dependencies(installed, candidate):
    # Check compatibility while the manager is still available, before the
    # operator begins downtime. Installation checks again after it exits.
    preserved = []
    for line in policy.command('/bin/ps', '-axo', 'pid=,comm=').stdout.splitlines():
        row = line.strip().split(None, 1)
        if len(row) == 2 and Path(row[1]).is_relative_to(installed):
            if is_independent_codex_wrapper(int(row[0]), row[1], installed):
                preserved.append(int(row[0]))
    proof = verify_dependencies(installed, candidate) if preserved else {}
    return {'preservedTerminalWrappers': preserved, 'runtimeCompatibility': proof}
