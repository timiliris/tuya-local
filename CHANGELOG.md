# Changelog

Changes to Tuya Local are recorded here in English. Version tags follow Semantic Versioning. The project remains pre-1.0: review each release before upgrading.

## [Unreleased]

### Fixed
- Normalize installer filenames before upload so SHA256SUMS matches GitHub download names; reject collisions after normalization.

## [0.5.0] - 2026-10-18

### Security
- Authenticate VLC and other RTSP clients with Tuya Local accounts and enforce the same per-camera grants on the server.
- Recheck active RTSP sessions and disconnect them when a password, role or camera grant changes, or an account is deleted.
- Require explicit private credentials for the integrated player, local AI and settings wake-up; localhost and AI query parameters cannot bypass account checks.
- Remove RTSP request/response dumps that could expose authentication headers.

### Delivery
- Add tag-triggered GitHub releases, checked against the English changelog and all desktop version files.
- Gate releases on application tests, the complete Git secret scan, and macOS/Windows/Linux package builds.
- Publish native installers with SHA-256 checksums and a versioned GHCR Docker image.

### Notes
- RTSP authentication controls access; RTSP itself is not encrypted. Retain the loopback default or use a trusted local network/VPN.
- Direct RTSP playback now requires a configured administrator account. Configure accounts in Tuya Local before connecting VLC.
- Desktop installers remain unsigned; automatic application updates are not included.

## [0.4.0] - 2026-10-18

### Added
- Per-user camera grants enforced by the server for camera lists and integrated playback.
- Persistent administrator and read-only accounts, with password management in settings.
- Local WebRTC playback with browser-compatible fallback, dark mode and sentinel mosaic/rotation modes.
- Autonomous per-camera local AI with local event history and battery-aware passive analysis.
- Standalone Docker server, Tauri v2 local/remote desktop launcher and five interface languages.
- Camera settings, automatic wake-up and the corrected local media engine.

### Security
- Protected HTTP API, session ownership checks and permission revocation for active integrated players.
- Restricted browser origins, bounded requests, protected private configuration and hardened Docker defaults.

The 0.4.0 entry consolidates development completed before the first tagged Tuya Local release. It is not an upstream bridge changelog.
