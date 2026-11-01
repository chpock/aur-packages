from contextlib import chdir
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from automation import package


ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *map(str, args)],
                                   text=True, stderr=subprocess.PIPE).strip()


class VersionTests(unittest.TestCase):
    def test_update_preserves_recipe_and_epoch(self):
        original = "pkgver='1.0'\npkgrel=7\nepoch=2\nsource=(\"v${pkgver}\")\n"
        self.assertEqual(package.bump_version(original, "1.10"),
                         "pkgver=1.10\npkgrel=1\nepoch=2\nsource=(\"v${pkgver}\")\n")

    def test_unsafe_versions_and_derived_assignments_are_rejected(self):
        for version in ("", "../bad", "1-rc1", "1;touch /tmp/unsafe", "$(id)", "1\n2", None):
            with self.subTest(version=version), self.assertRaises(ValueError):
                package.bump_version("pkgver=1\npkgrel=1\n", version)
        for recipe in ("pkgver=${_version}\npkgrel=1\n", "pkgver=1\npkgver=2\npkgrel=1\n"):
            with self.subTest(recipe=recipe), self.assertRaises(ValueError):
                package.bump_version(recipe, "2")

    def test_arch_version_order_and_downgrade(self):
        self.assertTrue(package.is_upgrade("1.9", "1.10"))
        self.assertFalse(package.is_upgrade("1.10", "1.10"))
        with self.assertRaises(ValueError):
            package.is_upgrade("2.0", "1.10")

    def test_nvchecker_result_must_be_unambiguous_and_successful(self):
        event = {"name": "demo", "event": "updated", "version": "1.2"}
        self.assertEqual(package.checked_version(json.dumps(event), "demo"), "1.2")
        for events in ([], [event, event], [dict(event, name="wrong")],
                       [event, {"level": "error", "event": "no-result"}]):
            with self.subTest(events=events), self.assertRaises(ValueError):
                package.checked_version("\n".join(map(json.dumps, events)), "demo")

    def test_srcinfo_identity_and_pkgbase_path(self):
        self.assertEqual(package.srcinfo_version("pkgbase = demo\npkgver = 1\n", "demo"), "1")
        for metadata in ("pkgbase = wrong\npkgver = 1\n", "pkgbase = demo\n",
                         "pkgbase = demo\npkgver = 1\npkgver = 2\n"):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                package.srcinfo_version(metadata, "demo")
        for name in ("../demo", ".git", "demo/other", "-demo", "demo\nother", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                package.check_pkgbase(name)


class PackageFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.aur_root = self.root / "aur"
        self.aur_root.mkdir()
        self.aur = self.aur_root / "demo.git"
        self.upstream = self.root / "upstream"
        self.upstream.mkdir()
        git(self.upstream, "init", "-b", "master")
        self.identity(self.upstream)
        (self.upstream / "PKGBUILD").write_text(
            "# Maintainer: Test Maintainer <maintainer@example.invalid>\n"
            "pkgname=demo\npkgver=1.0\npkgrel=4\npkgdesc='Test package'\n"
            "url='https://example.org/demo'\narch=('any')\nlicense=('GPL-3.0-or-later')\n"
            "source=('payload')\nsha256sums=('SKIP')\n"
            "package() { install -Dm644 \"$srcdir/payload\" \"$pkgdir/usr/share/demo/payload\"; }\n"
        )
        (self.upstream / "payload").write_text("payload\n")
        (self.upstream / "obsolete").write_text("old helper\n")
        metadata = subprocess.check_output(["makepkg", "--printsrcinfo"], cwd=self.upstream, text=True)
        (self.upstream / ".SRCINFO").write_text(metadata)
        git(self.upstream, "add", ".")
        git(self.upstream, "commit", "-m", "initial AUR recipe")
        git(self.root, "clone", "--bare", self.upstream, self.aur)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        self.identity(self.repo)
        (self.repo / "README").write_text("canonical repository\n")
        config = self.repo / "automation/packages/demo"
        config.mkdir(parents=True)
        (config / "nvchecker.toml").write_text('[demo]\nsource = "cmd"\ncmd = "printf 1.1"\n')
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "initialize repository")
        git(self.repo, "subtree", "add", "--prefix=demo", self.aur, "master", "-m", "import AUR")
        self.origin = self.root / "origin.git"
        git(self.root, "clone", "--bare", self.repo, self.origin)
        git(self.repo, "remote", "add", "origin", self.origin)
        self.build = self.root / "checkout"
        git(self.root, "clone", self.origin, self.build)
        self.work = self.root / "build"
        self.recipe = self.work / "recipe"
        self.archive = self.work / "recipe.tar"

    def identity(self, repo):
        git(repo, "config", "user.name", "Test Maintainer")
        git(repo, "config", "user.email", "maintainer@example.invalid")
        git(repo, "config", "core.hooksPath", "/dev/null")

    def prepare(self):
        if self.work.exists():
            shutil.rmtree(self.work)
        return package.prepare(self.build, "demo", self.work, str(self.aur))

    def pack_recipe(self):
        with tarfile.open(self.archive, "w") as archive:
            archive.add(self.recipe, arcname="recipe")

    def publish(self):
        self.pack_recipe()
        return package.publish(self.repo, "demo", self.archive, "main", str(self.aur_root))


class PublicationTests(PackageFixture):
    def test_preparation_changes_only_the_build_directory(self):
        original = (self.build / "demo/PKGBUILD").read_bytes()
        self.assertTrue(self.prepare())
        self.assertEqual((self.build / "demo/PKGBUILD").read_bytes(), original)
        self.assertEqual(git(self.build, "status", "--porcelain"), "")
        self.assertIn("pkgver=1.1", (self.recipe / "PKGBUILD").read_text())
        self.assertIn("pkgver = 1.1", (self.recipe / ".SRCINFO").read_text())
        self.assertFalse((self.recipe / "src").exists())
        self.assertFalse((self.recipe / "pkg").exists())

    def test_publishes_ready_files_and_preserves_aur_ancestry(self):
        before = git(self.aur, "rev-parse", "master")
        self.prepare()
        self.publish()
        self.assertIn("pkgver=1.1", git(self.aur, "show", "master:PKGBUILD"))
        self.assertEqual(git(self.aur, "merge-base", "master", before), before)
        self.assertEqual(git(self.origin, "rev-parse", "main:demo"),
                         git(self.aur, "rev-parse", "master^{tree}"))
        self.assertEqual(git(self.aur, "log", "-1", "--format=%an <%ae>"),
                         "Kostiantyn Kushnir <chpock@gmail.com>")

    def test_failed_aur_push_retries_without_new_release_or_duplicate_commit(self):
        self.prepare()
        hook = self.aur / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        canonical = git(self.origin, "rev-parse", "main")
        hook.unlink()
        git(self.build, "fetch", "origin")
        git(self.build, "reset", "--hard", "origin/main")
        self.assertTrue(self.prepare())
        self.publish()
        self.assertEqual(git(self.origin, "rev-parse", "main"), canonical)
        self.assertFalse(self.prepare())

    def test_aur_already_at_new_version_does_not_hide_pending_github_update(self):
        self.prepare()
        for name in ("PKGBUILD", ".SRCINFO"):
            (self.upstream / name).write_bytes((self.recipe / name).read_bytes())
        git(self.upstream, "add", ".")
        git(self.upstream, "commit", "-m", "co-maintainer updates version")
        git(self.upstream, "push", self.aur, "master")
        self.assertTrue(self.prepare())

    def test_external_aur_commit_is_not_overwritten(self):
        self.prepare()
        (self.upstream / "maintainer-note").write_text("external change\n")
        git(self.upstream, "add", ".")
        git(self.upstream, "commit", "-m", "co-maintainer change")
        git(self.upstream, "push", self.aur, "master")
        external = git(self.aur, "rev-parse", "master")
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertEqual(git(self.aur, "rev-parse", "master"), external)

    def test_other_package_changes_are_preserved(self):
        self.prepare()
        (self.build / "other").mkdir()
        (self.build / "other/PKGBUILD").write_text("other package\n")
        self.identity(self.build)
        git(self.build, "add", "other")
        git(self.build, "commit", "-m", "update another package")
        git(self.build, "push", "origin", "HEAD:main")
        self.publish()
        self.assertEqual(git(self.origin, "show", "main:other/PKGBUILD"), "other package")

    def test_changed_package_or_host_key_stops_stale_publication(self):
        self.prepare()
        for path in ("demo/payload", "automation/aur_known_hosts"):
            with self.subTest(path=path):
                (self.build / path).write_text("manual change\n")
                self.identity(self.build)
                git(self.build, "add", path)
                git(self.build, "commit", "-m", "manual change")
                git(self.build, "push", "origin", "HEAD:main")
                with self.assertRaises(ValueError):
                    self.publish()
                git(self.build, "reset", "--hard", "HEAD~1")
                git(self.build, "push", "--force", "origin", "HEAD:main")

    def test_only_recipe_is_copied_and_modes_additions_deletions_are_preserved(self):
        self.prepare()
        (self.recipe / "obsolete").unlink()
        helper = self.recipe / "helper"
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)
        (self.recipe / "helper-link").symlink_to("helper")
        self.pack_recipe()
        with tarfile.open(self.archive, "a") as archive:
            member = tarfile.TarInfo("README")
            member.size = 8
            archive.addfile(member, io.BytesIO(b"unwanted"))
        package.publish(self.repo, "demo", self.archive, "main", str(self.aur_root))
        self.assertEqual(git(self.origin, "show", "main:README"), "canonical repository")
        listing = git(self.origin, "ls-tree", "-r", "main", "demo")
        self.assertIn("100755 blob", listing)
        self.assertIn("120000 blob", listing)
        self.assertNotIn("demo/obsolete", listing)

    def test_archive_traversal_is_rejected_before_either_remote_changes(self):
        self.prepare()
        canonical = git(self.origin, "rev-parse", "main")
        aur = git(self.aur, "rev-parse", "master")
        with tarfile.open(self.archive, "w") as archive:
            member = tarfile.TarInfo("../README")
            member.size = 8
            archive.addfile(member, io.BytesIO(b"unwanted"))
        with self.assertRaises(tarfile.FilterError):
            package.publish(self.repo, "demo", self.archive, "main", str(self.aur_root))
        self.assertEqual(git(self.origin, "rev-parse", "main"), canonical)
        self.assertEqual(git(self.aur, "rev-parse", "master"), aur)

    def test_build_directory_must_be_fresh_and_outside_the_package(self):
        with self.assertRaises(ValueError):
            package.prepare(self.build, "demo", self.build / "demo/build", str(self.aur))
        self.work.mkdir()
        marker = self.work / "keep"
        marker.write_text("existing data\n")
        with self.assertRaises(FileExistsError):
            package.prepare(self.build, "demo", self.work, str(self.aur))
        self.assertEqual(marker.read_text(), "existing data\n")
        self.assertEqual(git(self.build, "status", "--porcelain"), "")

    def test_nested_build_directory_and_recipe_symlinks(self):
        (self.build / "demo/payload-link").symlink_to("payload")
        directory = self.root / "nested/build"
        self.assertTrue(package.prepare(self.build, "demo", directory, str(self.aur)))
        link = directory / "recipe/payload-link"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.readlink(), Path("payload"))

    def test_unreadable_updated_file_is_not_treated_as_a_normal_difference(self):
        updater = self.build / "automation/packages/demo/update"
        updater.write_text(
            '#!/bin/sh\nsed -i "s/pkgver=1.0/pkgver=$2/" "$1/PKGBUILD"\nchmod 000 "$1/obsolete"\n')
        updater.chmod(0o755)
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertIn("pkgver=1.0", (self.build / "demo/PKGBUILD").read_text())

    def test_successful_updater_with_wrong_version_is_rejected(self):
        updater = self.build / "automation/packages/demo/update"
        updater.write_text("#!/bin/sh\nexit 0\n")
        updater.chmod(0o755)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertIn("pkgver=1.0", (self.build / "demo/PKGBUILD").read_text())

    def test_custom_updater_failure_does_not_change_checkout(self):
        updater = self.build / "automation/packages/demo/update"
        updater.write_text("#!/bin/sh\nprintf '%s\\n' \"$2\" > \"$1/updater-ran\"\nexit 1\n")
        updater.chmod(0o755)
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertEqual((self.recipe / "updater-ran").read_text(), "1.1\n")
        self.assertFalse((self.build / "demo/updater-ran").exists())
        self.assertIn("pkgver=1.0", (self.build / "demo/PKGBUILD").read_text())
        self.assertFalse(self.archive.exists())

    def test_cli_prepares_and_publishes_with_github_outputs(self):
        output = self.root / "github-output"
        with chdir(self.build), patch.dict(os.environ, {"GITHUB_OUTPUT": str(output), "GITHUB_TOKEN": "test-read-only"}), \
                patch.object(sys, "argv", ["package.py", "prepare", "demo", "--workdir", str(self.work)]):
            package.main()
        self.assertEqual(output.read_text(), "needs_publish=true\n")
        self.pack_recipe()
        with chdir(self.repo), patch.object(sys, "argv", ["package.py", "publish", "demo", "--archive", str(self.archive),
                                                          "--aur-url", str(self.aur_root)]):
            package.main()
        self.assertIn("pkgver=1.1", git(self.aur, "show", "master:PKGBUILD"))


class ValidationTests(PackageFixture):
    def setUp(self):
        super().setUp()
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.log = self.root / "installation.log"
        # Unit tests must never install fixture packages on the developer's host.
        for name in ("sudo", "pacman"):
            executable = self.tools / name
            executable.write_text(
                "#!/bin/bash\nset -eu\n"
                'if [[ "$(basename "$0")" == sudo ]]; then\n'
                '  [[ "$1" == pacman && "$2" == -U && -f "${@: -1}" ]]\n'
                '  printf "install\\n" >> "$INSTALL_LOG"\n'
                '  exit "${INSTALL_STATUS:-0}"\n'
                'elif [[ "${1:-}" == -Qkk ]]; then\n'
                '  [[ $# == 2 && "$2" == demo ]]\n'
                '  printf "check\\n" >> "$INSTALL_LOG"\n'
                '  exit "${CHECK_STATUS:-0}"\n'
                'fi\nexec /usr/bin/pacman "$@"\n')
            executable.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.tools}:{os.environ['PATH']}", INSTALL_LOG=str(self.log))
        self.env.pop("PKGDEST", None)
        self.env.pop("SRCDEST", None)
        self.env.pop("BUILDDIR", None)

    def validate(self, **environment):
        return subprocess.run(["bash", str(ROOT / "automation/validate.sh"), "demo", str(self.work)],
                              cwd=self.build, text=True, capture_output=True, env=dict(self.env, **environment))

    def test_regular_build_checks_ready_files_and_keeps_build_outputs_separate(self):
        self.prepare()
        checker = self.build / "automation/packages/demo/check.py"
        checker.write_text("import sys\nfrom pathlib import Path\n"
                           "archive = Path(sys.argv[1])\nassert archive.is_file()\n"
                           "archive.with_suffix('.checked').write_text('checked')\n")
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.log.read_text(), "install\ncheck\n")
        self.assertTrue(list((self.work / "packages").glob("demo-1.1-1-any.pkg.tar.*")))
        self.assertTrue(list((self.work / "packages").glob("*.checked")))
        with tarfile.open(self.archive) as archive:
            self.assertEqual(set(archive.getnames()),
                             {"recipe", "recipe/PKGBUILD", "recipe/.SRCINFO", "recipe/payload", "recipe/obsolete"})
        self.assertEqual(git(self.build, "status", "--porcelain"), "?? automation/packages/demo/check.py")

    def test_failed_build_never_installs_or_changes_repository(self):
        self.prepare()
        recipe = self.recipe / "PKGBUILD"
        recipe.write_text(recipe.read_text().replace('package() { install', 'package() { return 1; install'))
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assertEqual(git(self.build, "status", "--porcelain"), "")

    def test_inconsistent_srcinfo_blocks_installation(self):
        self.prepare()
        metadata = self.recipe / ".SRCINFO"
        metadata.write_text(metadata.read_text().replace("pkgver = 1.1", "pkgver = 9.9"))
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_package_specific_check_failure_blocks_installation(self):
        self.prepare()
        checker = self.build / "automation/packages/demo/check.py"
        checker.write_text("raise SystemExit('fixture archive rejected')\n")
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture archive rejected", result.stderr)
        self.assertFalse(self.log.exists())

    def test_namcap_errors_block_installation_even_with_zero_exit_status(self):
        self.prepare()
        recipe = self.recipe / "PKGBUILD"
        recipe.write_text(recipe.read_text().replace("license=('GPL-3.0-or-later')", "license=('MIT')"))
        (self.recipe / ".SRCINFO").write_text(package.run(["makepkg", "--printsrcinfo"], self.recipe) + "\n")
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(" E: ", result.stdout)
        self.assertFalse(self.log.exists())

    def test_install_and_installed_file_check_failures_are_fatal(self):
        for failure, expected in (({"INSTALL_STATUS": "1"}, "install\n"),
                                  ({"CHECK_STATUS": "1"}, "install\ncheck\n")):
            with self.subTest(failure=failure):
                self.prepare()
                self.log.unlink(missing_ok=True)
                result = self.validate(**failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.log.read_text(), expected)
                self.assertIn("pkgver=1.0", (self.build / "demo/PKGBUILD").read_text())

    def test_build_mutating_recipe_is_not_accepted(self):
        self.prepare()
        recipe = self.recipe / "PKGBUILD"
        recipe.write_text(recipe.read_text().replace(
            'package() { install',
            'package() { echo "# build mutation" >> "$BUILDDIR/../recipe/PKGBUILD"; install'))
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("# build mutation", recipe.read_text())
        self.assertEqual(self.log.read_text(), "install\ncheck\n")
        self.assertIn("recipe/PKGBUILD:", result.stdout)
        self.assertIn("pkgver=1.0", (self.build / "demo/PKGBUILD").read_text())


if __name__ == "__main__":
    unittest.main()
