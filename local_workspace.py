"""Windows source/runtime separation. Standard library only; never starts trading.

Source lives in OneDrive. Everything executed by `run` uses a local working copy.
Archives remain local and are explicit snapshots, not a synchronization loop.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

SOURCE = Path(__file__).resolve().parent
if str(SOURCE) == os.environ.get('TRADING_WORKSPACE_ACTIVE') and os.environ.get('TRADING_WORKSPACE_SOURCE'):
    SOURCE = Path(os.environ['TRADING_WORKSPACE_SOURCE']).resolve()
CHUNK = 32 * 1024 * 1024
SKIP_DIRS = {'.git', '.portable-history', '__pycache__', '.pytest_cache',
             '.mypy_cache', '.ruff_cache', 'node_modules', '.local-workspace'}
SECRET_NAMES = {'CLAUDE.local.md', 'secrets.toml', '.stock_secrets'}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.new')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def contained(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f'Unsafe child path: {path}')
    return path


def settings(source=SOURCE, base=None):
    source = Path(source).resolve()
    config = json.loads((source / 'local-workspace.json').read_text(encoding='utf-8'))
    base = Path(base or os.environ.get('TRADING_WORKSPACE_HOME') or
                Path.home() / 'AppData' / 'Local' / 'Programs' / 'TradingWorkspace').resolve()
    # A user override must not silently place runtime back in a synced tree.
    forbidden = [source, *[Path(v).resolve() for k, v in os.environ.items()
                           if k.lower().startswith('onedrive') and v]]
    if any(base == p or base.is_relative_to(p) for p in forbidden) or any(
            'onedrive' in part.lower() for part in base.parts):
        raise ValueError('Runtime location must be outside OneDrive/source')
    runtime = contained(base / 'workspaces' / config['workspace_name'], base)
    state = contained(base / 'state' / config['id'], base)
    state.mkdir(parents=True, exist_ok=True)
    return source, base, runtime, state, config


@contextmanager
def workspace_lock():
    """Serialize initialization/snapshot writes; the OS releases crashed locks."""
    state = settings()[3]
    with (state / 'operation.lock').open('a+b') as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('Another initialization/snapshot is running; retry after it finishes.') from exc
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def is_secret(relative):
    p = Path(relative)
    return (p.name in SECRET_NAMES or p.name == '.env' or p.name.startswith('.env.')
            and not p.name.endswith(('example', 'template'))
            or p.suffix.lower() in {'.pfx', '.p12', '.pem', '.key'}
            or 'credentials' in p.name.lower() and p.suffix == '.json'
            or any(x in {'.secrets', '.streamlit', 'private'} for x in p.parts))


def is_seed(relative):
    p = Path(relative)
    return relative.startswith('data/') or (
        ('research' in relative or relative.startswith(('tradingview_', 'strategy_experiment')))
        and p.suffix.lower() in {'.json', '.jsonl', '.csv', '.npz', '.parquet', '.gz', '.db', '.sqlite3', '.png', '.jpg'})


def walk_files(root):
    root = Path(root)
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [n for n in dirs if n not in SKIP_DIRS and not n.startswith('.venv')
                   and n != 'venv' and not (Path(directory) / n).is_symlink()
                   and not (hasattr(os.path, 'isjunction') and os.path.isjunction(Path(directory) / n))]
        for name in files:
            path = Path(directory) / name
            if name == '.git' or path.is_symlink() or path.suffix in {'.pyc', '.pyo'}:
                continue
            yield path


def local_environment(base, runtime, config):
    env = os.environ.copy()
    temp_root = Path(os.environ.get('TRADING_WORKSPACE_TEMP') or Path(tempfile.gettempdir()) / 'TradingWorkspace').resolve()
    if any('onedrive' in part.lower() for part in temp_root.parts):
        raise ValueError('Temporary storage must be outside OneDrive')
    cache = temp_root / config['id']
    paths = {'TEMP': cache / 'tmp', 'TMP': cache / 'tmp',
             'PYTHONPYCACHEPREFIX': cache / 'pycache', 'PIP_CACHE_DIR': cache / 'pip',
             'MPLCONFIGDIR': cache / 'matplotlib', 'XDG_CACHE_HOME': cache / 'xdg',
             'PLAYWRIGHT_BROWSERS_PATH': base / 'browsers',
             'UV_CACHE_DIR': cache / 'uv', 'npm_config_cache': cache / 'npm'}
    for key, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env['PYTEST_ADDOPTS'] = '-o "cache_dir=' + (cache / 'pytest').as_posix() + '"'
    env['TRADING_WORKSPACE_ACTIVE'] = str(runtime)
    env['TRADING_WORKSPACE_HOME'] = str(base)
    modules = [runtime, *[runtime / p for p in config.get('python_paths', [])]]
    # Never inherit source-side import paths into runtime workers.
    env['PYTHONPATH'] = os.pathsep.join(map(str, modules))
    scripts = runtime / '.venv' / ('Scripts' if os.name == 'nt' else 'bin')
    env['PATH'] = str(scripts) + os.pathsep + env.get('PATH', '')
    return env


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], stderr=subprocess.PIPE)


def detach_git(source, base, state, config):
    pointer = source / '.git'
    target = contained(base / 'git' / (config['id'] + '.git'), base)
    target.parent.mkdir(parents=True, exist_ok=True)
    if pointer.is_dir():
        if target.exists():
            raise ValueError('Git target exists; refusing to overwrite metadata')
        if list(pointer.rglob('*.lock')):
            raise ValueError('Git lock present; finish the other Git operation first')
        # Same-volume rename preserves objects, reflogs, index and config exactly.
        # Both absolute endpoints are checked before this directory move.
        contained(pointer, source)
        if os.name == 'nt': os.chmod(pointer, 0o700)  # OneDrive may mark directories read-only.
        try:
            os.rename(pointer, target)
        except PermissionError:
            # Do not weaken an ACL automatically. Runtime separation can work
            # while the owner decides whether this protected metadata may move.
            print('Git metadata remains in source: Windows permissions/attributes blocked relocation.', file=sys.stderr)
            return pointer
        pointer.write_text('gitdir: ' + target.as_posix() + '\n', encoding='utf-8')
    elif not target.exists():
        archive_root = base / 'history' / config['id']
        history = archive_root / '.portable-history' / 'git-latest.json'
        if not history.exists():
            raise ValueError('Copy local history/<project> from the previous computer before initializing this Git pointer. Git history is not stored on OneDrive.')
        info = json.loads(history.read_text(encoding='utf-8'))
        bundle = reassemble(archive_root, state, info)
        subprocess.run(['git', 'clone', '--bare', str(bundle), str(target)], check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(['git', '--git-dir', str(target), 'symbolic-ref', 'HEAD', info['head_ref']], check=True)
        subprocess.run(['git', '--git-dir', str(target), 'config', '--remove-section', 'remote.origin'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if config.get('git_remote'):
            subprocess.run(['git', '--git-dir', str(target), 'remote', 'add', 'origin', config['git_remote']], check=True)
        bundle.unlink()  # Verified chunks remain the recovery source.
    expected = 'gitdir: ' + target.as_posix() + '\n'
    if not pointer.exists() or pointer.read_text(encoding='utf-8') != expected:
        pointer.write_text(expected, encoding='utf-8')
    subprocess.run(['git', '--git-dir', str(target), 'config', 'core.bare', 'false'], check=True)
    subprocess.run(['git', '--git-dir', str(target), 'config', 'core.worktree', str(source)], check=True)
    # Rebuild only a missing index; never reset an existing staging area.
    if not (target / 'index').exists():
        git(source, 'read-tree', 'HEAD')
    return target


def save_chunks(source, archive, label):
    directory = source / '.portable-history' / label
    directory.mkdir(parents=True, exist_ok=False)
    chunks = []
    with Path(archive).open('rb') as f:
        for index, block in enumerate(iter(lambda: f.read(CHUNK), b'')):
            part = directory / f'{index:04d}.part'
            part.write_bytes(block)
            chunks.append({'path': part.relative_to(source).as_posix(),
                           'size': len(block), 'sha256': digest(part)})
    return {'chunks': chunks, 'sha256': digest(archive), 'size': Path(archive).stat().st_size}


def reassemble(source, state, info):
    target = state / ('restore-' + info['sha256'] + '.archive')
    if target.exists() and digest(target) == info['sha256']:
        return target
    temp = target.with_suffix('.partial')
    with temp.open('wb') as output:
        for part in info['chunks']:
            path = contained(source / part['path'], source / '.portable-history')
            if path.stat().st_size != part['size'] or digest(path) != part['sha256']:
                raise ValueError(f'Corrupt/incomplete archive: {path.name}')
            with path.open('rb') as f:
                shutil.copyfileobj(f, output)
    if digest(temp) != info['sha256']:
        raise ValueError('Archive checksum mismatch')
    os.replace(temp, target)
    return target


def restore_history(source, runtime, state):
    index = source / '.portable-history' / 'data-latest.json'
    if not index.exists():
        return 0
    info = json.loads(index.read_text(encoding='utf-8'))
    receipt = state / ('restored-' + info['sha256'] + '.json')
    if receipt.exists() and (runtime / '.local-workspace').exists():
        return 0
    archive = reassemble(source, state, info)
    count = 0
    with zipfile.ZipFile(archive) as z:
        manifest = json.loads(z.read('_archive_manifest.json'))
        for record in manifest:
            rel = record['path']
            path = contained(runtime / rel, runtime)
            # Existing/newer local history always wins. Restoration is seed-only.
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + '.restore')
            with z.open(rel) as src, temp.open('wb') as dst:
                shutil.copyfileobj(src, dst)
            if digest(temp) != record['sha256']:
                raise ValueError(f'Restored file checksum mismatch: {rel}')
            os.replace(temp, path)
            count += 1
    write_json(receipt, {'restored_files': count})
    (runtime / '.local-workspace').mkdir(exist_ok=True)
    archive.unlink()  # Do not retain a duplicate assembled archive.
    return count


def sync_source(source, runtime, state):
    index_path = state / 'source-files.json'
    old = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
    new = {}
    conflicts = []
    copied = 0
    for path in walk_files(source):
        rel = path.relative_to(source).as_posix()
        if is_secret(rel) or rel == 'local_workspace.py' and source == runtime:
            continue
        target = contained(runtime / rel, runtime)
        stamp = digest(path)
        # STOCK/data in source is a low-frequency reference seed, not a live DB.
        if is_seed(rel) and target.exists():
            new[rel] = old.get(rel, stamp)
            continue
        if target.exists():
            local_hash = digest(target)
            if local_hash == stamp:
                new[rel] = stamp
                continue
            if rel in old and local_hash != old[rel]:
                conflicts.append(rel)
                continue
            if rel not in old:
                # Unknown local file may be valuable; retain before updating code.
                backup = contained(state / 'conflicts' / (stamp[:12] + '/' + rel), state)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        new[rel] = stamp
        copied += 1
    if conflicts:
        # Preserve successful updates in the receipt so a retry cannot mistake
        # our own copied code for a user's local edit.
        write_json(index_path, {**old, **new})
        raise ValueError('Local source edits conflict; copy them to OneDrive source first: ' + ', '.join(conflicts[:12]))
    # Only remove unchanged files previously owned by this source mirror.
    # Generated/runtime data is never mirrored away.
    for rel, stamp in old.items():
        if rel not in new and not is_seed(rel):
            target = contained(runtime / rel, runtime)
            if target.exists() and digest(target) == stamp:
                backup = contained(state / 'retired-source' / rel, state)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
                target.unlink()
    write_json(index_path, new)
    return copied


def prepare(source=SOURCE, base=None, install=False):
    source, base, runtime, state, config = settings(source, base)
    runtime.mkdir(parents=True, exist_ok=True)
    git_location = detach_git(source, base, state, config)
    restored = restore_history(base / 'history' / config['id'], runtime, state)
    copied = sync_source(source, runtime, state)
    env = local_environment(base, runtime, config)
    env['TRADING_WORKSPACE_SOURCE'] = str(source)
    venv = runtime / '.venv'
    python = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    # A venv alone is not readiness; retry incomplete dependency installation.
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True, env=env)
    requirements = runtime / config['requirements']
    stamp = digest(requirements)
    receipt = state / 'dependencies.json'
    previous = json.loads(receipt.read_text()) if receipt.exists() else {}
    if previous.get('sha256') != stamp:
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True,
                       cwd=runtime, env=env, stdout=sys.stderr)
        write_json(receipt, {'sha256': stamp, 'python': str(python)})
    write_json(state / 'location.json', {'source': str(source), 'runtime': str(runtime), 'git': str(git_location)})
    return {'source': str(source), 'runtime': str(runtime), 'copied': copied, 'restored': restored}, env, python


def snapshot(source=SOURCE, base=None):
    source, base, runtime, state, config = settings(source, base)
    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1000000)
    archive_root = base / 'history' / config['id']
    # Explicit snapshot: never done per request, per startup, or by a watcher.
    archive = state / ('history-' + stamp + '.zip')
    manifest = []
    source_files = set(json.loads((state / 'source-files.json').read_text(encoding='utf-8')))
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for path in walk_files(runtime):
            rel = path.relative_to(runtime).as_posix()
            if is_secret(rel) or rel.startswith(('tmp/', '.codex/', '.claude/')) or path.suffix == '.log':
                continue
            if rel in source_files and not is_seed(rel):
                continue
            before = digest(path)
            z.write(path, rel)
            if digest(path) != before:
                raise ValueError('History changed while snapshotting; stop writers first: '+rel)
            manifest.append({'path': rel, 'size': path.stat().st_size, 'sha256': before})
        z.writestr('_archive_manifest.json', json.dumps(manifest, ensure_ascii=False))
    # Verify the archive payload before publishing its completion index.
    with zipfile.ZipFile(archive) as z:
        for record in manifest:
            h = hashlib.sha256()
            with z.open(record['path']) as f:
                for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
            if h.hexdigest() != record['sha256']: raise ValueError('Archive verification failed')
    info = save_chunks(archive_root, archive, 'data-' + stamp)
    info['file_count'] = len(manifest)
    write_json(archive_root / '.portable-history' / 'data-latest.json', info)
    history_bytes = archive.stat().st_size
    archive.unlink()
    bundle = state / ('git-' + stamp + '.bundle')
    git(source, 'bundle', 'create', str(bundle), '--all')
    git(source, 'bundle', 'verify', str(bundle))
    info_git = save_chunks(archive_root, bundle, 'git-' + stamp)
    info_git['head_ref'] = git(source, 'symbolic-ref', 'HEAD').decode().strip()
    write_json(archive_root / '.portable-history' / 'git-latest.json', info_git)
    git_bytes = bundle.stat().st_size
    bundle.unlink()
    return {'history_files': len(manifest), 'history_bytes': history_bytes,
            'git_bytes': git_bytes, 'snapshot': stamp}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'status', 'snapshot'])
    parser.add_argument('--install', action='store_true')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'status':
        source, base, runtime, state, config = settings()
        print(json.dumps({'source': str(source), 'runtime': str(runtime), 'exists': runtime.exists()}, ensure_ascii=True))
        return
    if args.action == 'snapshot':
        with workspace_lock():
            print(json.dumps(snapshot(), ensure_ascii=True))
        return
    with workspace_lock():
        result, env, python = prepare(install=args.install or args.action == 'prepare')
    if args.action == 'prepare':
        print(json.dumps(result, ensure_ascii=True))
        return
    command = args.command
    if command and command[0] == '--': command = command[1:]
    if not command: command = ['powershell.exe', '-NoExit']
    if command[0] in {'python', 'python.exe'}: command[0] = str(python)
    command = [arg.replace(str(SOURCE), result['runtime']).replace(SOURCE.as_posix(), result['runtime']) for arg in command]
    if command[0].lower().endswith(('python', 'python.exe')):
        os.chdir(result['runtime'])
    subprocess.run(command, cwd=result['runtime'], env=env, check=True)


if __name__ == '__main__':
    main()
