from dataclasses import dataclass, field


@dataclass
class OrbConfig:
    name:          str
    board_x:       int = 0
    board_y:       int = 0
    cell_w:        int = 0
    cell_h:        int = 0
    rows:          int = 5
    cols:          int = 6
    drag_speed_ms: int = 60
    beam_width:    int = 50
    max_steps:     int = 50
    db_id:         int = None
