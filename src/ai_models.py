"""Pinned OpenCV Zoo weights; downloaded at build time, never during inference."""
from __future__ import annotations
import hashlib
from pathlib import Path

REVISION = '47534e27c9851bb1128ccc0102f1145e27f23f98'
MODELS = {
    'nanodet': {'name': 'NanoDet standard', 'file': 'nanodet.onnx', 'size': 3800954,
                'upstream': 'object_detection_nanodet_2022nov.onnx',
                'sha256': '4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186'},
    'nanodet-int8': {'name': 'NanoDet compact', 'file': 'nanodet-int8.onnx', 'size': 1031424,
                     'upstream': 'object_detection_nanodet_2022nov_int8.onnx',
                     'sha256': '8dd32b85f2d273e9047f1d6b59e0b2fd008b1076338107bb547ac28942cdf90b'},
}
CLASSES = {
    'person': (0, 'Personne'), 'bicycle': (1, 'Vélo'), 'car': (2, 'Voiture'),
    'motorcycle': (3, 'Moto'), 'bus': (5, 'Bus'), 'truck': (7, 'Camion'),
    'bird': (14, 'Oiseau'), 'cat': (15, 'Chat'), 'dog': (16, 'Chien'),
    'horse': (17, 'Cheval'), 'sheep': (18, 'Mouton'), 'cow': (19, 'Vache'),
}


def download(destination):
    import requests
    destination.mkdir(parents=True, exist_ok=True)
    for model in MODELS.values():
        url = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{REVISION}/models/object_detection_nanodet/{model['upstream']}"
        response = requests.get(url, timeout=90)
        response.raise_for_status()
        data = response.content
        if len(data) != model['size'] or hashlib.sha256(data).hexdigest() != model['sha256']:
            raise RuntimeError('AI model checksum mismatch')
        (destination / model['file']).write_bytes(data)


class Detector:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.networks = {}

    def infer(self, jpeg, model):
        import importlib.util
        import io
        import cv2
        import numpy as np
        from PIL import Image
        if model not in self.networks:
            source = Path(__file__).resolve().parents[1] / 'vendor/nanodet/nanodet.py'
            spec = importlib.util.spec_from_file_location('tuya_nanodet', source)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            cv2.setNumThreads(2)
            self.networks[model] = module.NanoDet(str(self.directory / MODELS[model]['file']), prob_threshold=.20)
        with Image.open(io.BytesIO(jpeg)) as image:
            rgb = np.array(image.convert('RGB'))
        height, width = rgb.shape[:2]
        factor = min(416 / width, 416 / height)
        scaled_w, scaled_h = round(width * factor), round(height * factor)
        left, top = (416 - scaled_w) // 2, (416 - scaled_h) // 2
        square = np.zeros((416, 416, 3), dtype=np.uint8)
        square[top:top+scaled_h, left:left+scaled_w] = cv2.resize(rgb[:, :, ::-1], (scaled_w, scaled_h))
        predictions = self.networks[model].infer(square)
        names = {data[0]: name for name, data in CLASSES.items()}
        results = []
        for x1, y1, x2, y2, score, class_id in predictions:
            if int(class_id) not in names:
                continue
            box = [max(0., min(1., (float(x1)-left)/scaled_w)), max(0., min(1., (float(y1)-top)/scaled_h)),
                   max(0., min(1., (float(x2)-left)/scaled_w)), max(0., min(1., (float(y2)-top)/scaled_h))]
            if box[2] > box[0] and box[3] > box[1]:
                results.append({'class': names[int(class_id)], 'score': round(float(score), 4), 'box': box})
        return results


if __name__ == '__main__':
    import sys
    download(Path(sys.argv[1]))
