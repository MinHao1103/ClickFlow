import time
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
    b[fr][fc] = b[tr][tc]
    b[tr][tc] = held
    return b


def _beam_search(
    board: Board,
    sr: int,
    sc: int,
    beam_width: int,
    max_steps: int,
    deadline: float,
) -> tuple[int, list]:
    """Beam search from starting cell (sr, sc).
    Returns (best_score, best_path) found before deadline."""
    rows = len(board)
    cols = len(board[0])
    held = board[sr][sc]
    init = [row[:] for row in board]
    beams: list[tuple] = [(init, (sr, sc), held, [(sr, sc)])]

    best_score = 0
    best_path: list = [(sr, sc)]

    for _ in range(max_steps):
        if time.time() >= deadline:
            break
        candidates = []
        for brd, pos, h, path in beams:
            pr, pc = pos
            prev = path[-2] if len(path) >= 2 else None
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = pr + dr, pc + dc
                if not (0 <= nr < rows and 0 <= nc < cols):
                    continue
                if prev and (nr, nc) == prev:
                    continue
                nb = _move(brd, pos, (nr, nc), h)
                s = score_board(nb)
                candidates.append((s, nb, (nr, nc), h, path + [(nr, nc)]))
        if not candidates:
            break
        candidates.sort(key=lambda x: x[0], reverse=True)
        if candidates[0][0] > best_score:
            best_score = candidates[0][0]
            best_path = candidates[0][4]
        beams = [(c[1], c[2], c[3], c[4]) for c in candidates[:beam_width]]

    return best_score, best_path


class OrbSolver:
    def __init__(self, config: OrbConfig) -> None:
        self._cfg = config

    def solve(self, board: Board, time_limit: float = 8.0) -> tuple[list[tuple[int, int]], int]:
        """Multi-pass beam search. Uses time_limit seconds, then returns best found.

        Strategy:
          Pass 1 — beam_width from config, all 30 starts: fast baseline survey.
          Pass 2 — 4× wider beam, starts sorted by pass-1 score (best first).
          Pass 3 — 10× wider beam, remaining time.
        """
        rows = len(board)
        cols = len(board[0]) if board else 6
        base_bw = max(1, self._cfg.beam_width)
        max_steps = max(1, self._cfg.max_steps)
        deadline = time.time() + time_limit

        all_starts = [(r, c) for r in range(rows) for c in range(cols)]
        best_score = -1
        best_path: list = []
        start_scores: list[tuple[int, int, int]] = []  # (score, sr, sc)

        # ── Pass 1: quick survey ──────────────────────────────────────────
        for sr, sc in all_starts:
            if time.time() >= deadline:
                break
            s, path = _beam_search(board, sr, sc, base_bw, max_steps, deadline)
            start_scores.append((s, sr, sc))
            if s > best_score:
                best_score = s
                best_path = path

        logger.info("Solver pass1: best=%d in %.2fs", best_score, time_limit - (deadline - time.time()))

        # Sort starts by pass-1 score so subsequent passes focus on best first
        start_scores.sort(reverse=True)

        # ── Pass 2 & 3: progressively wider beams, time-bounded ──────────
        for multiplier in (4, 10):
            wider = base_bw * multiplier
            for _, sr, sc in start_scores:
                if time.time() >= deadline:
                    break
                s, path = _beam_search(board, sr, sc, wider, max_steps, deadline)
                if s > best_score:
                    best_score = s
                    best_path = path
            if time.time() >= deadline:
                break

        predicted = max(best_score, 0)
        elapsed = time_limit - max(deadline - time.time(), 0)
        logger.info("Solver done: %d steps, %d combo(s), %.2fs used",
                    len(best_path), predicted, elapsed)
        return best_path, predicted
