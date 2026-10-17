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

The server has no built-in web authentication. Do not expose it directly to the Internet. Use a trusted or protected private network for remote access and preserve private media-candidate routing. HTTPS alone does not solve WebRTC routing.

## Private data

Account sessions and device keys stay in Docker volumes or the desktop application's data directory. `.env`, local playlists, network captures, AI settings and event databases are excluded from source control. Downloaded viewer snapshots stay on the device that downloaded them.

Do not attach raw logs, Tuya responses, device identifiers or private images to public issues. Gitleaks scans Git history; exceptions cover only exact public protocol constants and a narrowly scoped synthetic test vector.

Use GitHub private vulnerability reporting when available. Otherwise open an issue without sensitive proof and agree on a private channel before sharing details.
