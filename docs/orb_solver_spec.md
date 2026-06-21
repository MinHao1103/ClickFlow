# 自動轉珠功能開發規範

> 適用專案：ClickFlow  
> 功能模組：摩靈 / 神魔之塔系列遊戲自動轉珠  
> 文件版本：1.0.0  
> 最後更新：2026-06-21

---

## 目錄

1. [功能範圍](#1-功能範圍)
2. [架構設計](#2-架構設計)
3. [資料結構規範](#3-資料結構規範)
4. [模組規範](#4-模組規範)
5. [演算法規範](#5-演算法規範)
6. [UI 規範](#6-ui-規範)
7. [新增動作類型：drag](#7-新增動作類型drag)
8. [測試規範](#8-測試規範)
9. [開發里程碑](#9-開發里程碑)
10. [限制與排除事項](#10-限制與排除事項)

---

## 1. 功能範圍

### 1.1 本次實作範圍（In Scope）

| 功能 | 說明 |
|------|------|
| 盤面框選校準 | 使用者拖曳選取遊戲盤面區域，儲存格子尺寸與原點 |
| 盤面顏色辨識 | 截圖後對每格取平均 HSV 值，對應珠色類型 |
| 轉珠路線求解 | Beam Search 演算法，最大化 combo 數 |
| 自動拖曳執行 | mouseDown → moveTo 路徑 → mouseUp 模擬手指拖動 |
| `drag` 動作類型 | ClickFlow 通用拖曳步驟（非轉珠專用） |
| 設定儲存 | 盤面校準資料存入 `clicker.db` |

### 1.2 排除範圍（Out of Scope）

- AI 學習最優策略（非 Beam Search 以外的 ML 方案）
- 自動識別回合開始/結束（需使用者手動觸發）
- 多種遊戲自動切換
- 行動裝置直接控制（本版本僅支援 PC 模擬器）

---

## 2. 架構設計

### 2.1 新增模組位置

```
ClickFlow/
├── services/
│   ├── orb_board.py        ← 盤面辨識（截圖 → 二維陣列）
│   ├── orb_solver.py       ← Beam Search 路線求解
│   └── orb_executor.py     ← 路線轉換為滑鼠拖曳指令
├── views/
│   └── orb_calibrate.py    ← 盤面校準 Toplevel 視窗
└── models/
    └── orb_config.py       ← 校準設定 dataclass
```

### 2.2 資料流

```
[使用者按下執行]
       │
       ▼
OrbBoard.capture()          # 截取盤面區域
       │  傳回 screenshot PIL.Image
       ▼
OrbBoard.recognize(image)   # 辨識每格珠色
       │  傳回 Board (2D list of OrbType)
       ▼
OrbSolver.solve(board)      # Beam Search 求解
       │  傳回 List[Tuple[int,int]]  (row,col 路徑)
       ▼
OrbExecutor.run(path)       # 轉換座標並拖曳
       │
       ▼
[完成，等待下一回合]
```

### 2.3 與現有架構的邊界

- `services/orb_*.py` **不得** import 任何 `views/` 模組
- UI 回呼一律透過 `root.after(0, fn)` 傳入，不直接操作 tkinter widget
- 校準設定透過 `DatabaseManager` 儲存，不自行建立新的持久化機制

---

## 3. 資料結構規範

### 3.1 OrbType（珠色列舉）

```python
# models/orb_config.py
from enum import Enum

class OrbType(Enum):
    FIRE  = "fire"    # 火  — 紅
    WATER = "water"   # 水  — 藍
    WOOD  = "wood"    # 木  — 綠
    LIGHT = "light"   # 光  — 黃
    DARK  = "dark"    # 暗  — 紫
    HEART = "heart"   # 心  — 粉紅
    EMPTY = "empty"   # 空格（消除後下落前的暫態）
```

### 3.2 Board

```python
Board = list[list[OrbType]]
# board[row][col]，row 0 = 最上排，col 0 = 最左欄
```

### 3.3 OrbConfig（校準設定）

```python
@dataclass
class OrbConfig:
    name: str                  # 設定名稱（對應遊戲）
    board_x: int               # 盤面左上角螢幕 X（邏輯像素）
    board_y: int               # 盤面左上角螢幕 Y（邏輯像素）
    cell_w:  int               # 單格寬度（像素）
    cell_h:  int               # 單格高度（像素）
    rows:    int               # 盤面列數（預設 5）
    cols:    int               # 盤面欄數（預設 6）
    drag_speed_ms: int = 25    # 每格移動耗時（毫秒）
    beam_width:    int = 10    # Beam Search 寬度
    max_steps:     int = 30    # 最大步數
```

### 3.4 SolverState（Beam Search 內部狀態）

```python
@dataclass
class SolverState:
    board:    Board
    pos:      tuple[int, int]   # 目前持珠位置 (row, col)
    held:     OrbType           # 目前手持的珠色
    path:     list[tuple[int, int]]
    _score:   int = -1          # 快取，-1 表示尚未計算

    def score(self) -> int:
        if self._score == -1:
            # 模擬放珠後評估
            sim = self._place_and_resolve()
            self._score = sim
        return self._score
```

---

## 4. 模組規範

### 4.1 `services/orb_board.py`

#### 職責
截取盤面區域並將每個格子辨識為 `OrbType`。

#### 公開介面

```python
class OrbBoard:
    def __init__(self, config: OrbConfig) -> None: ...

    def capture(self) -> Image:
        """截取 config 定義的盤面矩形，回傳 PIL.Image（實體像素）。"""

    def recognize(self, image: Image) -> Board:
        """將截圖轉為 Board。每格取中心 60% 區域的平均 HSV，對應 OrbType。"""

    def snapshot(self) -> Board:
        """capture() + recognize() 的便利方法。"""
```

#### 顏色對應表（HSV Hue 範圍）

```python
# Hue 值為 OpenCV 範圍（0–179）
ORB_HSV: dict[OrbType, tuple[int, int]] = {
    OrbType.FIRE:  (0,   10),   # 紅（含環繞 170–179）
    OrbType.WATER: (100, 130),  # 藍
    OrbType.WOOD:  (50,  80),   # 綠
    OrbType.LIGHT: (20,  35),   # 黃
    OrbType.DARK:  (130, 155),  # 紫
    OrbType.HEART: (155, 175),  # 粉紅
}
```

**辨識邏輯**：
1. 裁切格子中心 60%×60% 區域（排除邊框光暈干擾）
2. 轉換為 HSV 色彩空間
3. 計算飽和度 S > 50 的像素平均 Hue
4. 若飽和度不足（接近灰白），判為 `EMPTY`
5. 對應 `ORB_HSV` 表，找最近的 OrbType

#### 錯誤處理

| 情況 | 行為 |
|------|------|
| 截圖失敗 | 拋出 `RuntimeError("截圖失敗，請確認盤面區域設定")` |
| 格子無法辨識 | 設為 `OrbType.EMPTY`，並寫入 warning log |
| 空白格超過 30% | 拋出 `RuntimeError("盤面辨識異常，請重新校準")` |

---

### 4.2 `services/orb_solver.py`

#### 職責
接收 `Board`，用 Beam Search 求解最高 combo 路線，回傳拖曳路徑。

#### 公開介面

```python
class OrbSolver:
    def __init__(self, config: OrbConfig) -> None: ...

    def solve(self, board: Board) -> list[tuple[int, int]]:
        """
        回傳路線：[(r0,c0), (r1,c1), ...]
        第一個元素為起始格（拖曳起點），後續為依序經過的格。
        """

    def simulate_score(self, board: Board) -> int:
        """對外提供評分，供 UI 顯示預期 combo 數。"""
```

#### Beam Search 規格

```
輸入：board, config.beam_width, config.max_steps

初始化：
  candidates = []
  for each (r, c) in board:
      state = SolverState(board=deepcopy(board), pos=(r,c),
                          held=board[r][c], path=[(r,c)])
      # 拿起珠：board[r][c] 暫時設為 EMPTY
      candidates.append(state)

每輪迭代（最多 max_steps 次）：
  next_candidates = []
  for state in candidates:
      for (dr, dc) in [(-1,0),(1,0),(0,-1),(0,1)]:
          new_r = state.pos[0] + dr
          new_c = state.pos[1] + dc
          if 越界 or (new_r, new_c) == 前一格:  # 防止立即折返
              continue
          new_state = state.move(new_r, new_c)   # 交換珠色
          next_candidates.append(new_state)

  按 score() 降序排列
  candidates = next_candidates[:beam_width]

回傳 candidates[0].path
```

#### combo 評分函式規格

```python
def _resolve(board: Board) -> int:
    """
    模擬消除與下落，回傳總 combo 數。
    純函數，不修改傳入的 board。
    """
    total = 0
    b = deepcopy(board)
    while True:
        matched = set()
        # 橫向掃描：同色連續 >= 3
        for r in range(ROWS):
            for c in range(COLS - 2):
                if b[r][c] != EMPTY and b[r][c] == b[r][c+1] == b[r][c+2]:
                    matched |= {(r,c),(r,c+1),(r,c+2)}
                    # 繼續延伸
                    k = c + 3
                    while k < COLS and b[r][k] == b[r][c]:
                        matched.add((r, k)); k += 1
        # 縱向掃描：同色連續 >= 3
        for c in range(COLS):
            for r in range(ROWS - 2):
                if b[r][c] != EMPTY and b[r][c] == b[r+1][c] == b[r+2][c]:
                    matched |= {(r,c),(r+1,c),(r+2,c)}
                    k = r + 3
                    while k < ROWS and b[k][c] == b[r][c]:
                        matched.add((k, c)); k += 1
        if not matched:
            break
        for (r, c) in matched:
            b[r][c] = EMPTY
        _drop(b)    # 珠子受重力下落填補空格
        total += 1  # 每次消除算 1 combo
    return total
```

**6 顆同色加成**：同一次消除中，同色珠 ≥ 6 顆，該 combo 計為 2（加倍）。

---

### 4.3 `services/orb_executor.py`

#### 職責
將路線座標轉為螢幕像素並執行滑鼠拖曳。

#### 公開介面

```python
class OrbExecutor:
    def __init__(self, config: OrbConfig) -> None: ...

    def run(self, path: list[tuple[int, int]],
            on_done: Callable[[], None],
            on_error: Callable[[str], None]) -> None:
        """在 daemon 執行緒中執行，完成後呼叫 on_done。"""

    def abort(self) -> None:
        """中途中止，立即 mouseUp。"""
```

#### 座標換算

```python
def _to_screen(self, row: int, col: int) -> tuple[int, int]:
    x = self._cfg.board_x + col * self._cfg.cell_w + self._cfg.cell_w // 2
    y = self._cfg.board_y + row * self._cfg.cell_h + self._cfg.cell_h // 2
    return x, y
```

#### 拖曳執行流程

```
1. 換算起點座標
2. pyautogui.mouseDown(start_x, start_y)
3. time.sleep(0.05)   ← 讓遊戲偵測到按下
4. for (r, c) in path[1:]:
       x, y = _to_screen(r, c)
       pyautogui.moveTo(x, y, duration=drag_speed_ms/1000)
5. time.sleep(0.03)   ← 讓遊戲偵測到最終位置
6. pyautogui.mouseUp()
```

#### 執行緒安全

- `run()` 啟動一個 daemon thread（名稱 `OrbExecutor`）
- `abort()` 設置 `_stop_event`，執行緒在每次 moveTo 前檢查，中止時呼叫 `pyautogui.mouseUp()`
- 同一時間只允許一個執行緒執行（呼叫時若已在執行，直接 return）

---

### 4.4 `views/orb_calibrate.py`

#### 職責
提供 `OrbCalibrateWindow`（`tk.Toplevel`）讓使用者框選盤面並設定參數。

#### UI 結構

```
OrbCalibrateWindow (700×500)
├── 說明文字：「請拖曳選取遊戲盤面區域」
├── [截取盤面] 按鈕  → 進入 _RegionSelector 流程
├── 預覽框：顯示截取到的盤面截圖（疊加格線）
├── 設定區
│   ├── 列數（rows）    數字輸入
│   ├── 欄數（cols）    數字輸入
│   ├── 拖曳速度（ms）  數字輸入
│   └── Beam 寬度       數字輸入
├── [辨識測試] 按鈕  → 截圖辨識並標色顯示於預覽框
└── [儲存] / [取消] 按鈕
```

#### 格線預覽規格

辨識測試後，每格以對應顏色填色（半透明）並標示珠色縮寫：

```
火=紅框  水=藍框  木=綠框  光=黃框  暗=紫框  心=粉框
```

---

## 5. 演算法規範

### 5.1 Beam Search 參數建議值

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `beam_width` | 10 | 越大品質越好，速度越慢 |
| `max_steps` | 30 | 超過 30 步意義不大 |
| 目標執行時間 | < 1.5 秒 | 含截圖 + 辨識 + 求解 |

### 5.2 效能基準

在一般筆電（i5 以上）上測試，目標達成條件：

| 盤面大小 | beam_width=10 | beam_width=20 |
|----------|---------------|---------------|
| 5×6      | < 800ms       | < 1500ms      |
| 6×7      | < 1200ms      | < 2500ms      |

若超時，自動降低 `beam_width` 至 5 並警告使用者。

### 5.3 移動限制

- 不允許立即折返上一格（防無效來回）
- 不允許越出盤面邊界
- 路線中重複經過同一格是**允許的**（實際轉珠可以繞路）

---

## 6. UI 規範

### 6.1 主視窗新增「轉珠」分頁或區塊

在主視窗右側「控制台」區域新增「🔮 轉珠」按鈕，點擊後開啟 `OrbCalibrateWindow`。

若已有儲存的校準設定，顯示一個下拉選單供選擇。

### 6.2 執行觸發

提供兩種觸發方式：

| 方式 | 說明 |
|------|------|
| 熱鍵 `F8` | 全域熱鍵，遊戲中直接觸發（透過 `KeyboardMonitor`） |
| UI 按鈕 | 主視窗或迷你視窗中的「▶ 轉珠」按鈕 |

### 6.3 執行中狀態顯示

使用現有 `_Mini` 覆蓋視窗，新增顯示欄位：

```
🔮 轉珠模式
辨識：5×6 盤面
求解：combo 預測 8
執行中...
```

### 6.4 錯誤提示

| 錯誤 | 顯示方式 |
|------|----------|
| 盤面辨識異常 | `_set_status("盤面辨識失敗，請重新校準", "error")` |
| 求解超時 | `_set_status("求解超時，已降低精度", "error")` |
| 未校準 | `messagebox.showwarning("請先設定盤面區域")` |

---

## 7. 新增動作類型：`drag`

### 7.1 目的

`drag` 是通用的拖曳動作，與遊戲無關，可用於：
- 拖曳檔案
- 拖曳 UI 元件
- 任何 mouseDown → moveTo → mouseUp 的場景

### 7.2 ClickStep 欄位對應

| 欄位 | 用途 |
|------|------|
| `x`, `y` | 拖曳起點座標 |
| `extra_json` | `{"to_x": int, "to_y": int, "duration": float}` |
| `delay` | 拖曳完成後的等待秒數 |
| `count` | 重複次數 |

### 7.3 display_label 格式

```
[drag] (100, 200) → (300, 400)  耗時0.3s  間隔1秒
```

### 7.4 ClickExecutor 執行邏輯

```python
elif action == "drag":
    params    = json.loads(step.extra_json or "{}")
    to_x      = int(params.get("to_x", step.x))
    to_y      = int(params.get("to_y", step.y))
    duration  = float(params.get("duration", 0.3))
    for _ in range(step.count):
        if self._stop_event.is_set(): break
        pyautogui.mouseDown(step.x, step.y)
        time.sleep(0.05)
        pyautogui.moveTo(to_x, to_y, duration=duration)
        time.sleep(0.03)
        pyautogui.mouseUp()
        self._interruptible_sleep(step.delay)
```

### 7.5 UI 步驟編輯器

`drag` 選中時顯示：
- 起點 X / 起點 Y（現有 `_ent_x`, `_ent_y`）
- 終點 X / 終點 Y（新增兩個 `_numeric_entry`）
- 拖曳耗時（秒）
- 執行次數 / 間隔

---

## 8. 測試規範

### 8.1 單元測試

**`tests/test_orb_board.py`**

```python
def test_recognize_fire():
    """純紅色格子應辨識為 FIRE"""

def test_recognize_empty():
    """灰白格子應辨識為 EMPTY"""

def test_board_too_many_empty():
    """超過 30% 空格應拋出 RuntimeError"""
```

**`tests/test_orb_solver.py`**

```python
def test_resolve_simple_row():
    """三顆同色橫排 → 1 combo"""

def test_resolve_chain():
    """消除後下落觸發第二次消除 → 2 combo"""

def test_six_orbs_double():
    """同色 6 顆 → combo 計為 2"""

def test_solve_finds_better_than_random():
    """Beam Search 分數必須 >= 隨機路線分數"""

def test_no_backtrack():
    """路線中不允許立即折返前一格"""
```

**`tests/test_orb_executor.py`**

```python
def test_to_screen_coord():
    """座標換算公式驗證"""

def test_abort_releases_mouse(monkeypatch):
    """中止時必須呼叫 mouseUp"""
```

### 8.2 整合測試

手動測試 Checklist（每次 release 前確認）：

- [ ] 框選盤面，格線預覽正確對齊
- [ ] 辨識測試：各色珠顏色標色正確
- [ ] 求解後在模擬器中執行拖曳，珠子確實移動
- [ ] F8 熱鍵在遊戲中可觸發
- [ ] 按 Space 中止執行緒，mouseUp 正確釋放
- [ ] 校準設定儲存/載入正確
- [ ] `drag` 動作在步驟編輯器正確顯示與執行

---

## 9. 開發里程碑

### Phase 1 — 基礎拖曳（`drag` 動作類型）
**目標**：ClickFlow 支援通用拖曳，可手動設定起點/終點執行

- [ ] `ClickStep` / `ClickExecutor` 加入 `drag` 支援
- [ ] 步驟編輯器新增終點 X/Y、耗時欄位
- [ ] 手動測試拖曳功能

### Phase 2 — 盤面校準
**目標**：使用者可框選盤面並儲存校準設定

- [ ] `OrbConfig` dataclass
- [ ] `DatabaseManager` 支援儲存/讀取 `OrbConfig`
- [ ] `OrbCalibrateWindow` UI（框選 + 格線預覽）
- [ ] 主視窗新增「🔮 轉珠」入口按鈕

### Phase 3 — 盤面辨識
**目標**：截圖可正確辨識每格珠色

- [ ] `OrbBoard.capture()` + `OrbBoard.recognize()`
- [ ] 顏色對應表調校（需實機測試）
- [ ] 辨識測試預覽（標色格線）

### Phase 4 — 路線求解
**目標**：Beam Search 可在 1.5 秒內求出有效路線

- [ ] `_resolve()` combo 評分函式（含單元測試）
- [ ] `OrbSolver.solve()` Beam Search 實作
- [ ] 效能基準測試

### Phase 5 — 整合執行
**目標**：完整流程可跑通

- [ ] `OrbExecutor.run()` + `abort()`
- [ ] F8 熱鍵整合 `KeyboardMonitor`
- [ ] `_Mini` 視窗顯示轉珠狀態
- [ ] 完整整合測試

---

## 10. 限制與排除事項

| 項目 | 說明 |
|------|------|
| 僅支援 PC 模擬器 | 需使用 BlueStacks / MuMu / LDPlayer 等 Android 模擬器，不支援直連手機 |
| 不保證所有版本 | 遊戲更新後珠色/UI 可能變動，需重新校準顏色對應表 |
| 執行時不得移動視窗 | 執行中移動模擬器視窗會導致座標偏移 |
| 不處理特殊珠 | 初版不支援強化珠、鎖珠、毒珠等特殊狀態，一律視為普通珠色 |
| 不自動偵測回合 | 使用者需手動在回合開始時觸發（按 F8） |
| 相似度辨識限制 | 盤面光影過強（技能特效期間）可能辨識錯誤，需等特效結束後再觸發 |
