"""
婚禮問答遊戲系統 — Wedding Quiz
Kai & Bella Wedding 2026.05.24
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR    = Path(__file__).parent
STATIC_DIR  = BASE_DIR / "static"
UPLOADS_DIR = BASE_DIR / "static" / "uploads"
QUESTIONS_FILE = BASE_DIR / "questions.json"

HOST_PASSWORD = "kai2026"   # MC 登入密碼，可自行更改
PORT = 8001                  # 與彈幕系統 (8000) 分開

# ─────────────────────────────────────────────
# 遊戲狀態
# ─────────────────────────────────────────────
class GameState:
    def __init__(self):
        self.phase = "LOBBY"
        # LOBBY → QUESTION → REVEAL → LEADERBOARD → (loop) → FINISHED
        self.current_q_index = -1
        self.question_start_time: Optional[float] = None
        self.questions: list[dict] = []

        # 連線管理
        self.players:  dict[str, dict] = {}  # conn_id → {nickname, score, ws, last_points}
        self.displays: list[WebSocket] = []
        self.hosts:    list[WebSocket] = []

        # 本輪答案
        self.answers: dict[str, int] = {}    # conn_id → option_index (0-3)

        # 倒數計時任務
        self.timer_task: Optional[asyncio.Task] = None

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

    # ── 計分 ──
    def calc_score(self, seconds_elapsed: float) -> int:
        deduction = int(seconds_elapsed) * 50
        return max(100, 1000 - deduction)

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
        dead_ids = []
        for conn_id, p in list(self.players.items()):
            try:
                await p["ws"].send_json(data)
            except Exception:
                dead_ids.append(conn_id)
        for cid in dead_ids:
            self.players.pop(cid, None)

    async def send_to(self, ws: WebSocket, data: dict):
        try:
            await ws.send_json(data)
        except Exception:
            pass

    # ── 玩家數量廣播 ──
    async def broadcast_player_count(self):
        await self.broadcast_displays_hosts({
            "type": "player_count",
            "count": len(self.players),
            "nicknames": [p["nickname"] for p in self.players.values()],
        })


game = GameState()


# ─────────────────────────────────────────────
# 遊戲流程函數
# ─────────────────────────────────────────────
async def start_game():
    game.load_questions()
    if not game.questions:
        return
    game.current_q_index = 0
    await _send_question()


async def _send_question():
    q = game.current_question
    if not q:
        await finish_game()
        return

    game.phase = "QUESTION"
    game.answers = {}
    game.question_start_time = time.time()

    # 取消舊計時
    if game.timer_task and not game.timer_task.done():
        game.timer_task.cancel()

    time_limit = q.get("time_limit", 20)

    # 給 display/host 完整資訊
    await game.broadcast_displays_hosts({
        "type": "question_start",
        "index": game.current_q_index,
        "total": len(game.questions),
        "text": q["text"],
        "image": q.get("image"),
        "options": q["options"],
        "time_limit": time_limit,
        "option_type": q.get("option_type", "text"),  # "text" or "image"
    })

    # 給玩家只送選項（不送正確答案）
    await game.broadcast_players({
        "type": "question_start",
        "index": game.current_q_index,
        "total": len(game.questions),
        "text": q["text"],
        "image": q.get("image"),
        "options": q["options"],
        "time_limit": time_limit,
        "option_type": q.get("option_type", "text"),
    })

    # 自動倒數計時
    game.timer_task = asyncio.create_task(_auto_reveal(time_limit))


async def _auto_reveal(time_limit: int):
    await asyncio.sleep(time_limit)
    if game.phase == "QUESTION":
        await reveal_answer()


async def reveal_answer():
    if game.phase != "QUESTION":
        return

    if game.timer_task and not game.timer_task.done():
        game.timer_task.cancel()

    game.phase = "REVEAL"
    q = game.current_question
    correct = q["correct"]
    distribution = game.get_answer_distribution()

    # 計分
    now = time.time()
    for conn_id, opt in game.answers.items():
        if conn_id not in game.players:
            continue
        if opt == correct:
            elapsed = now - game.question_start_time
            pts = game.calc_score(elapsed)
            game.players[conn_id]["score"] += pts
            game.players[conn_id]["last_points"] = pts
        else:
            game.players[conn_id]["last_points"] = 0

    leaderboard = game.get_leaderboard()

    reveal_payload = {
        "type": "answer_reveal",
        "correct": correct,
        "correct_text": q["options"][correct],
        "distribution": distribution,
        "reveal_media": q.get("reveal_media"),  # {"type":"image"|"video", "url":"..."}
        "leaderboard": leaderboard,
        "total_answered": len(game.answers),
        "total_players": len(game.players),
    }
    await game.broadcast_displays_hosts(reveal_payload)

    # 通知每個玩家個人成績
    for conn_id, p in game.players.items():
        answered = conn_id in game.answers
        correct_ans = game.answers.get(conn_id) == correct
        await game.send_to(p["ws"], {
            "type": "personal_result",
            "correct": correct_ans,
            "points_earned": p.get("last_points", 0),
            "total_score": p["score"],
            "answered": answered,
        })


async def show_leaderboard():
    if game.phase != "REVEAL":
        return
    game.phase = "LEADERBOARD"
    await game.broadcast_all({
        "type": "leaderboard",
        "leaderboard": game.get_leaderboard(),
        "is_final": False,
    })


async def next_question():
    if game.phase != "LEADERBOARD":
        return
    game.current_q_index += 1
    if game.current_q_index >= len(game.questions):
        await finish_game()
    else:
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
    game.question_start_time = None
    game.answers = {}
    game.players = {}
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

        elif conn_type == "player":
            nickname = msg.get("nickname", "").strip()[:20]
            if not nickname:
                await game.send_to(websocket, {"type": "error", "msg": "請輸入暱稱"})
                return
            # 暱稱重複檢查
            existing_names = [p["nickname"] for p in game.players.values()]
            if nickname in existing_names:
                nickname = f"{nickname}_{conn_id[-3:]}"

            game.players[conn_id] = {
                "nickname": nickname,
                "score": 0,
                "last_points": 0,
                "ws": websocket,
            }
            await game.send_to(websocket, {
                "type": "joined",
                "nickname": nickname,
                "phase": game.phase,
            })
            await game.broadcast_player_count()
            # 如果已在進行中，送當前題目
            if game.phase == "QUESTION" and game.current_question:
                q = game.current_question
                elapsed = time.time() - game.question_start_time
                remaining = max(0, q.get("time_limit", 20) - elapsed)
                await game.send_to(websocket, {
                    "type": "question_start",
                    "index": game.current_q_index,
                    "total": len(game.questions),
                    "text": q["text"],
                    "image": q.get("image"),
                    "options": q["options"],
                    "time_limit": q.get("time_limit", 20),
                    "remaining": remaining,
                    "option_type": q.get("option_type", "text"),
                })
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
                        await game.send_to(websocket, {"type": "answer_received", "option": opt})
                        # 通知 host/display 答題人數
                        await game.broadcast_displays_hosts({
                            "type": "answer_progress",
                            "answered": len(game.answers),
                            "total": len(game.players),
                        })
                        # 所有人都答了就自動揭曉
                        if len(game.answers) >= len(game.players) and len(game.players) > 0:
                            if game.timer_task and not game.timer_task.done():
                                game.timer_task.cancel()
                            await reveal_answer()

            # Host 指令
            elif conn_type == "host":
                if action == "start_game":
                    await start_game()
                elif action == "reveal_answer":
                    await reveal_answer()
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
            del game.players[conn_id]
            await game.broadcast_player_count()


# ─────────────────────────────────────────────
# 頁面路由
# ─────────────────────────────────────────────
@app.get("/display", response_class=HTMLResponse)
async def display_page():
    return HTMLResponse((STATIC_DIR / "display.html").read_text("utf-8"))

@app.get("/play", response_class=HTMLResponse)
async def play_page():
    return HTMLResponse((STATIC_DIR / "play.html").read_text("utf-8"))

@app.get("/host", response_class=HTMLResponse)
async def host_page():
    return HTMLResponse((STATIC_DIR / "host.html").read_text("utf-8"))

@app.get("/")
async def root():
    return {"status": "婚禮問答遊戲運行中 🎮", "play": "/play", "display": "/display", "host": "/host"}

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
