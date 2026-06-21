import logging
import numpy as np
import cv2
import pyautogui
from PIL import Image
from models.orb_config import OrbConfig

logger = logging.getLogger(__name__)

Board = list[list[str]]

FIRE  = "火"
WATER = "水"
WOOD  = "木"
EARTH = "土"
DARK  = "暗"
HEART = "心"
EMPTY = "?"

# OpenCV HSV: H=0-179, S=0-255, V=0-255
# Each entry: (orb_name, [(hue_lo, hue_hi), ...])
# Ranges chosen to avoid overlap; 心/火 both near red wrap-point,
# separated by 心 being higher-hue magenta (≥152) vs 火 low-hue red (≤12 or ≥170)
_ORB_HSV = [
    (FIRE,  [(0, 12), (170, 179)]),   # red — wraps around 179→0
    (EARTH, [(13, 38)]),              # orange-gold
    (WOOD,  [(45, 92)]),              # green
    (WATER, [(92, 128)]),             # blue
    (DARK,  [(128, 152)]),            # purple/violet
    (HEART, [(152, 170)]),            # pink/magenta
]

# Canvas preview colours (tkinter colour strings)
ORB_COLOR = {
    FIRE:  "#f87171",
    WATER: "#60a5fa",
    WOOD:  "#4ade80",
    EARTH: "#fbbf24",
    DARK:  "#c084fc",
    HEART: "#f472b6",
    EMPTY: "#334155",
}


class OrbBoard:
    def __init__(self, config: OrbConfig) -> None:
        self._cfg = config

    def capture(self) -> Image.Image:
        cfg = self._cfg
        return pyautogui.screenshot(region=(
            cfg.board_x, cfg.board_y,
            cfg.cols * cfg.cell_w,
            cfg.rows * cfg.cell_h,
        ))

    def recognize(self, image: Image.Image) -> Board:
        cfg = self._cfg
        arr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2HSV)
        board = []
        for r in range(cfg.rows):
            row = []
            for c in range(cfg.cols):
                # 35-65 % crop: avoids the reddish outer glow/border common in orb art
                x1 = int(c * cfg.cell_w + cfg.cell_w * 0.35)
                y1 = int(r * cfg.cell_h + cfg.cell_h * 0.35)
                x2 = int(c * cfg.cell_w + cfg.cell_w * 0.65)
                y2 = int(r * cfg.cell_h + cfg.cell_h * 0.65)
                cell = arr[y1:y2, x1:x2]
                row.append(self._classify(cell))
            board.append(row)
        return board

    def snapshot(self) -> Board:
        return self.recognize(self.capture())

    def _classify(self, cell_hsv: np.ndarray) -> str:
        # Exclude white highlights (S too low) and shadows (V out of range)
        mask = (
            (cell_hsv[:, :, 1] > 100) &
            (cell_hsv[:, :, 2] > 45)  &
            (cell_hsv[:, :, 2] < 235)
        )
        if not np.any(mask):
            return EMPTY

        hues = cell_hsv[:, :, 0][mask]
        sats = cell_hsv[:, :, 1][mask].astype(float)

        # Score each orb type by total saturation of pixels that fall within its hue range.
        # More saturated pixels = stronger vote; whichever orb captures the most wins.
        best_name  = EMPTY
        best_score = 0.0
        for orb, ranges in _ORB_HSV:
            score = 0.0
            for lo, hi in ranges:
                in_range = (hues >= lo) & (hues <= hi)
                score += float(sats[in_range].sum())
            if score > best_score:
                best_score = score
                best_name  = orb

        if best_name == EMPTY:
            logger.debug("Unclassified cell — no hue range matched (S-sum=0)")
        return best_name
