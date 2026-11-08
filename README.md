# AUR packages

Arch Linux packaging recipes and automated updates for
[Arch User Repository (AUR)](https://aur.archlinux.org/) packages maintained by
[Kostiantyn Kushnir](https://github.com/chpock).

The applications are developed in their upstream projects. This repository
maintains their Arch Linux packaging, not the applications themselves.

## Packages

| Package | Upstream project | Recipe |
| --- | --- | --- |
| [magic-context-dashboard-bin](https://aur.archlinux.org/packages/magic-context-dashboard-bin) | [Magic Context Dashboard](https://github.com/cortexkit/magic-context) | [PKGBUILD](magic-context-dashboard-bin/PKGBUILD) |
| repowise | [Repowise](https://github.com/repowise-dev/repowise) | [PKGBUILD](repowise/PKGBUILD) |

## Automated updates

[GitHub Actions](https://github.com/chpock/aur-packages/actions) checks for new
upstream releases daily, with a separate workflow for each package.

Updates are built, checked with namcap, and trial-installed in a disposable Arch
Linux container. Only updates that pass these checks are published to GitHub and
AUR. These are packaging and installation checks, not a guarantee that the
application works correctly.

For adding packages and running local checks, see [MAINTAINING.md](MAINTAINING.md).
