# ClickFlow

Windows 桌面自動化工具 — 錄製滑鼠與鍵盤操作，儲存為可重複執行的腳本。

![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## 功能特色

- **多步驟錄製** — 支援點擊、雙擊、右鍵、移動、延遲、鍵盤輸入、快捷鍵
- **多操作管理** — 可儲存多組不同操作，隨時切換載入
- **即時座標追蹤** — 視窗頂部即時顯示游標位置，按 `S` 鍵一鍵擷取
- **靈活執行** — 設定執行輪數或開啟無限循環，`Space` 鍵隨時中斷
- **熱鍵支援** — 免開視窗，直接用鍵盤控制擷取與停止

## 快速開始

### 安裝

```bash
pip install pyautogui
```

### 執行

```bash
python main.py
```

## 使用方式

### 建立操作流程

1. 在左側「步驟編輯器」選擇動作類型（點擊／鍵盤輸入等）
2. 移動游標到目標位置，按 `S` 鍵自動填入座標
3. 點擊「＋ 新增步驟」加入序列
4. 重複以上步驟建立完整流程

### 儲存與管理

- 在底部列輸入名稱，點「儲存」儲存目前操作
- 底部列表顯示所有已儲存操作，點擊選取後按「載入」或雙擊直接載入
- 點「＋ 新增操作」清空步驟，開始全新操作

### 執行

1. 在右側設定執行輪數（勾選「無限循環」可持續執行）
2. 點「▶ 開始」執行
3. 按 `Space` 鍵或「■ 停止」中斷

## 熱鍵

| 鍵 | 功能 |
|----|------|
| `S` | 擷取目前游標座標，填入步驟編輯器 |
| `Space` | 立即停止執行中的腳本 |

## 支援的動作類型

| 類型 | 說明 |
|------|------|
| `click` | 滑鼠左鍵點擊 |
| `double_click` | 滑鼠左鍵雙擊 |
| `right_click` | 滑鼠右鍵點擊 |
| `move` | 移動游標到指定座標 |
| `delay` | 等待指定秒數 |
| `keyboard_input` | 輸入文字 |
| `hotkey` | 觸發快捷鍵（如 `ctrl+c`、`alt+F4`） |

## 打包為執行檔

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name ClickFlow --hidden-import pyscreeze --hidden-import mouseinfo main.py
```

輸出位於 `dist/ClickFlow.exe`，無需安裝 Python 即可執行。

## 專案結構

```
ClickFlow/
├── main.py                  # 入口：logging → DB → GUI
├── models/
│   ├── click_step.py        # 步驟資料類別
│   ├── profile.py           # 設定檔資料類別
│   └── action.py            # DB 動作記錄類別
├── services/
│   ├── database_manager.py  # SQLite CRUD
│   ├── click_executor.py    # 執行執行緒
│   ├── keyboard_monitor.py  # 全域熱鍵監聽
│   └── mouse_tracker.py     # 即時座標追蹤
└── views/
    └── main_window.py       # 完整 GUI（tkinter / ttk）
```

## 系統需求

- Windows 10 / 11（64-bit）
- Python 3.11+
- pyautogui >= 0.9.54
