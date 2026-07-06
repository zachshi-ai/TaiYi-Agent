# Taiyi macOS app (.app)

A double-clickable macOS app that runs Taiyi and opens the web UI — no terminal,
no Python required on the target machine. Built with PyInstaller.

## Build

```bash
bash deploy/macos/build.sh          # → dist/Taiyi.app
bash deploy/macos/build.sh --dmg    # → dist/Taiyi.app + dist/Taiyi.dmg (drag-to-Applications)
```

The script creates a throwaway build venv (`.build-venv/`, gitignored), installs
`taiyi[live]` + PyInstaller, and runs `deploy/macos/Taiyi.spec`.

## Run

```bash
open dist/Taiyi.app
```

On launch it starts the gateway on `http://127.0.0.1:8080/` and opens your browser
at the web UI (chat/tasks, approvals, OODA review, memory/metrics, config).

## Where data lives

The `.app` bundle is read-only, so all state is written to a writable location:

```
~/Library/Application Support/Taiyi/
  taiyi.yaml        # config — written on first launch (offline defaults); edit + relaunch
  audit.jsonl       # governance audit log
  taiyi.db          # memory (SQLite/FTS5/vector)
  iteration.db      # OODA trajectories
```

To point at a real model: launch once (creates `taiyi.yaml`), edit `provider` /
`base_url` / `model` / `api_key` there — or use the in-app **Config** panel — then
relaunch. `httpx` is already bundled (built from `taiyi[live]`).

## Unsigned — Gatekeeper note

This build is **not code-signed or notarized**. On the machine that built it, it
runs directly. On another Mac, the first open is blocked by Gatekeeper; the user
must **right-click → Open** once (or `xattr -dr com.apple.quarantine Taiyi.app`).
For public distribution, sign + notarize with an Apple Developer ID:

```bash
codesign --deep --force --options runtime \
  --sign "Developer ID Application: YOUR NAME (TEAMID)" dist/Taiyi.app
xcrun notarytool submit dist/Taiyi.dmg --apple-id ... --team-id ... --wait
xcrun stapler staple dist/Taiyi.app
```

## Files

- `taiyi_launcher.py` — the app entry point (starts server, opens browser, writes to Application Support)
- `Taiyi.spec` — PyInstaller bundle definition (ships rules/scenarios/skills/value_stream + web/dist)
- `build.sh` — build the `.app` (and optional `.dmg`)
