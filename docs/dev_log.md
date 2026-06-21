# Dev Log

開發過程中的決策記錄、踩過的坑、調校過的參數。

---

## 2026-06-21

### 架構決策：一個專案，兩個 Tab

**決定**：轉珠功能整合進 ClickFlow，不拆獨立專案。

**原因**：
- `pyautogui`、`PIL`、`opencv`、`_RegionSelector`、深色主題完全共用，拆出去等於重寫
- 單人維護，兩個 repo 只是增加負擔
- 轉珠模組（`services/orb_*.py`）與自動化模組完全不交叉，定位不會混亂

**邊界規則**：
- `services/orb_*.py` 不得 import `ClickStep`、`ClickExecutor`、任何 `views/`
- 兩個 Tab 共用 `_C` 調色盤、`DatabaseManager`、`KeyboardMonitor`
- 轉珠流程有自己的執行緒（`OrbExecutor`），不經過 `ClickExecutor`

---

### 新增 `drag` 動作類型

**決定**：在自動化 Tab（Tab 1）加入通用 `drag` 步驟。

**原因**：`drag` 和轉珠無關，是任何需要「按住拖動」場景的通用動作，屬於 ClickFlow 自動化功能的自然延伸。

**extra_json 格式**：`{"to_x": int, "to_y": int, "duration": float}`

---
