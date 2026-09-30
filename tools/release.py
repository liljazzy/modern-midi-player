"""Publish a new version in one command.

    python tools/release.py 1.2.0 "Faster editor" "New mixer colours"

1. sets midiplayer/__init__.py to the new version
2. adds the notes to CHANGELOG.md
3. commits, tags v1.2.0 and pushes

GitHub Actions (.github/workflows/release.yml) then builds the installer and
publishes the release; every installed copy is offered the update.
Use --dry-run to only edit the files.
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INIT = os.path.join(ROOT, "midiplayer", "__init__.py")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")


def current_version() -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', open(INIT, encoding="utf-8").read())
    return m.group(1)


def vtuple(v):
    return tuple(int(x) for x in v.split("."))


def git(*args):
    print("+ git", " ".join(args))
    subprocess.check_call(["git", *args], cwd=ROOT)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", help="new version, e.g. 1.2.0")
    ap.add_argument("notes", nargs="*", help="release note lines")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    new = a.version.lstrip("vV")
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        sys.exit("version must look like 1.2.3")
    old = current_version()
    if vtuple(new) <= vtuple(old):
        sys.exit(f"new version {new} must be higher than the current {old}")

    text = open(INIT, encoding="utf-8").read()
    open(INIT, "w", encoding="utf-8").write(re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{new}"', text))

    notes = "\n".join(f"- {n}" for n in a.notes) or "- Improvements and fixes."
    log = open(CHANGELOG, encoding="utf-8").read() if os.path.exists(CHANGELOG) else "# Changelog\n"
    head, sep, rest = log.partition("\n## ")
    log = f"{head.rstrip()}\n\n## {new}\n{notes}\n" + (f"\n## {rest}" if sep else "")
    open(CHANGELOG, "w", encoding="utf-8").write(log)
    print(f"version {old} -> {new}")
    if a.dry_run:
        return
    git("add", INIT, CHANGELOG)
    git("commit", "-m", f"Release {new}")
    git("tag", "-a", f"v{new}", "-m", f"Modern MIDI Player {new}")
    git("push")
    git("push", "origin", f"v{new}")
    print(f"\nPushed v{new}. GitHub Actions is building the installer; watch the Actions tab.")


if __name__ == "__main__":
    main()
