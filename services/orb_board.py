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
_ORB_HSV = [
    (FIRE,  [(0, 12), (168, 179)]),   # red wraps around 179→0
    (EARTH, [(13, 32)]),              # orange-gold
    (WOOD,  [(50, 85)]),              # green
    (WATER, [(95, 130)]),             # blue
    (DARK,  [(130, 153)]),            # purple
    (HEART, [(153, 172)]),            # pink/magenta
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
                x1 = int(c * cfg.cell_w + cfg.cell_w * 0.2)
                y1 = int(r * cfg.cell_h + cfg.cell_h * 0.2)
                x2 = int(c * cfg.cell_w + cfg.cell_w * 0.8)
                y2 = int(r * cfg.cell_h + cfg.cell_h * 0.8)
                cell = arr[y1:y2, x1:x2]
                row.append(self._classify(cell))
            board.append(row)
        return board

    def snapshot(self) -> Board:
        return self.recognize(self.capture())

    def _classify(self, cell_hsv: np.ndarray) -> str:
        mask = cell_hsv[:, :, 1] > 50        # ignore low-saturation pixels
        if not np.any(mask):
            return EMPTY
        hues = cell_hsv[:, :, 0][mask].astype(float)
        avg = float(np.median(hues))
        for name, ranges in _ORB_HSV:
            for lo, hi in ranges:
                if lo <= avg <= hi:
                    return name
        logger.debug("Unclassified hue: %.1f", avg)
        return EMPTY
