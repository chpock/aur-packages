import os
import runpy
from types import SimpleNamespace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECK = SimpleNamespace(**runpy.run_path(str(ROOT / 'automation/packages/repowise/check.py')))
COLLECT = runpy.run_path(str(ROOT / 'repowise/collect-web-licenses.py'))['collect']


class RepowiseCompatibilityTests(unittest.TestCase):
    def test_failed_lock_inspection_blocks_dependency_installation(self):
        for content in ('[[package]\n', 'packages = []\n', '[[package]]\nname = "numpy"\n'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / 'uv.lock').write_text(content)
                marker = root / 'installation-attempted'
                script = '''
set -e
source "$1"
mkdir "${pkgname}-${pkgver}"
cp uv.lock "${pkgname}-${pkgver}/uv.lock"
srcdir=$PWD
uv() {
    if [[ "$1" == export ]]; then return 0; fi
    printf attempted > "$INSTALL_MARKER"
    return 99
}
build
'''
                result = subprocess.run(['bash', '-c', script, 'bash', str(ROOT / 'repowise/PKGBUILD')],
                                        cwd=root, env=dict(os.environ, INSTALL_MARKER=str(marker)),
                                        text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists(), result.stderr)

    def test_numpy_exception_is_removed_when_the_upstream_lock_changes(self):
        for versions, needed in ((['1.26.4'], True), (['2.5.4'], False),
                                 (['1.26.4', '2.5.4'], False), ([], False)):
            with self.subTest(versions=versions), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / 'uv.lock').write_text('package = []\n' if not versions else ''.join(
                    f'[[package]]\nname = "numpy"\nversion = "{version}"\n' for version in versions))
                result = subprocess.run(['bash', '-c', 'source "$1"; _needs_numpy_compat',
                                         'bash', str(ROOT / 'repowise/PKGBUILD')], cwd=root,
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), 'true' if needed else 'false')


class RepowiseWebLicenseTests(unittest.TestCase):
    def test_runtime_notices_and_readme_are_preserved_without_unshipped_nested_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / 'source/node_modules/example'
            runtime = root / 'web/node_modules/example'
            original.mkdir(parents=True)
            runtime.mkdir(parents=True)
            metadata = '{"name":"example","version":"1"}'
            (original / 'package.json').write_text(metadata)
            (runtime / 'package.json').write_text(metadata)
            for name in ('LICENSE', 'README.md', 'licenses/third-party.txt',
                         'index.js', 'node_modules/unshipped/LICENSE'):
                file = original / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(f'original {name}')
            COLLECT(root / 'source', root / 'web', root / 'notices')
            names = {str(path.relative_to(root / 'notices'))
                     for path in (root / 'notices').rglob('*') if path.is_file()}
            self.assertEqual(names, {'example/LICENSE', 'example/README.md', 'example/licenses/third-party.txt'})
            self.assertEqual((root / 'notices/example/LICENSE').read_text(), 'original LICENSE')
            (runtime / 'package.json').write_text('{"name":"example","version":"2"}')
            with self.assertRaises(ValueError):
                COLLECT(root / 'source', root / 'web', root / 'mismatch')


class RepowiseArchiveTests(unittest.TestCase):
    def payload(self, root):
        for command in CHECK.COMMANDS:
            target = root / 'usr/lib/repowise/bin' / command
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('#!/usr/lib/repowise/bin/python\n')
            target.chmod(0o755)
            launcher = root / 'usr/bin' / command
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.symlink_to(f'../lib/repowise/bin/{command}')
        (root / 'usr/lib/repowise/bin/python').symlink_to(sys.executable)
        web = root / 'usr/lib/repowise/web'
        for name in ('server.js', '.next/BUILD_ID', '.next/static/chunks/app.js',
                     'node_modules/next/package.json'):
            path = web / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
        license_file = root / 'usr/share/licenses/repowise/LICENSE'
        license_file.parent.mkdir(parents=True)
        license_file.write_text('GNU AFFERO GENERAL PUBLIC LICENSE\n')
        for name in ('next/license.md', 'react/LICENSE', 'sharp/LICENSE',
                     '@img/sharp-libvips-linux-x64/README.md'):
            notice = license_file.parent / 'web' / name
            notice.parent.mkdir(parents=True, exist_ok=True)
            notice.write_text('upstream copyright and license notice')

    def test_complete_payload_has_three_private_runtime_launchers_and_system_ui(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.payload(root)
            self.assertEqual(CHECK.check_contents(root), root / 'usr/lib/repowise/bin/python')

    def test_missing_web_dependency_notice_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.payload(root)
            (root / 'usr/share/licenses/repowise/web/next/license.md').unlink()
            with self.assertRaises(ValueError):
                CHECK.check_contents(root)

    def test_build_root_bytecode_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.payload(root)
            bytecode = root / 'usr/lib/repowise/lib/python3.14/site-packages/example/__pycache__/module.pyc'
            bytecode.parent.mkdir(parents=True)
            bytecode.write_bytes(b'bytecode containing a build path')
            with self.assertRaises(ValueError):
                CHECK.check_contents(root)

    def test_missing_or_unsafe_payload_is_rejected(self):
        mutations = ('launcher', 'executable', 'interpreter', 'dashboard', 'static', 'license')
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.payload(root)
                if mutation == 'launcher':
                    path = root / 'usr/bin/repowise'
                    path.unlink()
                    path.symlink_to('/usr/bin/python')
                elif mutation == 'executable':
                    (root / 'usr/lib/repowise/bin/repowise').chmod(0o644)
                elif mutation == 'interpreter':
                    (root / 'usr/lib/repowise/bin/repowise').write_text('#!/usr/bin/python\n')
                elif mutation == 'dashboard':
                    (root / 'usr/lib/repowise/web/server.js').unlink()
                elif mutation == 'static':
                    (root / 'usr/lib/repowise/web/.next/static/chunks/app.js').unlink()
                else:
                    (root / 'usr/share/licenses/repowise/LICENSE').write_text('unrelated')
                with self.assertRaises(ValueError):
                    CHECK.check_contents(root)


if __name__ == '__main__':
    unittest.main()
