# Troubleshooting

## No cameras after login

Check the Tuya / Smart Life account region and whether the phone app can see the camera. Discovery retains sleeping cameras even when some capabilities are temporarily unavailable.

## Slow or rejected wake-up

A solar camera may need several attempts after a long sleep. The engine uses bounded retries. Closing other viewers can release an occupied connection. Diagnostics distinguish wake-up, connection and first video; they do not establish universal reliability across all firmware versions.

## Black HD video

Try SD and compatibility playback. H.265 HD depends on browser decoder support; fallback conversion is local H.264. For VLC, use RTSP over TCP.

## AI is waiting

Passive mode waits for an existing local viewer. It does not automatically follow a phone-only Tuya session. Choose continuous analysis for a powered camera if analysis must run without a viewer.

## Desktop server unavailable

Check the root URL and network access. A desktop build without bundled runtime resources can connect to an existing server but cannot start its own server. Prepare runtime resources before the final build.

## After updating

Rebuild Docker or install the new native package, then reload the interface. Do not delete account volumes or the application data directory to resolve a display issue.
