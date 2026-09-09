# Figaro MCP Servers

Local, fully-free, offline MCP servers that power **Figaro** — a private desktop
AI assistant. No cloud, no API keys, no telemetry. Two servers:

| Server | Directory | What it does |
| --- | --- | --- |
| **Figaro Image Gen** | `image-gen/` | Generates images locally with Stable Diffusion XL Turbo (Apple Silicon MPS). Previews appear inline in the chat and are auto-cleaned; images are only saved when the user explicitly asks. |
| **Figaro Local Agent** | `local-agent/` | Safe system maintenance on the current Mac: empty the Trash, clear `~/Library/Caches`, clear old temp files. Everything is dry-run-first for destructive actions. |

## How inline image previews work

The image is rendered on-device and written to the app's data directory
(`$APPDATA/previews`, 24-hour auto-prune):

1. `generate_image` returns only a **short text URL** — image bytes are never
   sent into the model context (a native image tool-result pushed 40x more
   tokens than the context window).
2. The URL uses the Tauri **asset protocol**
   (`asset://localhost/<percent-encoded absolute path>`), which Chat apps
   that run Tauri allow in their Content-Security-Policy
   (`img-src ... asset:`).
3. The assistant embeds that URL as a markdown image so the picture renders
   inline in the conversation.
4. The image is **not** saved anywhere until the user asks; `save_image`
   then copies it to `~/Pictures/Figaro` (or a folder of your choice).

> If your chat app uses a plain localhost HTTP server for previews, note that
> a Tauri CSP of `img-src 'self' asset: http://asset.localhost blob: data: https:`
> will block `http://127.0.0.1:PORT` images. The `asset:` protocol avoids that.

## Requirements

- macOS with Apple Silicon (MPS). CPU fallback is supported but slow.
- Python 3.10+ and [`uv`](https://docs.astral.sh/uv/) (optional, recommended)
  or plain `pip`.
- ~3 GB free disk for the sd-turbo model (model weights are **not** in this repo).

## Setup

### 1. Image server

```bash
cd image-gen
uv venv .venv
uv pip install -r requirements.txt
```

Download the SDXL-Turbo model (fp16 variant) into `image-gen/sd-turbo/`
(e.g. from Hugging Face:
`git clone https://huggingface.co/stabilityai/sdxl-turbo` plus the fp16
variant, or point `SD_TURBO_DIR` at any local diffusers-format folder).

Run it:

```bash
.venv/bin/mcp run server_image.py        # stdio transport, as the app will
```

The model auto-loads on startup (~6–20 s first time), auto-unloads after
5 minutes idle to free ~5 GB of unified memory.

Optional environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SD_TURBO_DIR` | `./sd-turbo` | Path to the local model folder |
| `FIGARO_PREVIEW_DIR` | `~/Library/Application Support/jan.ai.app/previews` | Where ephemeral previews are written |

### 2. Local agent server

No install needed — runs via `uvx`:

```bash
uvx --from "mcp[cli]<2" mcp run local-agent/server.py
```

### 3. Register the servers with the chat app

Put an entry for each server in the app's `~/.config/... /mcp_config.json`
(see `config/mcp_config.example.json`), replacing the `/ABSOLUTE/PATH/...`
placeholders. The maintenance server touches only the current user's own
`~/.Trash`, `~/Library/Caches`, and temp files.

### 4. Assistants (optional)

Example assistant personas that know how to use these tools live in
`config/assistants/` (`figaro.example.json`, `figaro-illustrator.example.json`,
`figaro-caretaker.example.json`). Their `instructions` encode the inline-image
rule: *embed the preview URL as a markdown image, never auto-save; save only
when asked*.

## Tools

**figaro-image-gen**

- `generate_image(prompt, negative_prompt, steps, width, height)` — returns the
  preview URL; the assistant renders it inline.
- `save_image(preview_path, name)` — permanently copies a preview to
  `~/Pictures/Figaro` (only when the user asks to keep the image).

**figaro-local-agent**

- `empty_trash()` — empties the user's macOS Trash, reports freed space.
- `clear_caches(dry_run)` — dry-run preview, then clear `~/Library/Caches`.
- `clear_temp(dry_run)` — dry-run preview, then remove temp files older than 24 h.

> `empty_trash` may need **Full Disk Access** for the host app on macOS when
> protected/root-owned items are present; the tool returns instructions if so.

## Updating (keeping customizations)

Figaro is a re-branded **Jan.app** (bundle id `jan.ai.app`). It can be kept up
to date from the official Jan releases without losing any modifications:

- All user data (assistants, MCP servers, models, threads, settings) lives in
  `~/Library/Application Support/Jan/data/` — separate from the app bundle, so
  it is never touched by an update.
- `figaro-update/figaro-update.py` downloads the latest
  `Jan.app.tar.gz` from `github.com/janhq/jan/releases`, then **re-applies** the
  Figaro display name, the icon (`figaro-update/figaro.icns`), and the ad-hoc
  code signature before swapping the bundle:
  ```bash
  figaro-update/figaro-update.py check     # installed vs latest
  figaro-update/figaro-update.py update    # install + relaunch
  figaro-update/figaro-update.py rebrand   # re-apply branding only
  ```
- `scripts/com.figaro.rebrand.plist` is a **LaunchAgent** guard: it runs
  `rebrand` every 30 minutes (and at login), so even if Jan's built-in Tauri
  updater swaps the bundle silently, the Figaro icon/name are restored
  automatically. Install it with:
  ```bash
  cp scripts/com.figaro.rebrand.plist ~/Library/LaunchAgents/
  launchctl load ~/Library/LaunchAgents/com.figaro.rebrand.plist
  ```
- From chat, ask the Caretaker to "check for updates" (`check_for_update`) or
  "update Figaro" (`update_figaro`) — it runs the same script in the
  background and reopens the app when done.

> Note: the OS-level process name remains "Jan" (embedded in the binary and not
> patchable) — everything inside the app and everywhere in the filesystem is
> branded as Figaro.

## Stability notes

- The image server grabs its pipeline under a lock so the idle-unload watchdog
  can never race an in-flight generation.
- Every failure path returns a plain message the assistant can relay (no
  unhandled exceptions bubble out of tool calls).

## License & credits

- Code: MIT (see `LICENSE`).
- Icon / preview art is generated by the local model at runtime. The sample
  app-icon (Twemoji **Black Cat**, `🐈‍⬛`) is © Twitter/X, licensed
  under CC-BY 4.0; attribute accordingly if you ship it.