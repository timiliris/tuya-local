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

## Documentation images

Run `python scripts/preview_demo.py` to serve the actual interface at `http://127.0.0.1:18889/` and the access-page preview at port 18890. This uses two synthetic cameras and temporary AI storage. It never loads account data, contacts Tuya or opens camera media. Capture the English light/dark views through the browser; publish only these reviewed images in `wiki/images/`. The 18890 password is random and deliberately undisclosed: this view is for documenting the sign-in screen.

For role screenshots, use `python scripts/preview_demo.py --users`, then open `http://localhost:18889/` for the administrator and `http://localhost:18891/` for the reader. These loopback-only fixtures auto-select synthetic sessions; they do not sign in to a real account. Use the `localhost` hostname to keep preview cookies separate from an existing installation on `127.0.0.1`.

## Releases

[CHANGELOG.md](https://github.com/timiliris/tuya-local/blob/main/CHANGELOG.md) is the English, versioned release history. Collect upcoming changes under `Unreleased`, then move them into a dated version section before publishing. Update `desktop/package.json`, its lock file, `desktop/src-tauri/tauri.conf.json`, `Cargo.toml` and `Cargo.lock` to the same version.

Run `python scripts/release.py notes v0.5.0 --output /tmp/tuya-local-release-notes.md` with the intended version to validate it. Commit the reviewed changes to `main`, then create and push an annotated `vMAJOR.MINOR.PATCH` tag. Never move a published tag: fix a failed published release with a new patch version.

The **Release** workflow verifies that the tagged commit belongs to `main`, validates all versions and the changelog, runs the CI tests and full-history secret scan, and builds native packages on macOS, Windows and Linux. It publishes the installers and `SHA256SUMS` on GitHub Releases and a Linux amd64/arm64 container at `ghcr.io/timiliris/tuya-local:vMAJOR.MINOR.PATCH` (also `:latest`). It does not publish camera data or images. Installer allowlists and the existing Docker build exclusions apply to releases too.

Desktop installers are currently unsigned. Checksums detect damaged downloads but do not replace platform signing/notarization. Releases do not enable automatic desktop updates. Only stable version tags are accepted; prereleases require an explicit workflow extension. Repository topics describe this project's camera/server features; this fork is independent of Tuya and LSC.

GitHub Container Registry creates a new package as private. After the first successful publication, the owner must change its visibility to **Public** once in [package settings](https://github.com/users/timiliris/packages/container/tuya-local/settings) to allow anonymous Docker pulls. Repository topics and public GitHub releases do not change package visibility. See [GitHub’s registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
