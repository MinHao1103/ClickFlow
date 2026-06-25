import logging
import numpy as np
import cv2
import pyautogui
from PIL import Image
from models.orb_config import OrbConfig

try:
    import mss as _mss_mod
    _HAS_MSS = True
except ImportError:
    _HAS_MSS = False

logger = logging.getLogger(__name__)

Board = list[list[str]]

FIRE  = "火"
WATER = "水"
WOOD  = "木"
LIGHT = "光"
DARK  = "暗"
HEART = "心"
EMPTY = "?"

# OpenCV HSV: H=0-179, S=0-255, V=0-255
# Each entry: (orb_name, [(hue_lo, hue_hi), ...])
# Ranges chosen to avoid overlap; 心/火 both near red wrap-point,
# separated by 心 being higher-hue magenta (≥152) vs 火 low-hue red (≤12 or ≥170)
_ORB_HSV = [
    (FIRE,  [(0, 12), (170, 179)]),   # red — wraps around 179→0
    (LIGHT, [(13, 44)]),              # orange-gold; extended to 44 to close gap with WOOD
    (WOOD,  [(44, 92)]),              # green
    (WATER, [(92, 128)]),             # blue
    (DARK,  [(128, 152)]),            # purple/violet
    (HEART, [(152, 170)]),            # pink/magenta
]

# Canvas preview colours (tkinter colour strings)
ORB_COLOR = {
    FIRE:  "#f87171",
    WATER: "#60a5fa",
    WOOD:  "#4ade80",
    LIGHT: "#fbbf24",
    DARK:  "#c084fc",
    HEART: "#f472b6",
    EMPTY: "#334155",
}


class OrbBoard:
    def __init__(self, config: OrbConfig) -> None:
        self._cfg = config

    def capture(self) -> Image.Image:
        cfg = self._cfg
        region = {
            "left": cfg.board_x, "top": cfg.board_y,
            "width": cfg.cols * cfg.cell_w, "height": cfg.rows * cfg.cell_h,
        }
        if _HAS_MSS:
            with _mss_mod.MSS() as sct:
                shot = sct.grab(region)
                return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return pyautogui.screenshot(region=(
            cfg.board_x, cfg.board_y,
            cfg.cols * cfg.cell_w, cfg.rows * cfg.cell_h,
        ))

    def recognize(self, image: Image.Image) -> Board:
        cfg = self._cfg
        arr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2HSV)

        # 35-65 % crop bounds (same for every cell)
        cx0 = int(cfg.cell_w * 0.35); cx1 = int(cfg.cell_w * 0.65)
        cy0 = int(cfg.cell_h * 0.35); cy1 = int(cfg.cell_h * 0.65)
        n_cells = cfg.rows * cfg.cols

        # Stack all cell centre crops: (n_cells, crop_h, crop_w, 3)
        crops = np.stack([
            arr[r * cfg.cell_h + cy0 : r * cfg.cell_h + cy1,
                c * cfg.cell_w + cx0 : c * cfg.cell_w + cx1]
            for r in range(cfg.rows)
            for c in range(cfg.cols)
        ])

        h = crops[:, :, :, 0].astype(np.int32)    # hue   (N, H, W)
        s = crops[:, :, :, 1].astype(np.float32)  # sat
        v = crops[:, :, :, 2]                      # val
        mask = (s > 100) & (v > 45) & (v < 235)   # exclude highlights/shadows

        # Saturation-weighted vote per orb type across all cells in one pass
        n_orbs = len(_ORB_HSV)
        scores = np.zeros((n_cells, n_orbs), dtype=np.float32)
        for j, (_, ranges) in enumerate(_ORB_HSV):
            in_hue = np.zeros((n_cells, crops.shape[1], crops.shape[2]), dtype=bool)
            for lo, hi in ranges:
                in_hue |= (h >= lo) & (h <= hi)
            scores[:, j] = (s * (mask & in_hue)).sum(axis=(1, 2))

        orb_names = [orb for orb, _ in _ORB_HSV]
        best = np.argmax(scores, axis=1)
        has_match = scores.max(axis=1) > 0

        flat = [orb_names[best[i]] if has_match[i] else EMPTY for i in range(n_cells)]
        return [flat[r * cfg.cols : (r + 1) * cfg.cols] for r in range(cfg.rows)]

    def snapshot(self) -> Board:
        return self.recognize(self.capture())
