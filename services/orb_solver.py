import logging
from collections import Counter
from models.orb_config import OrbConfig
from services.orb_board import Board, EMPTY

logger = logging.getLogger(__name__)


def _drop(b: Board) -> None:
    """Gravity: orbs fall down to fill empty cells."""
    rows = len(b)
    cols = len(b[0])
    for c in range(cols):
        filled = [b[r][c] for r in range(rows) if b[r][c] != EMPTY]
        empties = rows - len(filled)
        for r in range(rows):
            b[r][c] = EMPTY if r < empties else filled[r - empties]


def score_board(board: Board) -> int:
    """Count combos that would resolve from this board state."""
    rows = len(board)
    cols = len(board[0])
    total = 0
    b = [row[:] for row in board]
    while True:
        matched: set[tuple[int, int]] = set()
        for r in range(rows):
            c = 0
            while c < cols - 2:
                if b[r][c] != EMPTY and b[r][c] == b[r][c+1] == b[r][c+2]:
                    k = c
                    while k < cols and b[r][k] == b[r][c]:
                        matched.add((r, k)); k += 1
                    c = k
                else:
                    c += 1
        for c in range(cols):
            r = 0
            while r < rows - 2:
                if b[r][c] != EMPTY and b[r][c] == b[r+1][c] == b[r+2][c]:
                    k = r
                    while k < rows and b[k][c] == b[r][c]:
                        matched.add((k, c)); k += 1
                    r = k
                else:
                    r += 1
        if not matched:
            break
        color_counts = Counter(b[r][c] for r, c in matched)
        # 6+ same color in one wave scores double
        total += sum(2 if n >= 6 else 1 for n in color_counts.values())
        for r, c in matched:
            b[r][c] = EMPTY
        _drop(b)
    return total


def _move(board: Board, fpos: tuple, tpos: tuple, held: str) -> Board:
    """Move held orb from fpos to tpos. Returns new board copy."""
    b = [row[:] for row in board]
    fr, fc = fpos
    tr, tc = tpos
    b[fr][fc] = b[tr][tc]   # orb at destination fills old position
    b[tr][tc] = held         # held orb is now at new position
    return b


class OrbSolver:
    def __init__(self, config: OrbConfig) -> None:
        self._cfg = config

    def solve(self, board: Board) -> tuple[list[tuple[int, int]], int]:
        rows = len(board)
        cols = len(board[0]) if board else 6
        beam_width = max(1, self._cfg.beam_width)
        max_steps  = max(1, self._cfg.max_steps)

        best_score = -1
        best_path: list[tuple[int, int]] = []

        for sr in range(rows):
            for sc in range(cols):
                held = board[sr][sc]
                init = [row[:] for row in board]

                # beam state: (board_snapshot, current_pos, held, path)
                beams: list[tuple[Board, tuple, str, list]] = [
                    (init, (sr, sc), held, [(sr, sc)])
                ]

                for _ in range(max_steps):
                    candidates = []
                    for brd, pos, h, path in beams:
                        pr, pc = pos
                        prev = path[-2] if len(path) >= 2 else None
                        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                            nr, nc = pr + dr, pc + dc
                            if not (0 <= nr < rows and 0 <= nc < cols):
                                continue
                            if prev and (nr, nc) == prev:
                                continue  # no immediate backtrack
                            nb = _move(brd, pos, (nr, nc), h)
                            s = score_board(nb)
                            candidates.append(
                                (s, nb, (nr, nc), h, path + [(nr, nc)])
                            )
                    if not candidates:
                        break
                    candidates.sort(key=lambda x: x[0], reverse=True)
                    beams = [
                        (c[1], c[2], c[3], c[4])
                        for c in candidates[:beam_width]
                    ]

                if beams:
                    top_brd, top_pos, top_held, top_path = beams[0]
                    s = score_board(top_brd)
                    if s > best_score:
                        best_score = s
                        best_path = top_path

        predicted = max(best_score, 0)
        logger.info("Solver: %d steps, predicted %d combo(s)", len(best_path), predicted)
        return best_path, predicted
