"""
婚禮問答遊戲系統 — Wedding Quiz
Kai & Bella Wedding 2026.05.24
"""

import asyncio
import json
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR    = Path(__file__).parent
STATIC_DIR  = BASE_DIR / "static"
UPLOADS_DIR = BASE_DIR / "static" / "uploads"
QUESTIONS_FILE = BASE_DIR / "questions.json"

HOST_PASSWORD = "141319"   # MC 登入密碼，可自行更改
PORT = 8001                  # 與彈幕系統 (8000) 分開

# ─────────────────────────────────────────────
# 遊戲狀態
# ─────────────────────────────────────────────
class GameState:
    def __init__(self):
        self.phase = "LOBBY"
        # LOBBY → CATEGORY_INTRO → QUESTION → REVEAL → LEADERBOARD → (loop) → FINISHED
        self.current_q_index = -1
        self.question_start_time: Optional[float] = None
        self.questions: list[dict] = []
        # 過場用：當前類別、預備下一題的 index
        self.current_category: Optional[str] = None
        self.pending_q_index: int = -1

        # 連線管理
        self.players:  dict[str, dict] = {}  # conn_id → {nickname, score, ws, last_points}
        self.displays: list[WebSocket] = []
        self.hosts:    list[WebSocket] = []

        # 本輪答案
        self.answers: dict[str, int] = {}    # conn_id → option_index (0-3)
        # 本輪答題時間（從題目開始算起的秒數,用於最快榜 + 計分）
        self.answer_times: dict[str, float] = {}  # conn_id → elapsed_seconds

        # 倒數計時任務
        self.timer_task: Optional[asyncio.Task] = None

        # 重連用：display/host 端的最後一次廣播 payload（不含玩家個人化資料）
        self.last_question_payload: Optional[dict] = None
        self.last_reveal_payload_display: Optional[dict] = None
        self.last_option_breakdown_payload: Optional[dict] = None
        self.last_leaderboard_payload: Optional[dict] = None

        # 本輪隨機排列後的選項與正確答案 index
        self.shuffled_options: list = []
        self.shuffled_correct: int = 0

    # ── 題目 ──
    def load_questions(self):
        if QUESTIONS_FILE.exists():
            self.questions = json.loads(QUESTIONS_FILE.read_text("utf-8"))
        else:
            self.questions = []

    @property
    def current_question(self) -> Optional[dict]:
        if 0 <= self.current_q_index < len(self.questions):
            return self.questions[self.current_q_index]
        return None

    # ── 計分（毫秒級,避免同秒同分）──
    def calc_score(self, seconds_elapsed: float) -> int:
        # 每秒扣 50 分,毫秒粒度
        # 0.000s → 1000, 1.235s → 938, 9.987s → 501, 15s → 250(終極),最低 100
        deduction = seconds_elapsed * 50
        return max(100, int(round(1000 - deduction)))

    # ── 答案分布 ──
    def get_answer_distribution(self) -> list[int]:
        counts = [0, 0, 0, 0]
        for opt in self.answers.values():
            if 0 <= opt <= 3:
                counts[opt] += 1
        return counts

    # ── 排行榜 ──
    def get_leaderboard(self, top_n: int = 5) -> list[dict]:
        ranked = sorted(
            self.players.values(),
            key=lambda p: p["score"],
            reverse=True,
        )
        return [
            {"rank": i + 1, "nickname": p["nickname"], "score": p["score"],
             "last_points": p.get("last_points", 0)}
            for i, p in enumerate(ranked[:top_n])
        ]

    def get_full_leaderboard(self) -> list[dict]:
        return self.get_leaderboard(top_n=len(self.players))

    # ── 廣播 ──
    async def broadcast(self, data: dict, targets: list[WebSocket]):
        dead = []
        for ws in targets:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in targets:
                targets.remove(ws)

    async def broadcast_all(self, data: dict):
        player_ws = [p["ws"] for p in self.players.values()]
        await self.broadcast(data, self.displays + self.hosts + player_ws)

    async def broadcast_displays_hosts(self, data: dict):
        await self.broadcast(data, self.displays + self.hosts)

    async def broadcast_players(self, data: dict):
        # 失敗的連線標記為 disconnected,保留 record(score)等待重連 — 不直接 pop
        for conn_id, p in list(self.players.items()):
            if p.get("disconnected") or p.get("ws") is None:
                continue
            try:
                await p["ws"].send_json(data)
            except Exception:
                p["disconnected"] = True
                p["ws"] = None

    async def send_to(self, ws: WebSocket, data: dict):
        try:
            await ws.send_json(data)
        except Exception:
            pass

    # ── 玩家數量廣播（排除已斷線等待重連者）──
    async def broadcast_player_count(self):
        active = [p for p in self.players.values() if not p.get("disconnected")]
        await self.broadcast_displays_hosts({
            "type": "player_count",
            "count": len(active),
            "nicknames": [p["nickname"] for p in active],
        })


game = GameState()


# ─────────────────────────────────────────────
# 遊戲流程函數
# ─────────────────────────────────────────────
def _category_stats(target_index: int) -> dict:
    """計算 target_index 題目所屬類別在整個題庫中的位置統計。"""
    if not game.questions or target_index >= len(game.questions):
        return {"category": "", "category_index": 0, "category_total": 0,
                "questions_in_category": 0}
    target_cat = game.questions[target_index].get("category", "")
    # 統計所有類別（按出現順序去重）
    ordered_cats = []
    for q in game.questions:
        c = q.get("category", "")
        if c and c not in ordered_cats:
            ordered_cats.append(c)
    cat_idx = ordered_cats.index(target_cat) + 1 if target_cat in ordered_cats else 0
    in_cat_count = sum(1 for q in game.questions if q.get("category", "") == target_cat)
    return {
        "category": target_cat,
        "category_index": cat_idx,
        "category_total": len(ordered_cats),
        "questions_in_category": in_cat_count,
    }


def _build_category_intro_payload(target_index: int) -> dict:
    stats = _category_stats(target_index)
    return {
        "type": "category_intro",
        "category": stats["category"],
        "category_index": stats["category_index"],
        "category_total": stats["category_total"],
        "questions_in_category": stats["questions_in_category"],
        "next_question_index": target_index,
        "total_questions": len(game.questions),
    }


async def _send_category_intro(target_index: int):
    """進入類別過場畫面（主持人按下一題後才會切到 QUESTION）"""
    game.phase = "CATEGORY_INTRO"
    game.pending_q_index = target_index
    stats = _category_stats(target_index)
    game.current_category = stats["category"]
    await game.broadcast_all(_build_category_intro_payload(target_index))


async def _resync_player_to(ws: WebSocket, conn_id: str):
    """玩家加入/重連後,依當前 phase 補送對應畫面訊息。"""
    phase = game.phase
    if phase == "CATEGORY_INTRO" and game.pending_q_index >= 0:
        await game.send_to(ws, _build_category_intro_payload(game.pending_q_index))
    elif phase in ("QUESTION", "QUESTION_ENDED") and game.current_question:
        q = game.current_question
        elapsed = (time.time() - game.question_start_time) if game.question_start_time else 0
        remaining = max(0, q.get("time_limit", 10) - elapsed)
        await game.send_to(ws, {
            "type": "question_start",
            "index": game.current_q_index,
            "total": len(game.questions),
            "text": q["text"],
            "image": q.get("image"),
            "options": game.shuffled_options,
            "time_limit": q.get("time_limit", 10),
            "remaining": remaining,
            "option_type": q.get("option_type", "text"),
        })
        # 若已答過(接管 record 後或重連),通知前端切到 selected 畫面
        if conn_id in game.answers:
            await game.send_to(ws, {
                "type": "answer_received",
                "option": game.answers[conn_id],
            })
    elif phase in ("REVEAL", "FASTEST_BREAKDOWN") and game.current_question:
        # FASTEST_BREAKDOWN 階段,玩家手機仍停在 REVEAL 畫面
        q = game.current_question
        correct = game.shuffled_correct
        correct_text = (game.shuffled_options[correct]
                        if 0 <= correct < len(game.shuffled_options) else "")
        p = game.players.get(conn_id, {})
        last_opt = game.answers.get(conn_id)
        answered = last_opt is not None
        await game.send_to(ws, {
            "type": "answer_reveal",
            "correct": correct,
            "correct_text": correct_text,
            "option_type": q.get("option_type", "text"),
            "options": game.shuffled_options,   # 給重連用,前端可立刻渲染正確答案大圖
            "reveal_media": q.get("reveal_media"),
            "personal_correct": (last_opt == correct) if answered else False,
            "points_earned": p.get("last_points", 0),
            "total_score": p.get("score", 0),
            "answered": answered,
        })
    elif phase == "LEADERBOARD" and game.last_leaderboard_payload:
        await game.send_to(ws, game.last_leaderboard_payload)
    elif phase == "FINISHED":
        await game.send_to(ws, {
            "type": "game_finish",
            "leaderboard": game.get_full_leaderboard(),
        })


async def _resync_phase_to(ws: WebSocket):
    """display/host 重連時,根據當前 phase 補送對應的最後一次廣播。"""
    phase = game.phase
    if phase == "CATEGORY_INTRO" and game.pending_q_index >= 0:
        await game.send_to(ws, _build_category_intro_payload(game.pending_q_index))
    elif phase in ("QUESTION", "QUESTION_ENDED") and game.last_question_payload:
        payload = dict(game.last_question_payload)
        # 計算剩餘時間,讓重連端顯示正確倒數
        if game.question_start_time:
            elapsed = time.time() - game.question_start_time
            remaining = max(0, payload.get("time_limit", 10) - elapsed)
            payload["remaining"] = remaining
        await game.send_to(ws, payload)
    elif phase == "REVEAL" and game.last_reveal_payload_display:
        await game.send_to(ws, game.last_reveal_payload_display)
    elif phase == "FASTEST_BREAKDOWN" and game.last_option_breakdown_payload:
        await game.send_to(ws, game.last_option_breakdown_payload)
    elif phase == "LEADERBOARD" and game.last_leaderboard_payload:
        await game.send_to(ws, game.last_leaderboard_payload)
    elif phase == "FINISHED" and game.last_leaderboard_payload:
        # FINISHED 階段補送最終排行榜（用 finish 訊息）
        await game.send_to(ws, {
            "type": "game_finish",
            "leaderboard": game.get_full_leaderboard(),
        })


async def start_game():
    game.load_questions()
    if not game.questions:
        return
    # 第一題前先顯示類別過場
    await _send_category_intro(0)


async def _send_question():
    # 若處於過場階段,正式進入該題
    if game.phase == "CATEGORY_INTRO" and game.pending_q_index >= 0:
        game.current_q_index = game.pending_q_index
        game.pending_q_index = -1

    q = game.current_question
    if not q:
        await finish_game()
        return

    game.current_category = q.get("category", "")
    game.phase = "QUESTION"
    game.answers = {}
    game.answer_times = {}
    game.question_start_time = time.time()

    # 取消舊計時
    if game.timer_task and not game.timer_task.done():
        game.timer_task.cancel()

    time_limit = q.get("time_limit", 10)

    # 隨機排列選項（每次出題都重新洗牌）
    orig_correct = q["correct"]
    orig_options = q["options"]
    indices = list(range(len(orig_options)))
    random.shuffle(indices)
    shuffled_opts = [orig_options[i] for i in indices]
    shuffled_correct = indices.index(orig_correct)
    game.shuffled_options = shuffled_opts
    game.shuffled_correct = shuffled_correct

    question_payload = {
        "type": "question_start",
        "index": game.current_q_index,
        "total": len(game.questions),
        "text": q["text"],
        "image": q.get("image"),
        "options": shuffled_opts,
        "time_limit": time_limit,
        "option_type": q.get("option_type", "text"),
    }
    # 給 display/host 完整資訊（含洗牌後選項）
    await game.broadcast_displays_hosts(question_payload)
    # 給玩家只送選項（不送正確答案）— 結構同上
    await game.broadcast_players(question_payload)
    # 快取以供 display/host 斷線重連
    game.last_question_payload = question_payload
    # 進入新題時清掉舊 reveal/leaderboard 快取
    game.last_reveal_payload_display = None
    game.last_leaderboard_payload = None

    # 自動倒數計時（時間到只通知 host，不自動公佈答案）
    game.timer_task = asyncio.create_task(_auto_timer_end(time_limit))


async def _auto_timer_end(time_limit: int):
    await asyncio.sleep(time_limit)
    if game.phase == "QUESTION":
        game.phase = "QUESTION_ENDED"
        # 通知 host 計時結束，按鈕可以變成「公佈答案」
        await game.broadcast({"type": "timer_ended"}, game.hosts)


async def reveal_answer():
    if game.phase not in ("QUESTION", "QUESTION_ENDED"):
        return

    if game.timer_task and not game.timer_task.done():
        game.timer_task.cancel()

    game.phase = "REVEAL"
    q = game.current_question
    correct = game.shuffled_correct
    distribution = game.get_answer_distribution()

    # 計分：使用「玩家提交瞬間」的 elapsed,而非揭曉當下的 elapsed
    # 這樣主持人在 QUESTION_ENDED 停留多久都不影響分數
    for conn_id, opt in game.answers.items():
        if conn_id not in game.players:
            continue
        if opt == correct:
            elapsed = game.answer_times.get(conn_id, 0)
            pts = game.calc_score(elapsed)
            game.players[conn_id]["score"] += pts
            game.players[conn_id]["last_points"] = pts
        else:
            game.players[conn_id]["last_points"] = 0

    leaderboard = game.get_leaderboard()
    correct_text = game.shuffled_options[correct]
    option_type  = q.get("option_type", "text")

    reveal_payload = {
        "type": "answer_reveal",
        "correct": correct,
        "correct_text": correct_text,
        "option_type": option_type,
        "options": game.shuffled_options,   # 重連補送時前端可還原 currentOptions
        "distribution": distribution,
        "reveal_media": q.get("reveal_media"),
        "leaderboard": leaderboard,
        "total_answered": len(game.answers),
        "total_players": len(game.players),
    }
    # 大螢幕 + host 看到公佈畫面
    await game.broadcast_displays_hosts(reveal_payload)
    game.last_reveal_payload_display = reveal_payload

    # 玩家手機：個人成績 + 正確答案（讓手機也顯示答案大圖）
    for conn_id, p in game.players.items():
        if p.get("disconnected") or p.get("ws") is None:
            continue  # 已斷線的玩家保留 score,等重連時補送
        answered = conn_id in game.answers
        correct_ans = game.answers.get(conn_id) == correct
        await game.send_to(p["ws"], {
            "type": "answer_reveal",
            "correct": correct,
            "correct_text": correct_text,
            "option_type": option_type,
            "reveal_media": q.get("reveal_media"),
            "personal_correct": correct_ans,
            "points_earned": p.get("last_points", 0),
            "total_score": p["score"],
            "answered": answered,
        })


def _build_option_breakdown_payload() -> dict:
    """組裝「最快榜」資料:每個選項的人數 + 該選項最快搶答者。"""
    correct = game.shuffled_correct
    options_data = []
    for i in range(4):
        picks = []
        for conn_id, opt in game.answers.items():
            if opt == i and conn_id in game.players:
                p = game.players[conn_id]
                t = game.answer_times.get(conn_id, 0.0)
                picks.append({"nickname": p["nickname"], "time": round(t, 2)})
        picks.sort(key=lambda x: x["time"])
        options_data.append({
            "index": i,
            "label": "ABCD"[i],
            "count": len(picks),
            "fastest": picks[0] if picks else None,
            "option_text": game.shuffled_options[i] if i < len(game.shuffled_options) else "",
        })
    fastest_correct = options_data[correct]["fastest"] if 0 <= correct < 4 else None
    return {
        "type": "option_breakdown",
        "correct": correct,
        "options": options_data,
        "fastest_correct": fastest_correct,
        "option_type": (game.current_question or {}).get("option_type", "text"),
        "total_answered": len(game.answers),
        "total_players": len(game.players),
    }


async def show_fastest():
    """主持人按「顯示最快榜」,從 REVEAL 進入 FASTEST_BREAKDOWN。"""
    if game.phase != "REVEAL":
        return
    game.phase = "FASTEST_BREAKDOWN"
    payload = _build_option_breakdown_payload()
    await game.broadcast_displays_hosts(payload)
    game.last_option_breakdown_payload = payload


async def show_leaderboard():
    # 接受從 REVEAL 或 FASTEST_BREAKDOWN 進入(向下相容自動化測試直接 REVEAL→LEADERBOARD)
    if game.phase not in ("REVEAL", "FASTEST_BREAKDOWN"):
        return
    game.phase = "LEADERBOARD"
    payload = {
        "type": "leaderboard",
        "leaderboard": game.get_full_leaderboard(),
        "is_final": False,
    }
    await game.broadcast_all(payload)
    game.last_leaderboard_payload = payload


async def next_question():
    if game.phase != "LEADERBOARD":
        return
    next_idx = game.current_q_index + 1
    if next_idx >= len(game.questions):
        await finish_game()
        return
    # 跨類別時先插入過場;同類別直接出題
    next_cat = game.questions[next_idx].get("category", "")
    if next_cat and next_cat != game.current_category:
        await _send_category_intro(next_idx)
    else:
        game.current_q_index = next_idx
        await _send_question()


async def start_category_questions():
    """主持人按「開始本類別」,從 CATEGORY_INTRO 進入 QUESTION"""
    if game.phase != "CATEGORY_INTRO":
        return
    await _send_question()


async def finish_game():
    game.phase = "FINISHED"
    if game.timer_task and not game.timer_task.done():
        game.timer_task.cancel()
    await game.broadcast_all({
        "type": "game_finish",
        "leaderboard": game.get_full_leaderboard(),
    })


async def reset_game():
    game.phase = "LOBBY"
    game.current_q_index = -1
    game.pending_q_index = -1
    game.current_category = None
    game.question_start_time = None
    game.answers = {}
    game.answer_times = {}
    game.players = {}
    game.last_question_payload = None
    game.last_reveal_payload_display = None
    game.last_option_breakdown_payload = None
    game.last_leaderboard_payload = None
    if game.timer_task and not game.timer_task.done():
        game.timer_task.cancel()
    await game.broadcast_all({"type": "game_reset"})


# ─────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    game.load_questions()
    print("=" * 50)
    print("🎮 婚禮問答遊戲系統啟動")
    print(f"📺 大螢幕：  http://localhost:{PORT}/display")
    print(f"📱 玩家加入：http://localhost:{PORT}/play")
    print(f"🎛️  MC 控制：  http://localhost:{PORT}/host")
    print("=" * 50)
    yield


app = FastAPI(lifespan=lifespan, title="婚禮問答遊戲")


# ─────────────────────────────────────────────
# WebSocket
# ─────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    conn_id = str(id(websocket))
    conn_type = None

    try:
        # 第一則訊息：識別連線類型
        raw = await websocket.receive_text()
        msg = json.loads(raw)
        conn_type = msg.get("type")

        if conn_type == "display":
            game.displays.append(websocket)
            await game.send_to(websocket, {
                "type": "state_sync",
                "phase": game.phase,
                "player_count": len(game.players),
                "total_questions": len(game.questions),
            })
            await _resync_phase_to(websocket)

        elif conn_type == "host":
            if msg.get("password") != HOST_PASSWORD:
                await game.send_to(websocket, {"type": "error", "msg": "密碼錯誤"})
                return
            game.hosts.append(websocket)
            await game.send_to(websocket, {
                "type": "host_welcome",
                "phase": game.phase,
                "player_count": len(game.players),
                "total_questions": len(game.questions),
                "current_index": game.current_q_index,
            })
            await _resync_phase_to(websocket)

        elif conn_type == "player":
            nickname = msg.get("nickname", "").strip()[:20]
            if not nickname:
                await game.send_to(websocket, {"type": "error", "msg": "請輸入暱稱"})
                return

            # === 同名重連檢查 ===
            # 若同 nickname 的玩家已斷線:接管其 record(score / 已答內容轉到新 conn_id)
            # 若同 nickname 的玩家還在線:加流水號後綴(避免雙開頂掉)
            rejoined = False
            for old_cid, op in list(game.players.items()):
                if op["nickname"] != nickname:
                    continue
                if op.get("disconnected"):
                    # 接管舊 record
                    game.players[conn_id] = {
                        "nickname": op["nickname"],
                        "score": op["score"],
                        "last_points": op.get("last_points", 0),
                        "ws": websocket,
                        "disconnected": False,
                    }
                    # 轉移本輪答案 / 答題時間
                    if old_cid in game.answers:
                        game.answers[conn_id] = game.answers[old_cid]
                        del game.answers[old_cid]
                    if old_cid in game.answer_times:
                        game.answer_times[conn_id] = game.answer_times[old_cid]
                        del game.answer_times[old_cid]
                    del game.players[old_cid]
                    rejoined = True
                else:
                    # 同名在線:加後綴
                    existing_names = set(pp["nickname"] for pp in game.players.values())
                    base = nickname
                    suffix = 2
                    while f"{base}_{suffix}" in existing_names:
                        suffix += 1
                    nickname = f"{base}_{suffix}"
                break

            if not rejoined:
                game.players[conn_id] = {
                    "nickname": nickname,
                    "score": 0,
                    "last_points": 0,
                    "ws": websocket,
                    "disconnected": False,
                }

            await game.send_to(websocket, {
                "type": "joined",
                "nickname": game.players[conn_id]["nickname"],
                "phase": game.phase,
                "score": game.players[conn_id]["score"],
                "rejoined": rejoined,
            })
            await game.broadcast_player_count()
            # 依當前 phase 補送對應訊息,讓重連玩家立刻切到正確畫面
            await _resync_player_to(websocket, conn_id)
        else:
            return

        # ── 主要訊息迴圈 ──
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")

            # 玩家答題
            if conn_type == "player" and action == "answer":
                if game.phase == "QUESTION" and conn_id not in game.answers:
                    opt = int(msg.get("option", -1))
                    if 0 <= opt <= 3:
                        game.answers[conn_id] = opt
                        # 記錄提交時間(用於最快榜 + 精準計分)
                        if game.question_start_time:
                            game.answer_times[conn_id] = time.time() - game.question_start_time
                        await game.send_to(websocket, {"type": "answer_received", "option": opt})
                        # 通知 host/display 答題人數
                        await game.broadcast_displays_hosts({
                            "type": "answer_progress",
                            "answered": len(game.answers),
                            "total": len(game.players),
                        })
                        # 所有人都答了也繼續等計時，由 MC 或 timer 控制揭曉
                elif game.phase in ("LOBBY", "FINISHED"):
                    # 遊戲尚未開始或已結束,回 error 讓玩家端可記錄/顯示
                    await game.send_to(websocket, {
                        "type": "error", "msg": "遊戲尚未開始" if game.phase == "LOBBY" else "遊戲已結束"
                    })

            # Host 指令
            elif conn_type == "host":
                if action == "start_game":
                    await start_game()
                elif action == "start_category_questions":
                    await start_category_questions()
                elif action == "reveal_answer":
                    await reveal_answer()
                elif action == "show_fastest":
                    await show_fastest()
                elif action == "show_leaderboard":
                    await show_leaderboard()
                elif action == "next_question":
                    await next_question()
                elif action == "reset_game":
                    await reset_game()
                elif action == "reload_questions":
                    game.load_questions()
                    await game.send_to(websocket, {
                        "type": "questions_loaded",
                        "count": len(game.questions),
                    })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS] 例外: {e}")
    finally:
        # 清理連線
        if conn_type == "display" and websocket in game.displays:
            game.displays.remove(websocket)
        elif conn_type == "host" and websocket in game.hosts:
            game.hosts.remove(websocket)
        elif conn_type == "player" and conn_id in game.players:
            # 不直接刪除:標記 disconnected,保留 score 等待同名重連
            game.players[conn_id]["disconnected"] = True
            game.players[conn_id]["ws"] = None
            game.players[conn_id]["disconnected_at"] = time.time()
            await game.broadcast_player_count()


# ─────────────────────────────────────────────
# 頁面路由
# ─────────────────────────────────────────────
@app.get("/display", response_class=HTMLResponse)
async def display_page():
    return HTMLResponse((STATIC_DIR / "display.html").read_text("utf-8"))

@app.get("/sw.js")
async def service_worker():
    """PWA Service Worker — root scope 才能控制 /quiz/display"""
    return FileResponse(
        str(STATIC_DIR / "sw-quiz.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/quiz/"},
    )

@app.get("/manifest.json")
async def pwa_manifest():
    """PWA Manifest for 問答大螢幕"""
    return FileResponse(
        str(STATIC_DIR / "manifest-quiz.json"),
        media_type="application/manifest+json",
    )

@app.get("/play", response_class=HTMLResponse)
async def play_page():
    return HTMLResponse((STATIC_DIR / "play.html").read_text("utf-8"))

@app.get("/host", response_class=HTMLResponse)
async def host_page():
    return HTMLResponse((STATIC_DIR / "host.html").read_text("utf-8"))

@app.get("/")
async def root():
    return {"status": "婚禮問答遊戲運行中 🎮", "play": "/play", "display": "/display", "host": "/host"}

@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

# ── 上傳媒體（供出題用）──
@app.post("/api/upload")
async def upload_media(file: UploadFile = File(...)):
    import aiofiles
    ext = Path(file.filename).suffix.lower()
    allowed = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov"}
    if ext not in allowed:
        return JSONResponse({"error": "不支援的格式"}, status_code=400)
    filename = f"{int(time.time())}_{file.filename}"
    filepath = UPLOADS_DIR / filename
    async with aiofiles.open(filepath, "wb") as f:
        await f.write(await file.read())
    return {"url": f"/static/uploads/{filename}"}

# ── 題目管理 ──
@app.get("/api/questions")
async def get_questions():
    game.load_questions()
    return game.questions

@app.post("/api/questions")
async def save_questions(request_data: dict):
    questions = request_data.get("questions", [])
    QUESTIONS_FILE.write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
    game.load_questions()
    return {"status": "ok", "count": len(questions)}

@app.get("/api/status")
async def status():
    return {
        "phase": game.phase,
        "players": len(game.players),
        "current_question": game.current_q_index,
        "total_questions": len(game.questions),
    }


# ─────────────────────────────────────────────
# 靜態檔案
# ─────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─────────────────────────────────────────────
# 啟動
# ─────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
