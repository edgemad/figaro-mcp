#!/usr/bin/env python3
"""Figaro Local Agent - MCP server that lets Figaro perform safe system
maintenance tasks on this Mac (empty trash, clear caches, clear temporary
files).

Runs fully local. Only touches the current user's own trash/caches/temp dirs.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("figaro-local-agent")


def _human_size(bytes_: int) -> str:
    size = float(bytes_)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            break
        size /= 1024
    return f"{size:.1f} {unit}"


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


@mcp.tool()
def empty_trash() -> str:
    """Empty the macOS Trash for the current user. Returns how much was freed."""
    trash = Path.home() / ".Trash"
    if not trash.exists():
        return "No Trash found."
    freed = _dir_size(trash)
    items = [p for p in trash.iterdir()]
    if not items:
        return "Trash is already empty (0 items)."

    # Remove the contents of the Trash folder (not the special folder itself).
    # Do not abort on the first failure: collect per-item results and keep going.
    removed = 0
    failed = {}
    for item in items:
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
        except (PermissionError, OSError) as e:
            failed[item.name] = str(e or type(e).__name__)

    # Fallback 1: ask the Finder to empty the Trash for anything still remaining.
    still = [p for p in trash.iterdir()]
    if still:
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "Finder" to empty trash'],
                check=True, capture_output=True,
            )
            for p in list(still):
                if not p.exists():
                    still.remove(p)
        except subprocess.CalledProcessError:
            pass

    # Fallback 2: best-effort direct remove of anything still left.
    still = [p for p in trash.iterdir()]
    if still:
        for item in list(still):
            try:
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                still.remove(item)
            except (PermissionError, OSError):
                pass

    if still:
        leftover = ", ".join(p.name for p in still[:5])
        why = ""
        if failed:
            why = f" (first failure reason: {next(iter(failed.values()))})"
        if "Operation not permitted" in why or failed:
            why += (" Might be a macOS privacy guard on protected files. "
                    "Fix: System Settings > Privacy & Security > Full Disk Access, "
                    "add Figaro.app, restart Figaro, retry. If items are root-owned, "
                    "they can only be removed with sudo in a terminal.")
        return (f"Emptied {removed} item(s), freed {_human_size(freed)}, but could not remove "
                f"{len(still)} item(s): {leftover}.{why}")
    return f"Emptied Trash: removed {removed} item(s), freed {_human_size(freed)}."


@mcp.tool()
def clear_caches(dry_run: bool = True) -> str:
    """Clear the current user's cache folders under ~/Library/Caches.
    dry_run=True (default) only reports what would be cleared without deleting.
    Pass dry_run=False to actually clear them.
    """
    caches = Path.home() / "Library" / "Caches"
    if not caches.exists():
        return "No cache directory found."
    report = []
    total = 0
    for child in caches.iterdir():
        size = _dir_size(child) if child.is_dir() else (child.stat().st_size if child.is_file() else 0)
        total += size
        if not dry_run:
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as e:
                report.append(f"  - could not remove {child.name}: {e}")
                continue
        report.append(f"  - {child.name}: {_human_size(size)}")
    action = "would clear" if dry_run else "cleared"
    body = "\n".join(report)
    return (f"[dry-run] Caches that {action} (total {_human_size(total)}):\n{body}\n"
            f"Run with dry_run=False to actually clear them." if dry_run
            else f"Cleared caches (total {_human_size(total)}):\n{body}")


@mcp.tool()
def clear_temp(dry_run: bool = True) -> str:
    """Clear temporary files in the system /tmp (files older than 1 day).
    dry_run=True (default) only reports. Pass dry_run=False to clear.
    """
    tmp = Path(tempfile.gettempdir())
    if not tmp.exists():
        return "No temp directory found."
    candidates = []
    import time
    cutoff = time.time() - 86400  # 24h
    for child in tmp.iterdir():
        try:
            if child.stat().st_mtime < cutoff:
                candidates.append(child)
        except OSError:
            continue
    if not dry_run:
        removed = 0
        for child in candidates:
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                removed += 1
            except OSError:
                pass
        return f"Cleared {removed} temp item(s) older than 24h in {tmp}."
    return (f"[dry-run] {len(candidates)} temp item(s) older than 24h in {tmp} "
            "would be cleared. Run with dry_run=False to clear.")


@mcp.tool()
def check_for_update() -> str:
    """Check whether a newer Figaro (Jan) release is available from the
    official Jan GitHub releases. Returns installed vs latest version and a
    download link. Fully read-only."""
    try:
        out = subprocess.run(
            ["/usr/bin/python3", "/Users/edge/figaro-update/figaro-update.py", "check"],
            capture_output=True, text=True, timeout=60,
        )
        return (out.stdout or out.stderr or "check failed").strip().splitlines()[-1]
    except Exception as e:  # noqa: BLE001 - never crash the tool
        return f"Could not check for updates: {e}"


@mcp.tool()
def update_figaro() -> str:
    """Download and install the latest Figaro (Jan) release from the official
    Jan GitHub releases, preserving the Figaro icon/name/signature and all
    user data. Runs in the background: Figaro will quit and reopen by itself
    when the download finishes. Reply immediately; do not wait."""
    script = Path.home() / "figaro-update" / "figaro-update.py"
    if not script.exists():
        return f"Updater not found at {script}."
    log_file = Path.home() / "Library" / "Logs" / "figaro-update.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a") as f:
        f.write("\n---- update requested from chat ----\n")
    try:
        subprocess.Popen(
            ["/usr/bin/python3", str(script), "update"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:  # noqa: BLE001
        return f"Could not start the update: {e}"
    return ("Update started in the background. It will download the latest "
            "Jan release, install it while keeping every Figaro customization "
            "and all local data, then reopen Figaro automatically. Progress is "
            "logged to ~/Library/Logs/figaro-update.log")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
