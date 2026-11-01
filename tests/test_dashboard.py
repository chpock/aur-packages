import io
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import tempfile
import unittest


CHECK = Path(__file__).resolve().parents[1] / "automation/packages/magic-context-dashboard-bin/check.py"


class DashboardPackageTests(unittest.TestCase):
    def archive(self, root, executable=True, desktop=True):
        archive = root / "dashboard.pkg.tar.gz"
        files = {
            "usr/bin/magic-context-dashboard": (b"ELF fixture", 0o755 if executable else 0o644),
            "usr/share/licenses/magic-context-dashboard-bin/LICENSE": (b"MIT License\n", 0o644),
        }
        if desktop:
            files["usr/share/applications/Magic Context Dashboard.desktop"] = (
                b"[Desktop Entry]\nType=Application\nExec=magic-context-dashboard\n", 0o644)
        with tarfile.open(archive, "w:gz") as stream:
            for name, (data, mode) in files.items():
                entry = tarfile.TarInfo(name)
                entry.size, entry.mode = len(data), mode
                stream.addfile(entry, io.BytesIO(data))
        return archive

    def test_packaged_executable_desktop_and_license(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = self.archive(Path(temporary))
            result = subprocess.run([sys.executable, str(CHECK), str(archive)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_desktop_and_non_executable_binary_fail(self):
        for executable, desktop in ((False, True), (True, False)):
            with self.subTest(executable=executable, desktop=desktop), tempfile.TemporaryDirectory() as temporary:
                archive = self.archive(Path(temporary), executable, desktop)
                check = runpy.run_path(str(CHECK))["check"]
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    check(archive)

    def test_invalid_desktop_target_and_license_fail(self):
        check = runpy.run_path(str(CHECK))["check"]
        for target in ("desktop", "license"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = self.archive(root)
                content = root / "content"
                content.mkdir()
                with tarfile.open(archive) as stream:
                    stream.extractall(content, filter="data")
                if target == "desktop":
                    (content / "usr/share/applications/Magic Context Dashboard.desktop").write_text(
                        "[Desktop Entry]\nExec=unrelated-program\n")
                else:
                    (content / "usr/share/licenses/magic-context-dashboard-bin/LICENSE").write_text("changed license")
                with tarfile.open(archive, "w:gz") as stream:
                    stream.add(content / "usr", arcname="usr")
                with self.assertRaises(ValueError):
                    check(archive)


if __name__ == "__main__":
    unittest.main()
