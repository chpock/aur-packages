#!/usr/bin/env bash
set -euo pipefail

pkgbase=${1:?pkgbase required}
workdir=$(realpath "${2:?build directory required}")
repository=$PWD
export SRCDEST="$workdir/sources"
export BUILDDIR="$workdir/build"
export PKGDEST="$workdir/packages"
mkdir -p "$SRCDEST" "$BUILDDIR" "$PKGDEST"

# Preserve exactly the recipe that is about to be built, including file modes.
tar -cf "$workdir/recipe.tar" -C "$workdir" recipe
cd "$workdir/recipe"
makepkg --cleanbuild --syncdeps --noconfirm
makepkg --printsrcinfo > "$workdir/generated.SRCINFO"
diff -u .SRCINFO "$workdir/generated.SRCINFO"
archives=("$PKGDEST"/*.pkg.tar.*)
[[ -f "${archives[0]}" ]] || { echo 'No package archives were built' >&2; exit 1; }

# namcap reports E: findings even when it exits successfully.
namcap -m PKGBUILD "${archives[@]}" | tee "$workdir/namcap.log"
if grep -Eq '^[^[:space:]]+ E: ' "$workdir/namcap.log"; then
  echo 'namcap found packaging errors' >&2
  exit 1
fi
for archive in "${archives[@]}"; do
  if [[ -f "$repository/automation/packages/$pkgbase/check.py" ]]; then
    python "$repository/automation/packages/$pkgbase/check.py" "$archive"
  fi
done

sudo pacman -U --noconfirm "${archives[@]}"
pacman -Qpq "${archives[@]}" > "$workdir/package-names"
mapfile -t packages < "$workdir/package-names"
pacman -Qkk "${packages[@]}"
tar -df "$workdir/recipe.tar" -C "$workdir"
