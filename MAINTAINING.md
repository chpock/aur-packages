# Maintenance

## Add a package

The unit of automation is an AUR **pkgbase**, which can produce multiple packages.
The current builder supports x86_64 and dependencies from official Arch
repositories. Other architectures and AUR-only dependencies need explicit build
support.

1. From a clean checkout, import the existing AUR history with
   `aurpublish -p another-package`. Keep the root directory named after pkgbase
   and preserve the subtree-import merge; do not squash or rewrite its ancestry.
2. Add `automation/packages/another-package/nvchecker.toml` containing exactly
   one `[another-package]` source. Filter and normalize upstream versions to
   the PKGBUILD's `pkgver`.
3. Copy an existing package workflow to `.github/workflows/another-package.yml`;
   change its name, pkgbase, package paths, and schedule. The shared workflow
   does not need changing for an already-supported update method.
4. For anything beyond literal `pkgver=` and `pkgrel=` assignments, add an
   executable `automation/packages/another-package/update`. It receives the
   recipe copy's directory and new version as arguments; failure stops the update.
   Common code regenerates checksums and `.SRCINFO` afterward.
5. Optionally add `automation/packages/another-package/check.py`; it receives
   each built archive. Use it for package-specific content or functional checks.
6. Add the package to the table in [README.md](README.md), add regression coverage,
   and run local checks and build validation before pushing the new workflow.

Only AUR files belong in the root package directory. Configuration, scripts, and
tests stay outside it. Review structural packaging changes normally rather than
making them part of an automatic version bump. Do not use release-version bumps
for VCS packages such as `-git`.

Relevant pushes to `main` and scheduled runs publish automatically after checks.
Pushing a new package workflow therefore enables its automation. Manual runs
have a separate `publish` option, which is unchecked by default.

## Local checks

On Arch, install `base-devel git python python-coverage nvchecker pyalpm
pacman-contrib namcap aurpublish actionlint shellcheck`. Run from the repository
root as a normal user:

```sh
python -m unittest discover -s tests -v
# actionlint 1.7.12 does not recognize GitHub's queue key.
actionlint -ignore '^unexpected key "queue" for "concurrency" section\.'
shellcheck automation/validate.sh
```

Tests use real makepkg/namcap and temporary local Git/AUR repositories. They
substitute installation operations so fixture packages are never installed on
the developer's host. No external publication takes place.

## Build validation

Run the actual build/install validation **only inside a disposable, ordinary Arch
container**, as a non-root build user. It installs dependencies and the built
packages there. The user needs a writable checkout and passwordless sudo permission
for `/usr/bin/pacman`. The build directory must not already exist.

From the repository root inside that container:

```sh
python automation/package.py prepare magic-context-dashboard-bin --workdir "$HOME/aur-build"
bash automation/validate.sh magic-context-dashboard-bin "$HOME/aur-build"
```

Preparation changes only the recipe copy in the build directory. Source downloads,
build intermediates, and package archives are kept outside that recipe. Validation
checks `.SRCINFO`, runs namcap on the recipe and archives, executes package-specific
checks, installs the archives, and checks installed files with `pacman -Qkk`.

namcap `E:` findings stop the update even if namcap returns zero. Warnings remain
in the log and should be reviewed, not automatically translated into dependency
changes. The dashboard's GUI is not launched. Upstream signatures are not verified.

## Publication failures

Publication updates GitHub before AUR. If AUR fails afterward, the next run retries
AUR even without a new release; do not invent a version bump to trigger recovery.
Changes to the same package or its automation require fresh validation. Divergent
AUR history stops publication and needs manual reconciliation; it is never
force-pushed or automatically merged without testing.
