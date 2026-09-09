#!/usr/bin/env python3
"""Figaro updater.

Keeps Figaro (a re-branded Jan.app, bundle id jan.ai.app) up to date from the
official Jan release feed (https://github.com/janhq/jan/releases) while
re-applying the Figaro customizations on every install:

  * display name "Figaro"
  * custom icon (figaro.icns next to this script)
  * ad-hoc code signature

Usage:
  figaro-update.py check            compare installed vs latest, print status
  figaro-update.py update           download + install + relaunch (if newer)
  figaro-update.py rebrand          re-apply branding to the installed app only
  figaro-update.py rebrand --force  re-apply and re-sign even if already branded

What this does NOT touch: the app-data directory
(~Library/Application Support/Jan/data) that holds assistants, MCP servers,
settings, models and threads - so your customizations there always survive.
"""

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP = Path("/Applications/Figaro.app")
SCRIPT_DIR = Path(__file__).resolve().parent
ICON = SCRIPT_DIR / "figaro.icns"
LOG = Path.home() / "Library" / "Logs" / "figaro-update.log"
WORK = Path("/tmp/figaro-update")
RELEASE_URL = "https://api.github.com/repos/janhq/jan/releases/latest"
TARBALL_URL = "https://github.com/janhq/jan/releases/latest/download/Jan.app.tar.gz"
BRAND = "Figaro"
BUNDLE_ID = "jan.ai.app"


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with LOG.open("a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def installed_version() -> str:
    if not (APP / "Contents" / "Info.plist").exists():
        return "0.0.0 (not installed)"
    try:
        out = subprocess.run(
            ["plutil", "-extract", "CFBundleShortVersionString", "raw", "-o", "-",
             str(APP / "Contents" / "Info.plist")],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return out or "0.0.0"
    except subprocess.CalledProcessError:
        return "0.0.0"


def latest_release() -> dict:
    req = urllib.request.Request(RELEASE_URL, headers={"User-Agent": "figaro-updater"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def release_version(release: dict) -> str:
    return release.get("tag_name", "").lstrip("v")


def version_tuple(v: str) -> tuple:
    parts = []
    for p in v.strip().split("-")[0].split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def apply_branding(app: Path) -> None:
    plist = app / "Contents" / "Info.plist"
    if not plist.exists():
        raise FileNotFoundError(str(plist))
    with plist.open("rb") as f:
        info = plistlib.load(f)
    changed = False
    for key in ("CFBundleName", "CFBundleDisplayName"):
        if info.get(key) != BRAND:
            info[key] = BRAND
            changed = True
    if change_version := info.get("CFBundleShortVersionString"):
        info["CFBundleShortVersionString"] = change_version
        info["CFBundleVersion"] = change_version
    with plist.open("wb") as f:
        plistlib.dump(info, f)
    icon = app / "Contents" / "Resources" / "icon.icns"
    if ICON.exists() and (not icon.exists() or icon.read_bytes() != ICON.read_bytes()):
        shutil.copy2(ICON, icon)
        changed = True
    return changed


def resign(app: Path) -> None:
    subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", str(app)],
        check=True, capture_output=True,
    )


def download_tarball() -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    dst = WORK / "Jan.app.tar.gz"
    log(f"downloading {TARBALL_URL}")
    req = urllib.request.Request(TARBALL_URL, headers={"User-Agent": "figaro-updater"})
    with urllib.request.urlopen(req, timeout=600) as r, dst.open("wb") as f:
        shutil.copyfileobj(r, f, length=256 * 1024)
    log(f"downloaded {dst.stat().st_size / 1e6:.1f} MB")
    return dst


def install_update() -> str:
    release = latest_release()
    latest = release_version(release)
    cur = installed_version()
    if version_tuple(latest) <= version_tuple(cur):
        return f"Already up to date (installed {cur}, latest {latest})."

    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    tarball = download_tarball()
    subprocess.run(["tar", "-xzf", str(tarball), "-C", str(WORK), "--strip-components=1"],
                   check=True)
    apply_branding(WORK)
    resign(WORK)
    log("new bundle branded + signed")
    subprocess.run(["pkill", "-x", "Jan"], capture_output=True)
    time.sleep(2)
    backup = Path(f"/Applications/Figaro.app.backup-{cur}")
    if APP.exists():
        shutil.rmtree(backup, ignore_errors=True)
        os.rename(APP, backup)
        log(f"backed up old app to {backup.name}")
    os.rename(WORK, APP)
    log(f"installed Figaro {latest} at {APP}")
    return f"Updated Figaro from {cur} to {latest}."


def rebrand(force: bool) -> str:
    if not APP.exists():
        return "Figaro.app not found; nothing to rebrand."
    before = installed_version()
    cur_binary = APP / "Contents" / "MacOS" / "Jan"
    cur_icon = APP / "Contents" / "Resources" / "icon.icns"
    icon_ok = ICON.exists() and cur_icon.exists() and cur_icon.read_bytes() == ICON.read_bytes()
    with (APP / "Contents" / "Info.plist").open("rb") as f:
        name_ok = plistlib.load(f).get("CFBundleDisplayName") == BRAND
    if not force and icon_ok and name_ok and cur_binary.exists():
        return f"Figaro already branded (v{before}); nothing to do."
    apply_branding(APP)
    resign(APP)
    log(f"rebranded Figaro (v{before})")
    return f"Figaro branding re-applied (v{before}); icon + name + signature refreshed."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("update")
    rb = sub.add_parser("rebrand")
    rb.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.cmd == "check":
        cur = installed_version()
        try:
            latest = release_version(latest_release())
        except Exception as e:  # offline / API error
            log(f"check: {cur}; could not reach release feed: {e}")
            return
        state = "up to date" if version_tuple(latest) <= version_tuple(cur) else "UPDATE AVAILABLE"
        log(f"installed: {cur} | latest: {latest} | {state} | {TARBALL_URL}")
        return

    if args.cmd == "update":
        try:
            log(install_update())
        except Exception as e:  # noqa: BLE001 - report anything to the caller
            log(f"update FAILED: {e}")
            return
        subprocess.Popen(["open", str(APP)])
        log("relaunched Figaro")

    if args.cmd == "rebrand":
        print(rebrand(args.force))


if __name__ == "__main__":
    main()