import logging
import sys
from pathlib import Path
import ctypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(threadName)s] %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_LOG_DIR / "app.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)

import tkinter as tk
from services.database_manager import DatabaseManager
from views.main_window import MainWindow


def main() -> None:
    logger.info("Program Start")
    try:
        db = DatabaseManager()
        root = tk.Tk()
        app = MainWindow(root, db)
        root.protocol("WM_DELETE_WINDOW", app.on_close)
        root.mainloop()
    except Exception:
        logger.exception("Fatal error in main")
        raise


if __name__ == "__main__":
    main()
