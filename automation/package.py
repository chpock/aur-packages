#!/usr/bin/env python3
"""Prepare an AUR recipe in a build directory or publish its checked files."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib


MAINTAINER = ["-c", "user.name=Kostiantyn Kushnir", "-c", "user.email=chpock@gmail.com"]


def run(args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=True).stdout.strip()


def git(repo, *args):
    return run(["git", "-C", str(repo), *map(str, args)])


def check_pkgbase(name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9@._+-]*", name):
        raise ValueError(f"Invalid pkgbase: {name!r}")


def check_version(version):
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~]*", version):
        raise ValueError(f"Unsupported pkgver: {version!r}")
    return version


def bump_version(recipe, version):
    check_version(version)
    for field, pattern, value in (("pkgver", r"[A-Za-z0-9._+~]+", version),
                                  ("pkgrel", r"[0-9]+(?:\.[0-9]+)?", "1")):
        assignments = re.findall(rf"(?m)^{field}=.*$", recipe)
        literal = rf"{field}=(?:{pattern}|'{pattern}'|\"{pattern}\")[ \t]*"
        if len(assignments) != 1 or not re.fullmatch(literal, assignments[0]):
            raise ValueError(f"{field} needs one literal assignment; provide a package-specific updater")
        recipe = recipe.replace(assignments[0], f"{field}={value}", 1)
    return recipe


def is_upgrade(current, available):
    comparison = int(run(["vercmp", check_version(available), current]))
    if comparison < 0:
        raise ValueError(f"Upstream version {available} is older than packaged version {current}")
    return comparison > 0


def checked_version(output, pkgbase):
    events = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    if any(event.get("level") in ("error", "critical") for event in events):
        raise ValueError("nvchecker reported an error")
    versions = [event["version"] for event in events
                if event.get("name") == pkgbase and event.get("event") == "updated"]
    if len(versions) != 1:
        raise ValueError(f"nvchecker must return exactly one version for {pkgbase}")
    return check_version(versions[0])


def detect_version(config, pkgbase):
    content = config.read_text()
    if set(tomllib.loads(content)) != {pkgbase}:
        raise ValueError(f"{config} must contain only the [{pkgbase}] source")
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        if token := os.environ.get("GITHUB_TOKEN"):
            keyfile = directory / "keys.toml"
            keyfile.write_text("[keys]\ngithub = " + json.dumps(token) + "\n")
            keyfile.chmod(0o600)
            content += "\n[__config__]\nkeyfile = " + json.dumps(str(keyfile)) + "\n"
        config = directory / "nvchecker.toml"
        config.write_text(content)
        result = subprocess.run(["nvchecker", "-c", str(config), "--logger=json"],
                                text=True, capture_output=True, check=True)
        return checked_version(result.stdout + "\n" + result.stderr, pkgbase)


def srcinfo_version(text, pkgbase):
    bases = re.findall(r"(?m)^\s*pkgbase = (.+)$", text)
    versions = re.findall(r"(?m)^\s*pkgver = (.+)$", text)
    if bases != [pkgbase] or len(versions) != 1:
        raise ValueError(f"Invalid .SRCINFO identity for {pkgbase}")
    return check_version(versions[0])


def prepare(repo, pkgbase, workdir, aur_url=None):
    check_pkgbase(pkgbase)
    source = Path(repo).resolve() / pkgbase
    workdir = Path(workdir).resolve()
    if workdir.is_relative_to(source):
        raise ValueError("Build directory must be outside the package directory")
    workdir.mkdir(parents=True)
    directory = workdir / "recipe"
    shutil.copytree(source, directory, symlinks=True)
    (workdir / "sources").mkdir()
    configuration = Path(repo) / "automation/packages" / pkgbase
    current = srcinfo_version((directory / ".SRCINFO").read_text(), pkgbase)
    version = detect_version(configuration / "nvchecker.toml", pkgbase)
    if is_upgrade(current, version):
        updater = configuration / "update"
        if updater.is_file():
            run([str(updater), str(directory), version])
        else:
            recipe = directory / "PKGBUILD"
            recipe.write_text(bump_version(recipe.read_text(), version))
        run(["updpkgsums"], directory, env=dict(os.environ, SRCDEST=str(workdir / "sources")))
    metadata = run(["makepkg", "--printsrcinfo"], directory) + "\n"
    if srcinfo_version(metadata, pkgbase) != version:
        raise ValueError("Updated PKGBUILD did not produce the detected upstream version")
    (directory / ".SRCINFO").write_text(metadata)
    comparison = subprocess.run(["diff", "-qr", "--no-dereference", str(source), str(directory)],
                                text=True, capture_output=True)
    if comparison.returncode not in (0, 1):
        comparison.check_returncode()
    if comparison.returncode == 1:
        return True
    remote = aur_url or f"https://aur.archlinux.org/{pkgbase}.git"
    if not git(repo, "ls-remote", "--heads", remote, "refs/heads/master"):
        return True
    git(repo, "fetch", "--no-tags", remote, "master")
    return git(repo, "rev-parse", "FETCH_HEAD^{tree}") != git(repo, "rev-parse", f"HEAD:{pkgbase}")


def publish(repo, pkgbase, archive, branch, aur_root=None):
    check_pkgbase(pkgbase)
    if git(repo, "status", "--porcelain"):
        raise ValueError("Publication requires a clean checkout")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "check-ref-format", f"refs/heads/{branch}")
    git(repo, "fetch", "origin", f"refs/heads/{branch}")
    relevant = [pkgbase, "automation/package.py", "automation/validate.sh", "automation/aur_known_hosts",
                f"automation/packages/{pkgbase}", ".github/workflows/_update-aur-package.yml",
                f".github/workflows/{pkgbase}.yml"]
    if git(repo, "diff", "--name-only", base, "FETCH_HEAD", "--", *relevant):
        raise ValueError("Package or its automation changed after preparation; rerun validation")
    git(repo, "checkout", "--detach", "FETCH_HEAD")
    git(repo, "config", "core.hooksPath", "/dev/null")
    with tempfile.TemporaryDirectory() as temporary:
        with tarfile.open(archive) as files:
            files.extractall(temporary, filter="data")
        directory = Path(temporary) / "recipe"
        srcinfo_version((directory / ".SRCINFO").read_text(), pkgbase)
        shutil.rmtree(Path(repo) / pkgbase)
        shutil.copytree(directory, Path(repo) / pkgbase, symlinks=True)
    git(repo, "add", "--all", "--", pkgbase)
    srcinfo_version(git(repo, "show", f":{pkgbase}/.SRCINFO"), pkgbase)
    if git(repo, "diff", "--cached", "--name-only"):
        git(repo, *MAINTAINER, "commit", "-m", f"chore({pkgbase}): update package")
        git(repo, "push", "origin", f"HEAD:refs/heads/{branch}")
    # aurpublish exports Git history without executing PKGBUILD or installing hooks.
    run(["aurpublish", "--url", aur_root or "ssh://aur@aur.archlinux.org", pkgbase], repo)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--workdir", type=Path, required=True)
    publication = commands.add_parser("publish")
    publication.add_argument("--archive", type=Path, required=True)
    publication.add_argument("--branch", default="main")
    for command in (preparation, publication):
        command.add_argument("pkgbase")
        command.add_argument("--aur-url")
    args = parser.parse_args()
    if args.command == "prepare":
        pending = prepare(Path.cwd(), args.pkgbase, args.workdir, args.aur_url)
        print(f"needs_publish={str(pending).lower()}")
        if target := os.environ.get("GITHUB_OUTPUT"):
            with open(target, "a") as stream:
                stream.write(f"needs_publish={str(pending).lower()}\n")
    else:
        publish(Path.cwd(), args.pkgbase, args.archive, args.branch, args.aur_url)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Command failed: {error.cmd}\n{error.stdout or ''}\n{error.stderr or ''}") from error
    except (ValueError, OSError, tarfile.TarError) as error:
        raise SystemExit(str(error)) from error
