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

checker="$repository/automation/packages/$pkgbase/check.py"
policy="$repository/automation/packages/$pkgbase/namcap-exclude"
namcap_options=()
if [[ -f "$policy" ]]; then
  mapfile -t exclusions < <(grep -vE '^[[:space:]]*(#|$)' "$policy")
  # pydepends assumes global site-packages. Private runtimes need a mandatory
  # archive checker instead; no other namcap rule may be excluded here.
  if [[ ${#exclusions[@]} != 1 || ${exclusions[0]:-} != pydepends || ! -f "$checker" ]]; then
    echo 'Only pydepends may be replaced, and an archive check.py is required' >&2
    exit 1
  fi
  namcap_options=(-e pydepends)
fi

# namcap reports E: findings even when it exits successfully.
{
  if (( ${#namcap_options[@]} )); then
    echo 'namcap: replacing pydepends with the mandatory package archive check'
  fi
  namcap -m "${namcap_options[@]}" PKGBUILD "${archives[@]}"
} | tee "$workdir/namcap.log"
# Recipe findings use "PKGBUILD (pkgbase)" rather than the archive's single-word label.
if grep -Eq '^(PKGBUILD \([^)]*\)|[^[:space:]]+) E: ' "$workdir/namcap.log"; then
  echo 'namcap found packaging errors' >&2
  exit 1
fi
for archive in "${archives[@]}"; do
  if [[ -f "$checker" ]]; then
    python "$checker" "$archive"
  fi
done

sudo pacman -U --noconfirm "${archives[@]}"
pacman -Qpq "${archives[@]}" > "$workdir/package-names"
mapfile -t packages < "$workdir/package-names"
pacman -Qkk "${packages[@]}"
tar -df "$workdir/recipe.tar" -C "$workdir"
