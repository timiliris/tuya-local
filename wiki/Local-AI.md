# Local AI

The server performs analysis and the browser receives results. Analysis does not require an open browser, subject to each camera's selected operating mode.

- **Disabled:** no analysis for that camera.
- **Follow an existing player:** passively analyze a local stream opened by VLC or the integrated viewer. Stop with the last viewer; do not wake the camera for AI.
- **Continuous analysis:** the server opens its own stream for a powered camera. Recognized battery models always use passive analysis; missing capabilities are handled conservatively.

NanoDet standard and compact are bundled during image/package construction. Model downloads use a fixed OpenCV Zoo revision and verified checksums. Inference does not call an external service.

Per-camera filters include object classes, minimum confidence, object area, analysis region, a 1–5 second interval, successive confirmations and a similar-event cooldown. Scores are model predictions, not guarantees of accuracy. The system does not identify people.

The AI page shows worker states, recent results, video overlays and observations grouped by camera and object class. History is limited to seven days and 500 observations. Optional thumbnails are stored in the local event database.

AI settings, the database and its thumbnails are excluded from Git and Docker build inputs. Video readers and inference resources are bounded and released when disabled.
