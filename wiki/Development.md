# Architecture and development

- `src/`: shared Python server, camera settings and local AI.
- `web/`: interface shared by browsers and the desktop viewer.
- `vendor/tuya-ipc-terminal/`: corrected Go media engine.
- `vendor/nanodet/`: Apache-2.0 inference implementation.
- `desktop/`: Tauri v2 launcher and frozen-server entry point.
- `scripts/`: native server builds, runtime smoke tests and wiki publication.
- `tests/`: Python and JavaScript tests; Go tests live with the engine.
- `wiki/`: versioned sources for this documentation.

Docker and desktop use the same server API. The launcher controls only the server it started. Remote dashboard windows receive no native capabilities.

## Checks

```sh
python -m unittest discover -s tests -p 'test_console*.py'
node --test tests/*.cjs
cd vendor/tuya-ipc-terminal
go test -race ./pkg/rtsp ./pkg/webrtc ./pkg/tuya
```

In `desktop/`, run `npm ci` and `npm run check` for Rust tests. Package builds run natively on each target OS after preparing the embedded server. `python scripts/test_desktop_server.py` exercises the actual frozen engine, decoder, models, API, assets and graceful shutdown with isolated empty account data.

The old Tkinter GUI, bundled VLC, Inno Setup installers and legacy HLS/archive modes have been removed from the active tree. Licenses for retained code stay in the repository; Git history preserves the old implementation.
