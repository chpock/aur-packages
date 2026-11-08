# Maintenance

## Add a package

The unit of automation is an AUR **pkgbase**, which can produce multiple packages.
The current builder supports x86_64 and dependencies from official Arch
repositories. Other architectures and AUR-only dependencies need explicit build
support.

1. For an existing AUR package, import its history from a clean checkout with
   `aurpublish -p another-package`. Preserve the subtree-import merge; do not squash
   or rewrite its ancestry. For a new package, create its recipe and `.SRCINFO`
   directly: there is no AUR history to import. Keep the root directory named after
   pkgbase. A reachable empty AUR repository is treated as awaiting first publication;
   connection/repository errors still stop preparation.
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
changes. A package's `namcap-exclude` may replace only `pydepends`, and requires
its own mandatory archive `check.py`; all other rules and fatal errors remain.
The dashboard's GUI is not launched. Upstream signatures are not verified.

## Publication failures

Publication updates GitHub before AUR. If AUR fails afterward, the next run retries
AUR even without a new release; do not invent a version bump to trigger recovery.
Changes to the same package or its automation require fresh validation. Divergent
AUR history stops publication and needs manual reconciliation; it is never
force-pushed or automatically merged without testing.

## Repowise runtime

Repowise and its Python dependencies live in `/usr/lib/repowise`, isolated from system
site-packages but using Arch's system Python. The three commands are exposed under
`/usr/bin`. Rebuild the package after a Python minor-version upgrade; the private
site-packages and native extension wheels are tied to that interpreter version.
The package does not install or update application code through pipx, pip or uv at
runtime. Dependencies are downloaded during the build with versions and hashes
from the release's `uv.lock`; incompatible or unavailable wheels stop the build.

The narrowly scoped exception is documented in `repowise/numpy-override.txt`.
The release's optional graph extra pins an old NumPy even when that extra is not
installed. `_needs_numpy_compat` applies the replacement only when the universal
lock's sole NumPy version is exactly `1.26.4`. A newer or split upstream resolution
is used unchanged. Reevaluate and remove this exception when upstream fixes the
runtime lock; do not extend it by loosening unrelated dependency constraints.

New upstream dependencies and locked versions may update automatically when all
required compatibility and packaging checks succeed. Unsupported inputs, failed
inspection, missing verified artifacts or incompatible requirements must fail the
job, not select guessed replacements or skip a check. In particular, the NumPy
probe returns a value separately from its exit status: a lock inspection error
must stop the build rather than mean that the override is unnecessary.

The Next.js dashboard is built from the same release and npm lock, installed under
`/usr/lib/repowise/web`, and served read-only with system Node.js. `--refresh-ui`
continues to use those packaged files and explains that updates go through AUR;
it does not download a separate dashboard into HOME. Credentials, agent settings
and project indexes remain user-owned. Next.js standalone tracing omits many
upstream copyright/license notices, so `collect-web-licenses.py` copies them from
the matching installed npm packages into `/usr/share/licenses/repowise/web`.
READMEs are retained too, since bundled native libraries list their licenses there.
A mismatched package name/version stops collection rather than copying another
version's notices.

`automation/packages/repowise/check.py` verifies the archive's payload and runs
`smoke.py` with its extracted private interpreter before trial installation.
Namcap's `pydepends` rule resolves imports against global system packages rather
than the private archive, so only that rule is replaced by the mandatory isolated
`uv pip check` and application smoke tests. Dependencies of optional library test,
compilation and integration helpers are not included unless the selected runtime
requires them. Reevaluate the exception if Namcap gains private-environment support.
SciPy's known stale wheel RUNPATH is removed narrowly in `PKGBUILD`; other ELF
paths remain subject to Namcap. Native SciPy operations are covered by smoke tests.
For changes to the launch path or service wiring, additionally test the installed
CLI against a small local Git repository, its real API/MCP endpoints, and the
packaged dashboard as an unprivileged user with a fresh HOME.

The installed integration runner is deliberately separate from the pre-install
archive hook. Inside a disposable container with the package installed, use a
fresh normal-user HOME and a new repository path:

```sh
/usr/lib/repowise/bin/python tests/repowise_integration.py "$HOME/fixture"
```

It indexes Python/C++ code without model calls, exercises the native Sharp
dependency, sends real MCP/API/proxied-dashboard requests, and checks that the
package payload stays unchanged. `--browser` keeps the server alive for an
external browser; signal the PID recorded in `$HOME/integration.pid` with SIGUSR1
inside that container to finish. When publishing container ports, set a test
`REPOWISE_API_KEY` and enter it in the browser's Connection Settings. Forwarded
browser requests are network callers, not trusted loopback requests; do not
disable that authentication boundary. After reinstalling and removing the package,
run the same file with system Python and `--removed` to check that all saved user
state content, ownership and permissions were preserved. Never run these
installation/removal trials on the host.
