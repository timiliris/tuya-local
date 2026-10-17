# Docker server

You need Docker Compose, a Tuya / Smart Life account that already contains the cameras, and connectivity between the server and the camera VLAN.

```sh
git clone https://github.com/timiliris/tuya-local.git
cd tuya-local
cp .env.example .env
```

Set `TUYA_HOST_IP` in `.env` to the Docker host's LAN IPv4 address, then start:

```sh
docker compose up -d --build
```

Open `http://127.0.0.1:18788/`, choose the account region and scan the QR code using Tuya / Smart Life.

The interface and RTSP output bind to localhost by default. WebRTC media ports bind to the configured private LAN address. Read [Network and privacy](Network-and-privacy) before changing access for another computer.

Account sessions and settings live in the `tuya_data` and `tuya_config` volumes. Keep these volumes during upgrades:

```sh
git pull --ff-only
docker compose up -d --build
```

One image contains the corrected Go engine, Python server, FFmpeg, AI models and web interface. No separate legacy bridge is required.

Web access protection and container hardening are included. Version 0.3.0 adds administrator and read-only accounts: use **Server settings → Users** before enabling LAN web access. Passwords configured through the interface are retained in the account data volume. See [Network and privacy](Network-and-privacy) for sessions, HTTPS, allowed hostnames and RTSP limits.
