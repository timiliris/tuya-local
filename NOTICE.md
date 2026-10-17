# NOTICE — third-party code

This file is the attribution record required by the licenses of software
we vendor or statically link. Our own code is MIT; see LICENSE.

Nothing here implies endorsement by the original authors.

---

## 1. Vendored source — tuya-ipc-terminal

**Project:** [seydx/tuya-ipc-terminal](https://github.com/seydx/tuya-ipc-terminal)  
**Author:** seydx (https://github.com/seydx)  
**License:** MIT  
**Upstream commit we started from:** `d65b3e9babb4829176290b4d53195d62636f00bf`  
(“update version to 0.0.6”, 2025-05-31)  
**Location in this repo:** `vendor/tuya-ipc-terminal/`  
**Original license file:** `vendor/tuya-ipc-terminal/LICENSE`  
**Patch list:** `vendor/tuya-ipc-terminal/UPSTREAM.md`

```
MIT License

Copyright (c) 2025 seydx

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Python, desktop and media dependencies

The shared server uses Requests (Apache-2.0), qrcode (BSD-3-Clause), Pillow
(HPND), TinyTuya (MIT), NumPy (BSD-3-Clause) and OpenCV (Apache-2.0, with
third-party notices). The desktop shell uses Tauri (MIT or Apache-2.0).
Their original license texts accompany the packaged runtime where applicable.

Desktop builds freeze Python with PyInstaller and include an FFmpeg binary
from imageio-ffmpeg. FFmpeg is a separate executable; its exact version,
configuration, license statement and GPL/LGPL texts are retained in the
runtime's `licenses/` directory. Source and build provenance:
https://github.com/imageio/imageio-ffmpeg and https://ffmpeg.org/download.html.
Docker obtains FFmpeg and its license files from the Debian distribution.

The obsolete Tkinter GUI, bundled VLC and Inno Setup packaging have been
removed. The original MIT license for retained upstream code remains intact.
Historical documentation is linked from the project wiki's Archives page.

## OpenCV Zoo — NanoDet local object detection

The inference implementation in `vendor/nanodet/nanodet.py` is vendored from
[OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_nanodet),
commit `47534e27c9851bb1128ccc0102f1145e27f23f98`, under Apache License 2.0.
The complete license is preserved in `vendor/nanodet/LICENSE`.

The standard and quantized NanoDet ONNX weights from that same revision are
downloaded during Docker image or desktop package construction and verified by size and SHA-256
in `src/ai_models.py`. They are not stored in this Git repository.
The source repository distributes this model under Apache-2.0.
