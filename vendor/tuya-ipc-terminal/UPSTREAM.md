# Upstream: seydx/tuya-ipc-terminal

This directory is a **vendored copy** of

https://github.com/seydx/tuya-ipc-terminal

| | |
|---|---|
| License | MIT — see `LICENSE` (Copyright (c) 2025 seydx) |
| Upstream tag / message | 0.0.6 / `update version to 0.0.6` |
| Git commit | `d65b3e9babb4829176290b4d53195d62636f00bf` |
| Author | seydx &lt;dev@seydx.com&gt; |
| Date | 2025-05-31 |

Please prefer contributing engine fixes **upstream** when they are not
specific to this Windows GUI.

## Local patches (this project)

Applied on top of `d65b3e9`. Kept small on purpose.

1. **`pkg/rtsp/protocol.go`** — SDP uses the URL resolution (`hd` / `sd`)
   instead of always advertising HD. Backchannel `sendonly` audio track
   removed so VLC/LIVE555 does not reject the session.
2. **`pkg/rtsp/server.go`** — do not hold the stream mutex across the
   WebRTC dial (deadlock / stall when several cameras start).
3. **`pkg/storage/manager.go`** — look for `.tuya-data` in the process
   working directory, `%LOCALAPPDATA%\tuya-ipc-terminal`, and the user
   profile, not only `cwd/.tuya-data`.
4. **`cmd/cameras/cameras.go`** — treat doorbells / `sp_*` / p2p devices as
   cameras; do not drop a device if WebRTC config fails.

Re-apply after an upstream pull:

```bat
git -C vendor\tuya-ipc-terminal remote add upstream https://github.com/seydx/tuya-ipc-terminal.git
git -C vendor\tuya-ipc-terminal fetch upstream
```

Then merge/rebase and restore the three files if they conflict.

## Additional Tuya Local changes

The standalone fork adds bounded low-power wake/offer/HD retries, cached capability preservation, legacy HEVC RTP normalization, first-video readiness and failed-session cleanup. It rejects public/TURN camera media routes. `browser-player` forwards local RTSP RTP to a browser peer over local ICE-TCP without transcoding; unsupported browser codecs use the Python console's local FFmpeg fallback. Public mobile-signaling experiments remain disabled by default. Tests cover wake lifecycle, local media policy, codec normalization and browser negotiation.
