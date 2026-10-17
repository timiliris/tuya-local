# Desktop application

The Tauri v2 app targets macOS, Windows and Linux. It reuses the server's interface and video features.

## Built-in local server

Choose **Start and open** to run the bundled server without Docker. Python, FFmpeg, the Go engine and AI models are included in the native package. Account data stays in the application data directory, separate from Docker volumes.

The local HTTP and RTSP endpoints use dedicated loopback ports, allowing a Docker installation to remain running. Closing the viewer does not stop the local server. The application menu can reopen the launcher. **Stop local server** or quitting the application stops only its own processes.

## Existing server

Enter the root URL of an existing Tuya Local server: Docker on this computer or a server on another machine. The app checks the API before opening the viewer.

Server dashboards do not receive native Tauri capabilities. HTTP is accepted for localhost and private IP addresses; public addresses require HTTPS. The server has no built-in web login: restrict access to a trusted or protected private network. Public video relay candidates remain rejected.

## Build from source

Install the [Tauri v2 prerequisites](https://v2.tauri.app/start/prerequisites/), Python, Go and Node.js. Use an isolated Python environment:

```sh
python -m venv .venv
# Activate this environment using the appropriate command for your OS.
pip install -r desktop/requirements-build.txt
python scripts/build_desktop_server.py
cd desktop
npm ci
npm run build
```

Build natively for each operating system and architecture. Development builds are not signed or notarized releases. Native CI builds exercise the frozen runtime before packaging.
