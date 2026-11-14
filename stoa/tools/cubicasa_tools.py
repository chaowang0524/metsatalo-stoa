"""Floor plan symbols and rooms from the CubiCasa5K multi-task model.

Kalervo et al. 2019, https://github.com/CubiCasa/CubiCasa5k (CC BY-NC 4.0).
Clone that repo to ./CubiCasa5k and put model_best_val_loss_var.pkl in it.
One forward pass per drawing (4 rotations averaged, as in the authors'
notebook) gives room and icon segmentation plus junction heatmaps; the
authors' post-processing turns those into polygons, which we count. Results
are cached per drawing, so all questions about one plan share one pass.

Caveat: the weights were trained on CubiCasa5K, and 197 of the 241 preview
drawings are in its train split. Judge accuracy on test/val drawings only.
"""

import json
import sys
import threading
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2] / "CubiCasa5k"
WEIGHTS = REPO / "model_best_val_loss_var.pkl"
CACHE = Path("out/cubicasa_cache")
# Trained on F1_scaled images (median ~700x800); the post-processing also
# blows up combinatorially on big plans (a 4000 px one ran >10 min at 2048).
MAX_SIDE = 1024

ROOM_CLASSES = ["Background", "Outdoor", "Wall", "Kitchen", "Living Room", "Bed Room", "Bath",
                "Entry", "Railing", "Storage", "Garage", "Undefined"]
ICON_CLASSES = ["No Icon", "Window", "Door", "Closet", "Electrical Appliance", "Toilet", "Sink",
                "Sauna Bench", "Fire Place", "Bathtub", "Chimney"]
SPLIT = [21, 12, 11]  # heatmap, room and icon channels

_model = None
_device = None
_lock = threading.Lock()


def _load():
    global _model, _device
    if _model is None:
        import torch

        sys.path.insert(0, str(REPO))
        from floortrans.models.hg_furukawa_original import hg_furukawa_original

        # Not get_model(): its init_weights() loads an ImageNet start point we overwrite anyway.
        model = hg_furukawa_original(n_classes=51)
        n = sum(SPLIT)
        model.conv4_ = torch.nn.Conv2d(256, n, bias=True, kernel_size=1)
        model.upsample = torch.nn.ConvTranspose2d(n, n, kernel_size=4, stride=4)
        # weights_only: load tensors without running pickled code.
        ck = torch.load(WEIGHTS, map_location="cpu", weights_only=True)
        model.load_state_dict(ck["model_state"])
        _device = "mps" if torch.backends.mps.is_available() else "cpu"
        _model = model.eval().to(_device)

        # The post-processing indexes stats.mode(...).mode[0], written for the
        # old scipy that returned arrays; current scipy returns a scalar.
        import types

        from floortrans import post_prosessing
        from scipy import stats

        post_prosessing.stats = types.SimpleNamespace(
            mode=lambda a: types.SimpleNamespace(mode=np.atleast_1d(stats.mode(a).mode)))
    return _model


def _predict(path: Path):
    """Averaged network output over 4 rotations, shape (44, H, W), and the scale used."""
    import torch
    import torch.nn.functional as F

    img = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
    scale = min(1.0, MAX_SIDE / max(img.shape[:2]))
    if scale < 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    x = torch.tensor(np.moveaxis(img, -1, 0)[None].astype(np.float32))
    x = (2 * (x / 255.0) - 1).to(_device)

    from floortrans.loaders import RotateNTurns

    rot = RotateNTurns()
    preds = []
    with torch.no_grad():
        for fwd, back in [(0, 0), (1, -1), (2, 2), (-1, 1)]:
            p = _model(rot(x, "tensor", fwd))
            p = rot(rot(p, "tensor", back), "points", back)
            preds.append(F.interpolate(p, size=(h, w), mode="bilinear", align_corners=True).cpu())
    return torch.mean(torch.cat(preds), 0, True), (h, w), scale


def analyse(path: str | Path) -> dict:
    """Icons and rooms found on one floor plan, cached as JSON next to other outputs."""
    path = Path(path)
    cached = CACHE / f"{path.stem}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8-sig"))
    with _lock:  # one drawing at a time on the GPU
        _load()
        from floortrans.post_prosessing import get_polygons, split_prediction

        pred, size, scale = _predict(path)
        heatmaps, rooms, icons = split_prediction(pred, size, SPLIT)
        polygons, types, room_polygons, room_types = get_polygons((heatmaps, rooms, icons), 0.2, [1, 2])

    def bbox(poly) -> list[float]:
        # Icons are corner arrays, rooms shapely (Multi)Polygons.
        b = poly.bounds if hasattr(poly, "bounds") else (*np.min(poly, 0), *np.max(poly, 0))
        pts = np.asarray(b, float).reshape(2, 2) / scale  # back to original pixels
        return [round(v, 1) for v in (*pts.min(0), *pts.max(0))]

    out = {
        "file": path.name,
        "icons": [{"class": ICON_CLASSES[int(t["class"])], "bbox": bbox(p)}
                  for p, t in zip(polygons, types) if t["type"] == "icon"],
        # One (Multi)Polygon per room class: split it into separate rooms.
        "rooms": [{"class": ROOM_CLASSES[int(t["class"])], "bbox": bbox(g)}
                  for p, t in zip(room_polygons, room_types)
                  if ROOM_CLASSES[int(t["class"])] not in ("Background", "Wall", "Railing")
                  for g in getattr(p, "geoms", [p])],
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(out), encoding="utf-8")
    return out


def counts(path: str | Path) -> dict[str, int]:
    a = analyse(path)
    c: dict[str, int] = {}
    for kind in ("icons", "rooms"):
        for x in a[kind]:
            c[x["class"]] = c.get(x["class"], 0) + 1
    return c
