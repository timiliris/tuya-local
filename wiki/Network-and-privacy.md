# Network and privacy

## Local processing

Camera video, RTSP/WebRTC output, FFmpeg conversion, AI inference and thumbnails are processed locally. Camera connections reject public media relay candidates. The browser viewer does not use public STUN/TURN services.

Account login, wake-up and signaling still use Tuya. Camera settings and PTZ may also use Tuya services. The project does not modify camera firmware.

## Docker and remote access

- Web interface: `127.0.0.1:18788` by default.
- RTSP: `127.0.0.1:8554` by default.
- Camera media: UDP 52000–52019 on `TUYA_HOST_IP`.
- Browser WebRTC: TCP 52100–52119 on the same private address.

Camera VLANs must route the necessary traffic. An RTSP URL in a browser tab is not a substitute for a compatible player such as VLC.

## Web access protection (0.2.0)

The web interface and API support an optional server password. To enable it, set `TUYA_ACCESS_PASSWORD` to a unique password of at least 12 characters in the private `.env` file and recreate the container. Do not put a password in a URL, command-line argument, issue, screenshot or Git commit. Protect `.env` with owner-only permissions.

An alternative is `TUYA_ACCESS_PASSWORD_FILE`: mount a private file or Docker secret read-only and set this environment variable to its container path. The file takes precedence over the environment password. An invalid or unreadable configured secret stops startup; it does not silently disable authentication.

![Server sign-in](https://raw.githubusercontent.com/timiliris/tuya-local/main/wiki/images/server-access.png)

Sessions use random server-side tokens and HttpOnly, SameSite=Strict cookies. They expire after 12 hours and are invalidated on logout or server restart. Password verification uses scrypt; login attempts and session counts are bounded. Passwords and session tokens are never returned by the API. The public `/api/health` endpoint discloses only the service identifier and local-video policy.

The password is optional for the default localhost installation. **It protects HTTP access, not direct RTSP clients such as VLC.** Keep RTSP bound to localhost or behind a trusted network/VPN. A web password does not encrypt HTTP: use HTTPS or a trusted VPN for another computer.

For a DNS hostname, explicitly list it in `TUYA_ALLOWED_HOSTS` (comma-separated). Private IP literals and localhost work by default. Arbitrary hostnames are rejected to reduce DNS rebinding exposure. For an HTTPS reverse proxy, set `TUYA_PUBLIC_ORIGIN` to its exact origin (for example `https://cameras.example.com`, with no path); this also enables Secure session cookies. Forward Host unchanged, and keep the direct HTTP listener private. Forwarded headers are not trusted to select an origin or a client address. Proxied clients share the proxy's login rate limit.

All API writes require JSON. Host, Origin and Fetch Metadata checks reject cross-origin controls and embedded private resources. Responses restrict scripts, framing, content sniffing and referrer disclosure. Idle connections, request sizes and simultaneous HTTP workers are bounded. Docker drops capabilities, prevents privilege escalation and runs with a read-only root filesystem; account data volumes remain writable. New POSIX runtime files use an owner-only umask. These are layers of protection, not a claim that the server has undergone an independent security audit.

Do not expose it directly to the Internet. Preserve private media-candidate routing; HTTPS alone does not solve WebRTC routing. The request-origin policy follows the [OWASP CSRF prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html). Remote desktop windows have no [Tauri native capabilities](https://v2.tauri.app/security/capabilities/).

## Private data

Account sessions and device keys stay in Docker volumes or the desktop application's data directory. `.env`, local playlists, network captures, AI settings and event databases are excluded from source control. Downloaded viewer snapshots stay on the device that downloaded them.

Do not attach raw logs, Tuya responses, device identifiers or private images to public issues. Gitleaks scans Git history; exceptions cover only exact public protocol constants and a narrowly scoped synthetic test vector.

Use GitHub private vulnerability reporting when available. Otherwise open an issue without sensitive proof and agree on a private channel before sharing details.
