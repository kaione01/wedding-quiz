"""Integration tests for WebSocket functionality"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from main import GameState


class TestBroadcastFunctionality:
    """Test broadcasting messages to WebSocket connections"""

    @pytest.mark.asyncio
    async def test_broadcast_to_valid_connections(self):
        game = GameState()

        # Mock WebSocket connections
        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()

        data = {"type": "test", "content": "hello"}

        await game.broadcast(data, [mock_ws1, mock_ws2])

        mock_ws1.send_json.assert_called_once_with(data)
        mock_ws2.send_json.assert_called_once_with(data)

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_connections(self):
        game = GameState()

        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()
        mock_ws3 = AsyncMock()

        # Simulate ws2 throwing exception
        mock_ws2.send_json.side_effect = Exception("Connection lost")

        targets = [mock_ws1, mock_ws2, mock_ws3]
        data = {"type": "test"}

        await game.broadcast(data, targets)

        # ws2 should be removed from targets
        assert len(targets) == 2
        assert mock_ws2 not in targets

    @pytest.mark.asyncio
    async def test_broadcast_all_includes_all_endpoints(self):
        game = GameState()

        # Add players, displays, and hosts
        game.players = {
            "p1": {"nickname": "Player1", "score": 0, "ws": AsyncMock()},
            "p2": {"nickname": "Player2", "score": 0, "ws": AsyncMock()},
        }
        game.displays = [AsyncMock(), AsyncMock()]
        game.hosts = [AsyncMock()]

        data = {"type": "status"}
        await game.broadcast_all(data)

        # Verify all were called
        game.players["p1"]["ws"].send_json.assert_called_once()
        game.players["p2"]["ws"].send_json.assert_called_once()
        for display in game.displays:
            display.send_json.assert_called_once()
        for host in game.hosts:
            host.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_displays_hosts_only(self):
        game = GameState()

        # Add endpoints
        game.players = {"p1": {"nickname": "Player1", "score": 0, "ws": AsyncMock()}}
        game.displays = [AsyncMock()]
        game.hosts = [AsyncMock()]

        data = {"type": "game_status"}
        await game.broadcast_displays_hosts(data)

        # Only displays and hosts, not players
        assert game.displays[0].send_json.called
        assert game.hosts[0].send_json.called
        assert not game.players["p1"]["ws"].send_json.called

    @pytest.mark.asyncio
    async def test_broadcast_players_only(self):
        game = GameState()

        # Add endpoints
        game.players = {
            "p1": {"nickname": "Player1", "score": 0, "ws": AsyncMock()},
            "p2": {"nickname": "Player2", "score": 0, "ws": AsyncMock()},
        }
        game.displays = [AsyncMock()]

        data = {"type": "player_action"}
        await game.broadcast_players(data)

        # Only players, not displays
        assert game.players["p1"]["ws"].send_json.called
        assert game.players["p2"]["ws"].send_json.called
        assert not game.displays[0].send_json.called

    @pytest.mark.asyncio
    async def test_broadcast_players_removes_dead_connections(self):
        game = GameState()

        dead_ws = AsyncMock()
        dead_ws.send_json.side_effect = Exception("Dead connection")
        alive_ws = AsyncMock()

        game.players = {
            "dead": {"nickname": "DeadPlayer", "score": 0, "ws": dead_ws},
            "alive": {"nickname": "AlivePlayer", "score": 0, "ws": alive_ws},
        }

        await game.broadcast_players({"type": "test"})

        # Dead player should be removed
        assert "dead" not in game.players
        assert "alive" in game.players


class TestPlayerCountBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_player_count_sends_correct_data(self):
        game = GameState()

        game.players = {
            "p1": {"nickname": "Alice", "score": 100, "ws": AsyncMock()},
            "p2": {"nickname": "Bob", "score": 200, "ws": AsyncMock()},
        }
        game.displays = [AsyncMock()]
        game.hosts = [AsyncMock()]

        await game.broadcast_player_count()

        # Get the called data
        called_data = game.displays[0].send_json.call_args[0][0]

        assert called_data["type"] == "player_count"
        assert called_data["count"] == 2
        assert "Alice" in called_data["nicknames"]
        assert "Bob" in called_data["nicknames"]


class TestAnswerHandling:
    def test_player_answer_stored(self):
        game = GameState()

        game.answers["player1"] = 0
        game.answers["player2"] = 2

        assert game.answers["player1"] == 0
        assert game.answers["player2"] == 2

    def test_answers_cleared_for_new_question(self):
        game = GameState()

        game.answers = {"p1": 0, "p2": 1}
        game.answers.clear()

        assert len(game.answers) == 0


class TestGamePhaseTransition:
    def test_phase_transitions(self):
        game = GameState()

        assert game.phase == "LOBBY"
        game.phase = "QUESTION"
        assert game.phase == "QUESTION"
        game.phase = "REVEAL"
        assert game.phase == "REVEAL"
        game.phase = "LEADERBOARD"
        assert game.phase == "LEADERBOARD"
        game.phase = "FINISHED"
        assert game.phase == "FINISHED"
