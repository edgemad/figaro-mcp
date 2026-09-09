#!/usr/bin/env python3
"""Figaro Image Gen - MCP server that lets Figaro generate images locally
using Stable Diffusion XL Turbo in MPS. Fully free and offline once
the model is downloaded. Previews are ephemeral and saved explicitly.

Memory-conscious design:
- Model is loaded in a background thread at startup so the first request
  in a session is already fast.
- After 5 minutes without use the model is UNLOADED (frees ~5GB of
  unified memory). If a request arrives while it is reloading, the tool
  responds immediately with a "warming up, please retry" message instead
  of blocking and risking a connection timeout.
"""

import gc
import os
import sys
import threading
import time
import uuid
import warnings
from pathlib import Path

# Force offline model loading from the local HF cache. The full model is
# already downloaded to ~/.cache/huggingface; without this, every load does
# slow network validation HEAD requests against huggingface.co.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

warnings.filterwarnings("ignore")

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("figaro-image-gen")

# Previews are written inside the app's data dir (Tauri "$APPDATA") so the
# chat can render them inline via the asset protocol that is explicitly allowed
# by the app's content-security-policy (img-src ... asset:).
#   default $APPDATA = ~/Library/Application Support/jan.ai.app/
# URL = asset://localhost/<encodeURIComponent(absolutePath)>  (as convertFileSrc
# produces). Image bytes are NEVER put into the model context (that overflows
# the 16k-token window); the model only ever sees this tiny URL string.
_APPDATA = Path.home() / "Library" / "Application Support" / "jan.ai.app"
OUTPUT_DIR = Path(os.environ.get("FIGARO_PREVIEW_DIR", _APPDATA / "previews"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Where the local sd-turbo model lives. Defaults to ./sd-turbo next to this
# file (set SD_TURBO_DIR to point elsewhere, e.g. a shared model cache).
MODEL_DIR = Path(os.environ.get("SD_TURBO_DIR", Path(__file__).resolve().parent / "sd-turbo"))

PRUNE_AFTER_HOURS = 24

from urllib.parse import quote


def _asset_url(path: Path) -> str:
    return "asset://localhost/" + quote(str(path), safe="")

UNLOAD_AFTER_SECONDS = 300  # 5 minutes idle -> unload the model

# Lifecycle state:
#   0 = unloaded/idle
#   1 = loading
#   2 = ready (loaded)
_state = 0
_state_lock = threading.Lock()
_pipe = None
_load_error = None
_last_use = 0.0


def _log(msg: str) -> None:
    sys.stderr.write(f"[figaro-image-gen] {msg}\n")
    sys.stderr.flush()
    with open("/tmp/figaro-image-gen.log", "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def _build_pipe():
    import torch

    from diffusers import AutoPipelineForText2Image

    pipe = AutoPipelineForText2Image.from_pretrained(
        MODEL_DIR,
        torch_dtype=torch.float16,
        variant="fp16",
        low_cpu_mem_usage=True,
    )
    if torch.backends.mps.is_available():
        pipe.to("mps")
    else:
        pipe.to("cpu")
    return pipe


def _load_worker():
    global _state, _pipe, _load_error, _last_use
    try:
        _log("loading model...")
        pipe = _build_pipe()
        _pipe = pipe
        _log("model loaded; warming up...")
        try:
            pipe(
                prompt="warmup",
                num_inference_steps=1,
                guidance_scale=0.0,
                width=256,
                height=256,
            )
        except Exception as e:  # noqa: BLE001 - warmup is best-effort
            _log(f"warmup warning: {e}")
        _last_use = time.time()
        with _state_lock:
            _state = 2
        _log("model ready (warm)")
    except Exception as e:  # noqa: BLE001 - surface to the tool call
        _load_error = e
        with _state_lock:
            _state = 0
        _log(f"model load failed: {e}")


def _ensure_model_loading() -> bool:
    """Return True if the model is (or will be) ready soon,
    kicking off a load if it is currently unloaded."""
    global _state
    with _state_lock:
        if _state == 2:
            return True
        if _state == 1:
            return False
        _state = 1
        _load_error = None
        threading.Thread(target=_load_worker, daemon=True).start()
        return False


def _unload_model():
    global _state, _pipe
    with _state_lock:
        _state = 0
        _pipe = None
    gc.collect()
    try:
        import torch

        torch.mps.synchronize()
        torch.mps.empty_cache()
    except Exception:  # noqa: BLE001
        pass
    _log("idle: model unloaded (freed ~5GB)")


def _idle_watchdog():
    global _last_use
    while True:
        time.sleep(30)
        with _state_lock:
            loaded = _state == 2
            idle_for = time.time() - _last_use
        if loaded and idle_for > UNLOAD_AFTER_SECONDS:
            _unload_model()


@mcp.tool()
def generate_image(
    prompt: str,
    negative_prompt: str = "blurry, low quality, distorted, ugly",
    steps: int = 4,
    width: int = 512,
    height: int = 512,
):
    """Generate an image locally from a text description. Returns a SHORT
    TEXT result containing a localhost URL for the preview image, which the
    assistant should show inline in the conversation as a markdown image.
    The preview is ephemeral and NOT saved permanently; if the user likes
    it, tell them to say "save this image" to keep it.
    - prompt: what to draw (e.g. 'a cute white baby cat, fluffy, big blue eyes').
    - steps: 1-8, default 4 (higher = slower but slightly better).
    - width/height: 256-1024 (1024 is slower).
    """
    global _last_use
    ready = _ensure_model_loading()
    if _load_error is not None:
        err = f"The image model failed to load: {_load_error}. Restarting Figaro usually fixes this."
        _log(err)
        return err
    if not ready:
        return (
            "The image model went idle to save memory and is reloading now "
            "(takes about 25 seconds). Please repeat your image request in "
            "~25 seconds and it will complete quickly."
        )

    # Grab the pipeline under the lock so the idle-unload watchdog can never
    # replace _pipe with None between our ready-check and use.
    with _state_lock:
        if _state != 2 or _pipe is None:
            return (
                "The image model was just unloading to save memory; give it a "
                "moment and repeat your request in ~25 seconds."
            )
        pipe = _pipe

    import torch

    _last_use = time.time()
    guidance = 0.0  # sdxl-turbo uses guidance_scale=0
    start = time.time()
    image = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt,
        num_inference_steps=max(1, min(8, steps)),
        guidance_scale=guidance,
        width=max(256, min(1024, width)),
        height=max(256, min(1024, height)),
    ).images[0]

    fname = f"figaro-img-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}.png"
    out_path = OUTPUT_DIR / fname
    image.save(out_path)
    elapsed = time.time() - start
    _prune_previews()
    url = _asset_url(out_path)
    return (
        f"Preview ready in {elapsed:.1f}s. Show it inline in the conversation "
        f"as an image: ![{fname}]({url})\n"
        f"Download link for the user: {url}\n"
        "This is an ephemeral chat preview and is NOT saved anywhere. "
        "The user saves it themselves by downloading it from the link."
    )


@mcp.tool()
def make_document(name: str, format: str = "docx", content: str = "") -> str:
    """Create a real file from compiled text so the user can view or download
    it in the chat. Writes ONLY an ephemeral preview into the app's preview
    folder (auto-cleaned); nothing is ever saved to disk permanently.
    - name: file name without extension (e.g. 'freedom-promotions-2026').
    - format: 'docx' (Word, default), 'md', 'txt', or 'rtf'.
    - content: the full document text/markdown to put in the file.
    Return the link to present inline in the chat: [name](url). The user
    saves the file themselves by clicking the link. Never save any file."""
    fmt = format.lower().lstrip(".")
    if fmt not in ("docx", "md", "txt", "rtf"):
        return f"Unsupported format '{format}'. Use docx, md, txt or rtf."
    doc_name = f"doc-{name or 'document'}-{uuid.uuid4().hex[:4]}.{fmt}"
    if fmt == "docx":
        try:
            from docx import Document
        except ImportError:
            return "Word support is not installed; use format 'rtf' or 'md' instead."
        doc = Document()
        for line in content.splitlines():
            doc.add_paragraph(line)
        out_path = OUTPUT_DIR / doc_name
        doc.save(out_path)
    else:
        out_path = OUTPUT_DIR / doc_name
        if fmt == "rtf":
            esc = content.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            rtf = ("{\\rtf1\\ansi\\deff0 {\\fonttbl {\\f0 Courier;}} \\f0 " +
                   esc.replace("\n", "\\par\n") + "}")
            out_path.write_text(rtf, encoding="utf-8")
        else:
            out_path.write_text(content, encoding="utf-8")
    _prune_previews()
    url = _asset_url(out_path)
    return (
        f"File ready (ephemeral — nothing saved to disk). Present it in the "
        f"chat as a download link so the user can view it in the thread and "
        f"save it themselves if they want:\n[{doc_name}]({url})"
    )


def _prune_previews() -> None:
    """Delete preview images older than PRUNE_AFTER_HOURS so the preview
    folder doesn't accumulate files (chat-rendered images stay intact
    while recent)."""
    try:
        cutoff = time.time() - PRUNE_AFTER_HOURS * 3600
        for p in (list(OUTPUT_DIR.glob("figaro-img-*.png"))
                  + list(OUTPUT_DIR.glob("jan-img-*.png"))
                  + list(OUTPUT_DIR.glob("doc-*.docx"))
                  + list(OUTPUT_DIR.glob("doc-*.rtf"))
                  + list(OUTPUT_DIR.glob("doc-*.md"))
                  + list(OUTPUT_DIR.glob("doc-*.txt"))):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


# IMPORTANT: `mcp run file.py` imports this module and runs the FastMCP
# instance itself; code inside `if __name__ == "__main__":` never runs.
# Kicked off at module level so the model warms as soon as the server starts.
_ensure_model_loading()
threading.Thread(target=_idle_watchdog, daemon=True).start()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()