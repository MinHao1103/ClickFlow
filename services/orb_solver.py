import heapq
import time
import logging
from models.orb_config import OrbConfig
from services.orb_board import Board, EMPTY

logger = logging.getLogger(__name__)

# Per-solve score cache: board_state_flat_tuple → combo_count.
# Cleared at the start of each solve() call. Many beam paths converge to
# identical board states; caching eliminates redundant score_board() calls.
_score_cache: dict = {}


def _score_board_uncached(board: tuple, rows: int, cols: int) -> int:
    # Convert flat board tuple to 2D list for match/elimination gravity calculation
    b = [list(board[r*cols : (r+1)*cols]) for r in range(rows)]
    total = 0
    while True:
        matched_flat = [False] * (rows * cols)
        any_match = False
        
        # Horizontal check
        for r in range(rows):
            offset = r * cols
            c = 0
            while c < cols - 2:
                val = b[r][c]
                if val != EMPTY and val == b[r][c+1] == b[r][c+2]:
                    k = c
                    while k < cols and b[r][k] == val:
                        matched_flat[offset + k] = True
                        k += 1
                    any_match = True
                    c = k
                else:
                    c += 1
                    
        # Vertical check
        for c in range(cols):
            r = 0
            while r < rows - 2:
                val = b[r][c]
                if val != EMPTY and val == b[r+1][c] == b[r+2][c]:
                    k = r
                    while k < rows and b[k][c] == val:
                        matched_flat[k * cols + c] = True
                        k += 1
                    any_match = True
                    r = k
                else:
                    r += 1
                    
        if not any_match:
            break
            
        # BFS group counting using flat indices and flat seen array
        seen_flat = [False] * (rows * cols)
        for r in range(rows):
            offset = r * cols
            for c in range(cols):
                idx = offset + c
                if matched_flat[idx] and not seen_flat[idx]:
                    color = b[r][c]
                    seen_flat[idx] = True
                    total += 1
                    stack = [(r, c)]
                    while stack:
                        pr, pc = stack.pop()
                        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                            nr, nc = pr + dr, pc + dc
                            if 0 <= nr < rows and 0 <= nc < cols:
                                n_idx = nr * cols + nc
                                if matched_flat[n_idx] and not seen_flat[n_idx] and b[nr][nc] == color:
                                    seen_flat[n_idx] = True
                                    stack.append((nr, nc))
                                    
        # Perform elimination & drop
        for c in range(cols):
            filled = []
            for r in range(rows):
                idx = r * cols + c
                if matched_flat[idx]:
                    b[r][c] = EMPTY
                if b[r][c] != EMPTY:
                    filled.append(b[r][c])
            empties = rows - len(filled)
            for r in range(rows):
                b[r][c] = EMPTY if r < empties else filled[r - empties]
                
    return total


def score_board(board: tuple, rows: int, cols: int) -> int:
    """Count combos; results are cached per solve() call by flat board state."""
    if board not in _score_cache:
        _score_cache[board] = _score_board_uncached(board, rows, cols)
    return _score_cache[board]


def _move_flat(board: tuple, fidx: int, tidx: int) -> tuple:
    """Move held orb from fidx to tidx in the flat board representation."""
    b = list(board)
    b[fidx], b[tidx] = b[tidx], b[fidx]
    return tuple(b)


def _beam_search(
    board: tuple,
    sr: int,
    sc: int,
    rows: int,
    cols: int,
    beam_width: int,
    max_steps: int,
    deadline: float,
) -> tuple[int, list[tuple[int, int]]]:
    """Beam search starting from (sr, sc) using optimized flat board representations."""
    sidx = sr * cols + sc
    # beams elements: (flat_board_tuple, current_index, path_tuple)
    beams = [(board, sidx, (sidx,))]
    best_score = 0
    best_path = (sidx,)
    
    for _ in range(max_steps):
        if time.time() >= deadline:
            break
        candidates = []
        visited = set()
        for brd, idx, path in beams:
            pr, pc = idx // cols, idx % cols
            prev = path[-2] if len(path) >= 2 else None
            
            # Moves: Up, Down, Left, Right
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = pr + dr, pc + dc
                if 0 <= nr < rows and 0 <= nc < cols:
                    nidx = nr * cols + nc
                    if prev is not None and nidx == prev:
                        continue
                    nb = _move_flat(brd, idx, nidx)
                    
                    # Duplicate state pruning: board state + position
                    state_key = (nb, nidx)
                    if state_key in visited:
                        continue
                    visited.add(state_key)
                    
                    s = score_board(nb, rows, cols)
                    candidates.append((s, nb, nidx, path + (nidx,)))
                    
        if not candidates:
            break
        # heapq.nlargest is O(N log K) vs sort's O(N log N) — faster when N >> beam_width
        top = heapq.nlargest(beam_width, candidates, key=lambda x: x[0])
        if top[0][0] > best_score:
            best_score = top[0][0]
            best_path = top[0][3]
        beams = [(c[1], c[2], c[3]) for c in top]
        
    return best_score, [(idx // cols, idx % cols) for idx in best_path]


class OrbSolver:
    def __init__(self, config: OrbConfig) -> None:
        self._cfg = config

    def solve(self, board: Board, time_limit: float = 12.0) -> tuple[list[tuple[int, int]], int]:
        """Multi-pass beam search using optimized flat tuple representations.
        Uses time_limit seconds, then returns the best path found.

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

        # Convert 2D list board to a flat tuple for fast hash/move operations
        board_flat = tuple(cell for row in board for cell in row)

        all_starts = [(r, c) for r in range(rows) for c in range(cols) if board[r][c] != EMPTY]
        best_score = -1
        best_path: list = []
        start_scores: list[tuple[int, int, int]] = []  # (score, sr, sc)

        # ── Pass 1: quick survey over all starts ─────────────────────────
        for sr, sc in all_starts:
            if time.time() >= deadline:
                break
            s, path = _beam_search(board_flat, sr, sc, rows, cols, base_bw, max_steps, deadline)
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
                s, path = _beam_search(board_flat, sr, sc, rows, cols, wider, max_steps, deadline)
                if s > best_score:
                    best_score = s
                    best_path = path

        predicted = max(best_score, 0)
        elapsed = time_limit - max(deadline - time.time(), 0)
        logger.info("Solver done: %d steps, %d combo(s), %.2fs used",
                    len(best_path), predicted, elapsed)
        return best_path, predicted
