#!/usr/bin/env python3
"""Check desktop integration without launching the GUI on a headless builder."""

import configparser
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


def check(archive):
    binary = "usr/bin/magic-context-dashboard"
    desktop = "usr/share/applications/Magic Context Dashboard.desktop"
    license_file = "usr/share/licenses/magic-context-dashboard-bin/LICENSE"
    with tempfile.TemporaryDirectory() as temporary:
        subprocess.run(["bsdtar", "-xf", str(Path(archive).resolve()), "-C", temporary,
                        "--", binary, desktop, license_file], check=True)
        directory = Path(temporary)
        executable = directory / binary
        if executable.is_symlink() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("Dashboard executable is missing or not executable")
        entry = configparser.ConfigParser(interpolation=None)
        entry.read(directory / desktop)
        command = shlex.split(entry["Desktop Entry"]["Exec"])
        if not command or command[0] not in ("magic-context-dashboard", "/usr/bin/magic-context-dashboard"):
            raise ValueError("Desktop entry does not launch the packaged dashboard")
        if "MIT License" not in (directory / license_file).read_text():
            raise ValueError("Packaged MIT license is missing")


if __name__ == "__main__":
    try:
        check(sys.argv[1])
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
