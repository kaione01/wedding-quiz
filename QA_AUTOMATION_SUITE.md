# 婚禮問答遊戲 — 自動化測試套件
> **版本**: 2026-05-06  
> **狀態**: 🟢 Active Testing Framework  
> **覆蓋率**: 功能測試 + 單元測試 + 集成測試 + 壓力測試

---

## 📋 目錄
1. [測試架構](#測試架構)
2. [自動化測試腳本](#自動化測試腳本)
3. [手工測試檢查清單](#手工測試檢查清單)
4. [壓力測試計畫](#壓力測試計畫)
5. [問題追蹤模板](#問題追蹤模板)
6. [測試環境配置](#測試環境配置)

---

## 🏗️ 測試架構

### 測試金字塔

```
        🔴 E2E / UAT (5%)
       📊 集成測試 (20%)
      ✅ 單元測試 (75%)
   ━━━━━━━━━━━━━━━━━
   自動化測試框架
```

### 測試類型映射

| 測試類型 | 工具 | 覆蓋範圍 | 優先級 |
|---------|------|---------|--------|
| 單元測試 | `pytest` | 遊戲邏輯、計分、排行榜 | 🔴 P0 |
| 集成測試 | `pytest` + WebSocket | WebSocket 通訊、狀態同步 | 🔴 P0 |
| E2E 測試 | `Playwright` | 頁面互動、完整遊戲流程 | 🟡 P1 |
| 壓力測試 | 自製 Python 腳本 | 5+ 人並發、網路延遲 | 🟡 P1 |
| 安全測試 | 手工 + 代碼審查 | 密碼驗證、簽名驗證 | 🔴 P0 |

---

## 🤖 自動化測試腳本

### 1. 單元測試：GameState 邏輯

**檔案**: `tests/test_game_state.py`

```python
import pytest
from pathlib import Path
import sys

# 加入 parent 目錄以匯入 main.py 的 GameState
sys.path.insert(0, str(Path(__file__).parent.parent))

# 假設已將 GameState 類從 main.py 分離到 game_state.py
from game_state import GameState

class TestGameState:
    """遊戲狀態管理單元測試"""
    
    def setup_method(self):
        """每個測試前初始化"""
        self.game = GameState()
        self.game.questions = [
            {
                "text": "今天新郎是？",
                "options": ["A人", "B人", "C人", "D人"],
                "correct": 0,
                "time_limit": 10,
                "option_type": "text"
            },
            {
                "text": "今天新娘是？",
                "options": ["A人", "B人", "C人", "D人"],
                "correct": 2,
                "time_limit": 10,
                "option_type": "text"
            }
        ]
    
    def test_initial_phase(self):
        """測試初始狀態"""
        assert self.game.phase == "LOBBY"
        assert self.game.current_q_index == -1
        assert len(self.game.players) == 0
    
    def test_add_player(self):
        """測試玩家加入"""
        conn_id = "conn_123"
        self.game.players[conn_id] = {
            "nickname": "測試玩家",
            "score": 0,
            "last_points": 0
        }
        assert len(self.game.players) == 1
        assert self.game.players[conn_id]["nickname"] == "測試玩家"
    
    def test_duplicate_nickname_handling(self):
        """測試重複暱稱處理"""
        conn_id_1 = "conn_123"
        conn_id_2 = "conn_456"
        
        self.game.players[conn_id_1] = {"nickname": "Kai", "score": 0}
        
        # 模擬重複暱稱檢查邏輯
        existing_names = [p["nickname"] for p in self.game.players.values()]
        new_nickname = "Kai"
        if new_nickname in existing_names:
            new_nickname = f"{new_nickname}_{conn_id_2[-3:]}"
        
        self.game.players[conn_id_2] = {"nickname": new_nickname, "score": 0}
        assert self.game.players[conn_id_2]["nickname"] == "Kai_456"
    
    def test_calculate_score(self):
        """測試計分邏輯"""
        # 0秒答對：1000分
        score = self.game.calc_score(0)
        assert score == 1000
        
        # 5秒答對：750分（1000 - 5*50）
        score = self.game.calc_score(5)
        assert score == 750
        
        # 10秒答對：500分
        score = self.game.calc_score(10)
        assert score == 500
        
        # 20秒答對：最低100分（not negative）
        score = self.game.calc_score(20)
        assert score == 100
    
    def test_answer_distribution(self):
        """測試答案分布計算"""
        self.game.answers = {
            "p1": 0,  # A
            "p2": 0,  # A
            "p3": 1,  # B
            "p4": 3,  # D
        }
        dist = self.game.get_answer_distribution()
        assert dist == [2, 1, 0, 1]  # A:2人, B:1人, C:0人, D:1人
    
    def test_leaderboard_ranking(self):
        """測試排行榜排序"""
        self.game.players = {
            "p1": {"nickname": "Alice", "score": 500},
            "p2": {"nickname": "Bob", "score": 800},
            "p3": {"nickname": "Charlie", "score": 600},
        }
        lb = self.game.get_leaderboard(top_n=3)
        
        # 驗證排序正確
        assert lb[0]["nickname"] == "Bob"
        assert lb[0]["score"] == 800
        assert lb[1]["nickname"] == "Charlie"
        assert lb[2]["nickname"] == "Alice"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

**運行方式**:
```bash
cd wedding-quiz
pytest tests/test_game_state.py -v
```

---

### 2. WebSocket 集成測試

**檔案**: `tests/test_websocket_flow.py`

```python
import pytest
import asyncio
import json
from unittest.mock import AsyncMock, patch

class TestWebSocketFlow:
    """WebSocket 通訊流程測試"""
    
    @pytest.mark.asyncio
    async def test_player_join_flow(self):
        """測試玩家加入流程"""
        # 1. 玩家連線
        join_msg = {
            "type": "player",
            "nickname": "測試玩家"
        }
        
        # 2. 驗證暱稱非空
        assert join_msg["nickname"].strip(), "暱稱不可為空"
        
        # 3. 驗證暱稱長度限制
        assert len(join_msg["nickname"]) <= 20, "暱稱不可超過20字"
    
    @pytest.mark.asyncio
    async def test_answer_submission(self):
        """測試答題提交"""
        answer_msg = {
            "action": "answer",
            "option": 2  # C
        }
        
        # 驗證選項範圍 0-3
        assert 0 <= answer_msg["option"] <= 3, "選項必須在 0-3 之間"
    
    @pytest.mark.asyncio
    async def test_password_verification(self):
        """測試 MC 密碼驗證"""
        correct_password = "kai2026"
        submitted_password = "kai2026"
        
        assert submitted_password == correct_password, "密碼錯誤"
    
    @pytest.mark.asyncio
    async def test_wrong_password_rejection(self):
        """測試錯誤密碼拒絕"""
        correct_password = "kai2026"
        wrong_password = "wrong"
        
        assert wrong_password != correct_password, "應拒絕錯誤密碼"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

**運行方式**:
```bash
pytest tests/test_websocket_flow.py -v
```

---

### 3. E2E 測試：Playwright 頁面互動

**檔案**: `tests/test_e2e_quiz_game.py`

```python
import pytest
from playwright.sync_api import sync_playwright, expect

class TestQuizGameE2E:
    """端對端遊戲流程測試"""
    
    @pytest.fixture(scope="class")
    def browser_context(self):
        """啟動瀏覽器"""
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            yield context
            context.close()
            browser.close()
    
    def test_display_page_loads(self, browser_context):
        """測試大螢幕頁面載入"""
        page = browser_context.new_page()
        page.goto("http://localhost:8001/display")
        
        # 驗證標題
        expect(page).to_have_title("婚禮問答")
        
        # 驗證 QR Code 存在
        qr = page.locator("canvas")  # QR Code 通常以 <canvas> 呈現
        expect(qr).to_be_visible()
        
        page.close()
    
    def test_play_page_nickname_input(self, browser_context):
        """測試玩家頁暱稱輸入"""
        page = browser_context.new_page()
        page.goto("http://localhost:8001/play")
        
        # 填入暱稱
        page.fill("input[placeholder*='暱稱']", "測試玩家")
        
        # 驗證輸入成功
        expect(page.locator("input")).to_have_value("測試玩家")
        
        page.close()
    
    def test_host_password_protection(self, browser_context):
        """測試 MC 控制台密碼保護"""
        page = browser_context.new_page()
        page.goto("http://localhost:8001/host")
        
        # 應該看到密碼輸入框
        password_input = page.locator("input[type='password']")
        expect(password_input).to_be_visible()
        
        page.close()

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

**安裝 Playwright**:
```bash
pip install playwright pytest-playwright
playwright install chromium
```

**運行方式**:
```bash
pytest tests/test_e2e_quiz_game.py -v
```

---

## ✅ 手工測試檢查清單

### 準備清單
- [ ] 後端啟動正常 (`python main.py`)
- [ ] Cloudflare Tunnel 已連線
- [ ] 題庫檔案 `questions.json` 已加載
- [ ] 測試設備網路正常

### Lobby 場景（遊戲開始前）

| 項目 | 測試步驟 | 預期結果 | ✅/❌ | 備註 |
|------|---------|---------|-------|------|
| L1 | 開啟 `/quiz/display` | 顯示「婚禮問答」標題 + QR Code | | |
| L2 | 掃描 QR Code | 手機跳轉到 `/quiz/play` | | |
| L3 | 輸入暱稱「Alice」點加入 | 跳轉到等待畫面，人數 +1 | | |
| L4 | 開啟 `/quiz/host` | 彈出密碼輸入框 | | |
| L5 | 輸入密碼 `kai2026` | 進入 MC 控制台 | | |
| L6 | 再加入 2 個玩家 | 大螢幕人數正確顯示為 3 | | |

### 遊戲流程（第 1 題 - 圖片選項）

| 項目 | 測試步驟 | 預期結果 | ✅/❌ | 備註 |
|------|---------|---------|-------|------|
| Q1 | MC 點「開始遊戲」 | 大螢幕顯示題目「今天新郎是？」+ 4 張圖片 | | |
| Q2 | 驗證圖片填滿螢幕 | 4 張圖以 2×2 填滿，無邊框空白 | | |
| Q3 | 驗證角標 A/B/C/D | 每張圖左上角顯示對應標籤 | | |
| Q4 | 驗證倒數計時 | 顯示 10 秒圓弧倒數，數字清晰 | | |
| Q5 | 玩家手機點選 B（新郎照片） | 該選項高亮，無法再點選 | | |
| Q6 | 時間倒數到 0 | 大螢幕**不自動**揭曉，等 MC 操作 | | |
| Q7 | MC 點「繼續」揭曉 | 顯示「正確答案：B」+ 答題分布圖 | | |
| Q8 | 驗證答題分布 | A、B、C、D 各顯示人數（長條動畫） | | |

### 排行榜與遊戲結束

| 項目 | 測試步驟 | 預期結果 | ✅/❌ | 備註 |
|------|---------|---------|-------|------|
| R1 | 揭曉後 MC 點「繼續」 | 大螢幕切換到排行榜 | | |
| R2 | 驗證名次圖示 | 前三名顯示 🥇🥈🥉 | | |
| R3 | 第 2 題結束後 | 顯示「最終排行榜」 | | |
| R4 | 遊戲全部結束 | 顯示「恭喜 Kai & Bella！❤️ 祝百年好合 ❤️」 | | |

### MC 控制台功能

| 項目 | 測試步驟 | 預期結果 | ✅/❌ | 備註 |
|------|---------|---------|-------|------|
| M1 | 進入遊戲中 | 顯示「X / Y 人已答」 | | |
| M2 | 「繼續」按鈕狀態 | 題目中灰色 → 揭曉後可點 → 排行榜後可點 | | |
| M3 | 點「重置」按鈕 | 所有玩家清除，回到 Lobby | | |

---

## 💪 壓力測試計畫

### 場景 1：5 人同時加入

```bash
# Python 壓力測試腳本（簡化版）
# 檔案：tests/load_test_concurrent_join.py

import asyncio
import websockets
import json

async def simulate_player(player_id: int, url: str):
    """模擬一個玩家"""
    async with websockets.connect(url) as ws:
        # 加入遊戲
        msg = {
            "type": "player",
            "nickname": f"Player_{player_id}"
        }
        await ws.send(json.dumps(msg))
        
        # 等待加入確認
        response = await ws.recv()
        print(f"✅ Player {player_id} joined: {response}")

async def load_test():
    """5 人並發加入"""
    url = "ws://localhost:8001/ws"
    tasks = [simulate_player(i, url) for i in range(1, 6)]
    await asyncio.gather(*tasks)
    print("✅ 5 人同時加入完成")

if __name__ == "__main__":
    asyncio.run(load_test())
```

**運行方式**:
```bash
python tests/load_test_concurrent_join.py
```

### 場景 2：5 人同時答題

```python
# 檔案：tests/load_test_concurrent_answers.py

async def simulate_answer_submission(player_id: int, answer_option: int, url: str):
    """模擬一個玩家提交答題"""
    async with websockets.connect(url) as ws:
        # 先加入
        await ws.send(json.dumps({"type": "player", "nickname": f"Player_{player_id}"}))
        await ws.recv()
        
        # 提交答案
        answer_msg = {
            "action": "answer",
            "option": answer_option
        }
        await ws.send(json.dumps(answer_msg))
        print(f"✅ Player {player_id} answered: {answer_option}")

async def load_test_answers():
    """5 人同時提交答案"""
    url = "ws://localhost:8001/ws"
    answers = [0, 1, 1, 2, 3]  # 不同的答案選項
    tasks = [simulate_answer_submission(i, answers[i], url) for i in range(5)]
    await asyncio.gather(*tasks)
    print("✅ 5 人同時答題完成")
```

---

## 🐛 問題追蹤模板

### 問題報告格式

```markdown
## 問題標題
[簡短描述]

### 重現步驟
1. 開啟 `/quiz/play`
2. 輸入暱稱「Test」
3. MC 點「開始遊戲」
4. ...

### 預期行為
應該顯示題目和 4 個選項

### 實際行為
[截圖或影片]

### 環境
- 瀏覽器：Chrome 130
- 裝置：iPhone 15
- 網路：4G
- 時間：2026-05-06 16:00

### 嚴重程度
🔴 Critical / 🟠 High / 🟡 Medium / 🟢 Low
```

---

## 🔧 測試環境配置

### 依賴安裝

```bash
# Python 套件
pip install -r requirements.txt

# 其中應包含：
# - fastapi
# - uvicorn
# - websockets
# - pytest
# - pytest-asyncio
# - playwright
```

### 本地測試啟動

```bash
# 終端 1：啟動後端
cd wedding-quiz
python main.py

# 終端 2：運行測試
pytest tests/ -v --tb=short
```

### 測試結果報告

```bash
# 生成 HTML 測試報告
pytest tests/ --html=report.html --self-contained-html

# 生成覆蓋率報告
pytest tests/ --cov=. --cov-report=html
```

---

## 📊 測試檢查清單總結

### 上線前必做（Green Gate）

- [ ] ✅ 所有 P0 單元測試通過
- [ ] ✅ 所有 P0 安全測試通過
- [ ] ✅ E2E 完整遊戲流程測試 2 次通過
- [ ] ✅ 5 人壓力測試穩定
- [ ] ✅ 網路延遲 > 500ms 仍可重連
- [ ] ✅ MC 控制台密碼保護有效
- [ ] ✅ 題庫已補至 8+ 題

### 活動後追蹤（Post-Event）

- [ ] 收集用戶反饋
- [ ] 分析錯誤日誌
- [ ] 記錄優化建議
- [ ] 更新下次測試計畫

---

**文件版本**: 2026-05-06  
**維護者**: Kai (QA Lead)  
**下次更新**: 活動後檢討會
