#!/usr/bin/env python3
"""Validate the complete payload and run smoke tests before trial installation."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile


COMMANDS = ('repowise', 'repowise-augment', 'repowise-rewrite')


def check_contents(root):
    root = Path(root).resolve()
    for command in COMMANDS:
        launcher = root / 'usr/bin' / command
        target = root / 'usr/lib/repowise/bin' / command
        if not launcher.is_symlink() or launcher.resolve() != target:
            raise ValueError(f'Invalid system launcher: {command}')
        if not target.is_file() or not os.access(target, os.X_OK):
            raise ValueError(f'Missing executable: {command}')
        if target.read_bytes().splitlines()[0] != b'#!/usr/lib/repowise/bin/python':
            raise ValueError(f'Launcher does not use the private runtime: {command}')
    web = root / 'usr/lib/repowise/web'
    for required in ('server.js', '.next/BUILD_ID', 'node_modules/next/package.json'):
        if not (web / required).is_file():
            raise ValueError(f'Missing dashboard asset: {required}')
    if not any((web / '.next/static').rglob('*.*')):
        raise ValueError('Dashboard static assets are missing')
    license_file = root / 'usr/share/licenses/repowise/LICENSE'
    if not license_file.is_file() or 'GNU AFFERO GENERAL PUBLIC LICENSE' not in license_file.read_text():
        raise ValueError('Repowise license is missing')
    for name in ('next/license.md', 'react/LICENSE', 'sharp/LICENSE',
                 '@img/sharp-libvips-linux-x64/README.md'):
        notice = license_file.parent / 'web' / name
        if not notice.is_file() or not notice.stat().st_size:
            raise ValueError(f'Missing dashboard dependency notice: {name}')
    python = root / 'usr/lib/repowise/bin/python'
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError('Private runtime interpreter link is missing')
    if next((root / 'usr/lib/repowise/lib').rglob('*.pyc'), None) is not None:
        raise ValueError('Private runtime contains build-time bytecode')
    return python


def check(archive):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        subprocess.run(['bsdtar', '-xf', str(Path(archive).resolve()), '-C', str(root)], check=True)
        python = check_contents(root)
        home = root / 'home'
        home.mkdir()
        environment = dict(os.environ, HOME=str(home), XDG_CACHE_HOME=str(home / '.cache'),
                           PYTHONDONTWRITEBYTECODE='1', UV_PYTHON_DOWNLOADS='never')
        subprocess.run(['uv', 'pip', 'check', '--python', str(python)],
                       env=environment, cwd=home, check=True)
        subprocess.run([str(python), str(root / 'usr/lib/repowise/bin/repowise'), '--version'],
                       env=environment, cwd=home, check=True)
        subprocess.run([str(python), str(Path(__file__).with_name('smoke.py').resolve())],
                       env=environment, cwd=home, check=True)


if __name__ == '__main__':
    try:
        check(sys.argv[1])
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
