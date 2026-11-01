# AUR packages

Daily updates for AUR packages maintained by Kostiantyn Kushnir <chpock@gmail.com>.
The software itself is developed upstream; this repository maintains its packaging.

## Packages

| pkgbase | Upstream releases | Workflow |
| --- | --- | --- |
| [magic-context-dashboard-bin](https://aur.archlinux.org/packages/magic-context-dashboard-bin) | [cortexkit/magic-context](https://github.com/cortexkit/magic-context), stable `dashboard-v*` tags | [magic-context-dashboard-bin.yml](.github/workflows/magic-context-dashboard-bin.yml) |

## How updates work

Each package has its own `<pkgbase>.yml`, schedule, and manual trigger. These
workflows call `_update-aur-package.yml`, which has two jobs:

1. **Build and check:** copy the package files into a fresh build directory.
   nvchecker discovers the release; the updater changes `pkgver`, resets
   `pkgrel`, runs `updpkgsums`, and regenerates `.SRCINFO` in that copy.
   Ordinary `makepkg --cleanbuild --syncdeps --noconfirm` builds the package as
   a non-root user in an ordinary Arch container. namcap checks both PKGBUILD
   and the built archives, followed by optional package-specific checks.
   `pacman -U` installs the archives in that disposable container and
   `pacman -Qkk` checks the installed files. Neither an AUR key nor a write
   token is available to this job. The repository's package files stay unchanged.
2. **Publish:** only after every check succeeds, a fresh job receives the ready
   recipe files, replaces that package directory, commits to GitHub, then
   publishes its subtree with aurpublish. It does not execute PKGBUILD or
   package hooks. The AUR key is available only to this job.

The build directory separates files that belong in AUR from temporary outputs:

```text
build/
  recipe/       # PKGBUILD, .SRCINFO, patches, .install, and other AUR files
  sources/      # downloaded sources (SRCDEST)
  build/        # makepkg's src/ and pkg/ (BUILDDIR)
  packages/     # built package archives (PKGDEST)
```

A plain `recipe.tar` preserves the recipe's hidden files, symlinks, and executable
bits during transfer between jobs. It contains no build outputs. It is created
before building and compared with the recipe after all checks, so build/install
steps cannot silently change the files selected for publication. There are no
Git patches, bundles, temporary candidate commits, privileged containers, nested
chroots, or external version-state database.

Already synchronized packages skip building and publishing. If GitHub succeeds
but AUR fails, the next run retries AUR even without a new release. Downgrades
are rejected using Arch's `vercmp`.

Publish jobs share a GitHub concurrency queue so different packages do not race
GitHub pushes. Unrelated package changes are preserved. A change to the same
package or its automation requires a fresh run. AUR history divergence stops
publication; it is never force-pushed or automatically merged without testing.

## Checks and their limits

- Source checksums and `.SRCINFO` consistency are checked by Arch tools.
- namcap `E:` findings stop the update even if namcap itself returns zero.
  Warnings remain visible in the build log; dependencies and packaging options
  are never changed automatically in response to suggestions. See
  [namcap](https://wiki.archlinux.org/title/Namcap) for the distinction.
- Trial installation checks pacman transactions and package hooks; `pacman -Qkk`
  checks the presence and metadata of installed files. This does not prove
  application functionality or exercise an upgrade from every older version.
- Dashboard-specific checks cover its executable, desktop launcher, and license.
  Its GUI is not launched. CLI packages can add non-interactive functional checks.

The current dashboard emits warnings for unstripped upstream binaries, an empty
debug directory, loader-path detection, transitively supplied dependencies, and
libappindicator that the static analysis does not see being used. These warnings
are not suppressed. Direct GUI dependencies remain declared even when static
analysis cannot detect dynamically loaded libraries.

The current builder supports x86_64 and dependencies from official Arch
repositories. Other architectures and AUR-only dependencies need explicit build
support. A successful build/install is not a guarantee against upstream runtime,
dependency, or license changes. SHA-256 fixes downloaded bytes but does not
authenticate the publisher; upstream signatures are not verified.

## Enable publication

1. Register a dedicated SSH key in your AUR account and add the private key as
   the GitHub Actions secret **`AUR_SSH_PRIVATE_KEY`**.
2. Allow the publication job to write repository contents. Branch protection
   must permit this bot's push; the workflow does not bypass protection.
3. Run the package workflow manually with **`publish` unchecked** first. Then
   run with publication enabled. Scheduled runs and relevant pushes to `main`
   publish automatically after successful checks.

The AUR Ed25519 host key is pinned in `automation/aur_known_hosts`; verify rotations
against [the official AUR site](https://aur.archlinux.org/). Actions are pinned to
commit SHAs and updated through Dependabot.

GitHub schedules are best-effort, can miss runs, and are disabled in public
repositories after 60 days without activity. Enable failure notifications and
check the Actions page; guaranteed liveness monitoring needs an external check.

## Add another package

1. From a clean checkout, import its existing history with
   `aurpublish -p another-package`. Keep the root directory named after pkgbase
   and preserve the subtree-import merge; do not squash or rewrite its ancestry.
2. Add `automation/packages/another-package/nvchecker.toml` containing exactly
   one `[another-package]` source. Filter and normalize upstream versions to
   the PKGBUILD's `pkgver`.
3. Copy a package workflow to `.github/workflows/another-package.yml`; change
   its name, pkgbase, package paths, and schedule. Shared code stays unchanged.
4. For anything beyond literal `pkgver=` and `pkgrel=` assignments, add an
   executable `automation/packages/another-package/update`. It receives the
   recipe copy's directory and new version as arguments; failure stops the update.
   Common code regenerates checksums and `.SRCINFO` afterward.
5. Optionally add `automation/packages/another-package/check.py`; it receives
   each built archive. Add tests and run without publication before enabling it.

Only AUR files belong in the root package directory. Configuration, scripts, and
tests stay outside it. Structural packaging changes should be reviewed and
committed normally. Do not use release-version bumps for VCS packages such as `-git`.

## Local checks

On Arch, install `base-devel git python python-coverage nvchecker pyalpm
pacman-contrib namcap aurpublish actionlint shellcheck`. Run as a normal user:

```sh
python -m unittest discover -s tests -v
# GitHub's documented queue key is not recognized by actionlint 1.7.12.
actionlint -ignore '^unexpected key "queue" for "concurrency" section\.'
shellcheck automation/validate.sh
```

Tests exercise real makepkg/namcap and temporary local Git/AUR repositories.
They substitute the installation operations so fixture packages are never
installed on the developer's host. No external publication takes place.

**Run the actual build/install validation only inside a disposable Arch container.**
It installs dependencies and built packages there. The build user needs passwordless
sudo permission for `/usr/bin/pacman`. Use a fresh build directory:

```sh
python automation/package.py prepare magic-context-dashboard-bin --workdir /work/build
bash automation/validate.sh magic-context-dashboard-bin /work/build
```
