# Cameras, playback and settings

## Playback

The viewer prefers local WebRTC. When the browser cannot decode H.265 HD, local H.264 conversion provides a fallback. SD uses fewer resources. VLC can open each camera's HD/SD RTSP URL; prefer RTSP over TCP.

## Sentinel display

Select cameras and choose a fullscreen grid or timed rotation. Screen wake lock is used when supported. Playback reconnects interrupted streams. Continuous viewing uses battery power on solar/battery cameras.

## Camera settings

The settings panel exposes only recognized typed controls from each camera's schema: image, detection, recording, battery and storage, depending on the model.

Edit controls and save the batch. The server can wake a battery camera before sending changes and then read back Tuya's reported values. Confirmation describes the reported state, not an independent physical check.

A phone-app change made during wake-up is not silently overwritten. Privacy mode is not bypassed. PTZ and settings can use Tuya's cloud; they do not require cloud video playback.
