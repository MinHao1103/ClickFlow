import heapq
import time
import logging
from models.orb_config import OrbConfig
from services.orb_board import Board, EMPTY

logger = logging.getLogger(__name__)

# Per-solve score cache: board_state_key → combo_count.
# Cleared at the start of each solve() call. Many beam paths converge to
# identical board states; caching eliminates redundant score_board() calls.
_score_cache: dict = {}


def _drop(b: Board) -> None:
    """Gravity: orbs fall down to fill empty cells."""
    rows = len(b)
    cols = len(b[0])
    for c in range(cols):
        filled = [b[r][c] for r in range(rows) if b[r][c] != EMPTY]
        empties = rows - len(filled)
        for r in range(rows):
            b[r][c] = EMPTY if r < empties else filled[r - empties]


def _score_board_uncached(board: Board) -> int:
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
        # Count same-colour connected groups (BFS): each group = 1 combo.
        # Two separate 3-orb groups score 2; one 6-orb chain scores 1.
        seen: set[tuple[int, int]] = set()
        for pos in matched:
            if pos in seen:
                continue
            color = b[pos[0]][pos[1]]
            seen.add(pos)
            total += 1
            stack = [pos]
            while stack:
                pr, pc = stack.pop()
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nb = (pr + dr, pc + dc)
                    if nb in matched and nb not in seen and b[nb[0]][nb[1]] == color:
                        seen.add(nb)
                        stack.append(nb)
        for r, c in matched:
            b[r][c] = EMPTY
        _drop(b)
    return total


def score_board(board: Board) -> int:
    """Count combos; results are cached per solve() call by board state."""
    key = tuple(tuple(row) for row in board)
    if key not in _score_cache:
        _score_cache[key] = _score_board_uncached(board)
    return _score_cache[key]


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
    # path stored as tuple for fast immutable concatenation
    beams: list[tuple] = [(init, (sr, sc), held, ((sr, sc),))]

    best_score = 0
    best_path: tuple = ((sr, sc),)

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
                candidates.append((s, nb, (nr, nc), h, path + ((nr, nc),)))
        if not candidates:
            break
        # heapq.nlargest is O(N log K) vs sort's O(N log N) — faster when N >> beam_width
        top = heapq.nlargest(beam_width, candidates, key=lambda x: x[0])
        if top[0][0] > best_score:
            best_score = top[0][0]
            best_path = top[0][4]
        beams = [(c[1], c[2], c[3], c[4]) for c in top]

    return best_score, list(best_path)


class OrbSolver:
    def __init__(self, config: OrbConfig) -> None:
        self._cfg = config

    def solve(self, board: Board, time_limit: float = 12.0) -> tuple[list[tuple[int, int]], int]:
        """Multi-pass beam search. Uses time_limit seconds, then returns best found.

        Strategy:
          Pass 1 — base beam_width, all non-EMPTY starts: fast baseline survey.
          Pass 2 — 6×  wider beam, all starts sorted by pass-1 score.
          Pass 3 — 20× wider beam, top-10 starts only.
          Pass 4 — 40× wider beam, top-5  starts only (deep refinement).
        Passes stop early if deadline is reached.
        """
        _score_cache.clear()

        rows = len(board)
        cols = len(board[0]) if board else 6
        base_bw = max(1, self._cfg.beam_width)
        max_steps = max(1, self._cfg.max_steps)
        deadline = time.time() + time_limit

        all_starts = [(r, c) for r in range(rows) for c in range(cols) if board[r][c] != EMPTY]
        best_score = -1
        best_path: list = []
        start_scores: list[tuple[int, int, int]] = []  # (score, sr, sc)

        # ── Pass 1: quick survey over all starts ─────────────────────────
        for sr, sc in all_starts:
            if time.time() >= deadline:
                break
            s, path = _beam_search(board, sr, sc, base_bw, max_steps, deadline)
            start_scores.append((s, sr, sc))
            if s > best_score:
                best_score = s
                best_path = path

        logger.info("Solver pass1: best=%d in %.2fs", best_score, time_limit - (deadline - time.time()))

        # Sort by pass-1 score — best starts get the deep passes
        start_scores.sort(reverse=True)

        # ── Pass 2-4: wider beams on progressively narrower start sets ────
        for multiplier, top_n in ((6, len(start_scores)), (20, 10), (40, 5)):
            if time.time() >= deadline:
                break
            wider = base_bw * multiplier
            for _, sr, sc in start_scores[:top_n]:
                if time.time() >= deadline:
                    break
                s, path = _beam_search(board, sr, sc, wider, max_steps, deadline)
                if s > best_score:
                    best_score = s
                    best_path = path

        predicted = max(best_score, 0)
        elapsed = time_limit - max(deadline - time.time(), 0)
        logger.info("Solver done: %d steps, %d combo(s), %.2fs used",
                    len(best_path), predicted, elapsed)
        return best_path, predicted
