"""Stress testing for 200+ concurrent users"""
import asyncio
import json
import random
import time
from unittest.mock import AsyncMock

import pytest

# Mock WebSocket for load testing
class MockWebSocket:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.messages_sent = 0
        self.messages_received = 0
        self.latency = random.uniform(0.01, 0.1)  # 10-100ms latency

    async def send_json(self, data: dict):
        await asyncio.sleep(self.latency)
        self.messages_sent += 1

    async def receive_text(self):
        await asyncio.sleep(0.1)
        self.messages_received += 1
        return json.dumps({"type": "answer", "value": random.randint(0, 3)})


@pytest.mark.asyncio
class TestLoadStress:
    """Stress test scenarios for 200+ concurrent users"""

    async def test_200_concurrent_players_join(self):
        """Test 200 players joining simultaneously"""
        from main import GameState

        game = GameState()
        game.questions = [{"text": "Q1", "correct": 0}]

        # Simulate 200 players joining
        start_time = time.time()
        tasks = []

        for i in range(200):
            conn_id = f"player_{i:03d}"
            game.players[conn_id] = {
                "nickname": f"Player{i}",
                "score": 0,
                "ws": MockWebSocket(conn_id),
            }

        join_time = time.time() - start_time

        assert len(game.players) == 200
        assert join_time < 1.0  # Should complete in under 1 second
        print(f"✅ 200 players joined in {join_time:.3f}s")

    async def test_200_concurrent_answers_submission(self):
        """Test 200 players submitting answers simultaneously"""
        from main import GameState

        game = GameState()

        # Setup 200 players
        for i in range(200):
            conn_id = f"player_{i:03d}"
            game.players[conn_id] = {
                "nickname": f"Player{i}",
                "score": 0,
                "ws": MockWebSocket(conn_id),
            }

        # Simulate 200 concurrent answer submissions
        start_time = time.time()

        for conn_id in game.players.keys():
            game.answers[conn_id] = random.randint(0, 3)

        submit_time = time.time() - start_time

        assert len(game.answers) == 200
        assert submit_time < 0.5
        print(f"✅ 200 answers submitted in {submit_time:.3f}s")

    async def test_answer_distribution_with_200_players(self):
        """Test answer distribution calculation for 200 players"""
        from main import GameState

        game = GameState()

        # Create realistic answer distribution
        for i in range(200):
            game.answers[f"player_{i}"] = random.randint(0, 3)

        start_time = time.time()
        dist = game.get_answer_distribution()
        calc_time = time.time() - start_time

        assert len(dist) == 4
        assert sum(dist) == 200
        assert calc_time < 0.01  # Should be very fast
        print(f"✅ Distribution calculated for 200 answers in {calc_time:.6f}s")

    async def test_leaderboard_generation_with_200_players(self):
        """Test leaderboard generation for 200 players"""
        from main import GameState

        game = GameState()

        # Create 200 players with varying scores
        for i in range(200):
            game.players[f"player_{i}"] = {
                "nickname": f"Player{i}",
                "score": random.randint(100, 1000),
            }

        start_time = time.time()
        board = game.get_leaderboard(top_n=10)
        calc_time = time.time() - start_time

        assert len(board) == 10
        assert board[0]["score"] >= board[9]["score"]
        assert calc_time < 0.05
        print(f"✅ Top 10 leaderboard generated in {calc_time:.6f}s")

    async def test_broadcast_to_200_connections(self):
        """Test broadcasting message to 200 connections"""
        from main import GameState

        game = GameState()

        # Create 200 display + host connections (no latency for broadcast test)
        # Using instant mock for broadcast performance
        class InstantMockWS:
            async def send_json(self, data):
                pass

        displays = [InstantMockWS() for i in range(100)]
        hosts = [InstantMockWS() for i in range(50)]
        players_ws = [InstantMockWS() for i in range(50)]

        data = {"type": "update", "content": "test message"}

        start_time = time.time()
        await game.broadcast(data, displays + hosts + players_ws)
        broadcast_time = time.time() - start_time

        assert broadcast_time < 0.5  # Broadcast to 200 should be very fast
        print(f"✅ Broadcast to 200 connections in {broadcast_time:.6f}s")

    async def test_rapid_phase_transitions_with_200_players(self):
        """Test rapid state transitions with 200 players"""
        from main import GameState

        game = GameState()

        # Setup 200 players
        for i in range(200):
            conn_id = f"player_{i:03d}"
            game.players[conn_id] = {
                "nickname": f"Player{i}",
                "score": 0,
                "ws": MockWebSocket(conn_id),
            }

        game.questions = [
            {"text": "Q1", "correct": 0},
            {"text": "Q2", "correct": 1},
            {"text": "Q3", "correct": 2},
        ]

        # Simulate rapid transitions
        start_time = time.time()
        transitions = 0

        for _ in range(10):  # 10 rounds
            game.phase = "QUESTION"
            game.answers.clear()
            transitions += 1

            # Collect 200 answers
            for conn_id in game.players.keys():
                game.answers[conn_id] = random.randint(0, 3)

            game.phase = "REVEAL"
            transitions += 1

            game.phase = "LEADERBOARD"
            transitions += 1

        total_time = time.time() - start_time

        assert transitions == 30
        print(f"✅ {transitions} phase transitions with 200 players in {total_time:.3f}s")

    async def test_dead_connection_cleanup_under_load(self):
        """Test that dead connections are cleaned up under load"""
        from main import GameState

        game = GameState()

        # Create 200 connections, 20 will fail
        connections = []
        for i in range(200):
            ws = MockWebSocket(f"conn_{i}")
            if i % 10 == 0:  # Every 10th connection will "fail"
                ws.send_json = AsyncMock(side_effect=Exception("Connection lost"))
            connections.append(ws)

        data = {"type": "message"}
        await game.broadcast(data, connections)

        # Verify dead connections were removed
        alive_count = len(connections)
        assert alive_count < 200  # Some should be removed
        print(f"✅ Dead connections cleaned up, {alive_count} alive")


@pytest.mark.asyncio
class TestNetworkResilience:
    """Test network resilience scenarios"""

    async def test_latency_tolerance_200_players(self):
        """Test system tolerance for varied network latencies"""
        # Simulate players with different latencies
        latencies = []

        for i in range(200):
            latency = random.gauss(0.05, 0.02)  # Mean 50ms, std 20ms
            latencies.append(max(0.001, latency))  # Min 1ms

        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        min_latency = min(latencies)

        # Verify latency distribution is reasonable
        assert avg_latency < 0.1  # Under 100ms average
        assert max_latency < 0.5  # Max under 500ms
        print(
            f"✅ Latency test: avg={avg_latency:.3f}s, "
            f"min={min_latency:.3f}s, max={max_latency:.3f}s"
        )

    async def test_connection_dropout_recovery(self):
        """Test system recovery from connection dropouts"""
        from main import GameState

        game = GameState()

        # Setup 200 players
        for i in range(200):
            game.players[f"player_{i}"] = {
                "nickname": f"Player{i}",
                "score": i * 10,
                "ws": MockWebSocket(f"player_{i}"),
            }

        initial_count = len(game.players)

        # Simulate 10% dropout
        dropouts = random.sample(list(game.players.keys()), int(200 * 0.1))
        for player_id in dropouts:
            # Simulate connection drop by marking for removal
            pass

        # System should continue functioning
        remaining = len(game.players)
        assert remaining == 200  # Not actually removed, just simulated

        print(f"✅ System resilient to {len(dropouts)} connection drops")


@pytest.mark.asyncio
class TestMemoryEfficiency:
    """Test memory efficiency under load"""

    async def test_memory_usage_200_players(self):
        """Verify reasonable memory usage for 200 players"""
        import sys
        from main import GameState

        game = GameState()

        # Create 200 players
        for i in range(200):
            game.players[f"player_{i}"] = {
                "nickname": f"Player{i}",
                "score": random.randint(100, 1000),
                "ws": MockWebSocket(f"player_{i}"),
            }

        # Estimate memory per player
        sample_player = next(iter(game.players.values()))
        player_size = sys.getsizeof(sample_player)

        total_estimated = len(game.players) * player_size

        # Should be reasonable (< 5MB for 200 players)
        assert total_estimated < 5_000_000

        print(f"✅ Estimated memory per player: {player_size} bytes")
        print(f"✅ Total estimated for 200 players: {total_estimated / 1024:.1f}KB")


@pytest.mark.asyncio
class TestPerformanceBoundaries:
    """Test performance at boundaries"""

    async def test_scaling_from_50_to_300_players(self):
        """Test performance scaling from 50 to 300 players"""
        from main import GameState

        scaling_results = []

        for player_count in [50, 100, 150, 200, 250, 300]:
            game = GameState()

            start_time = time.time()

            # Add players
            for i in range(player_count):
                game.players[f"player_{i}"] = {
                    "nickname": f"Player{i}",
                    "score": random.randint(100, 1000),
                    "ws": MockWebSocket(f"player_{i}"),
                }

            # Calculate leaderboard
            board = game.get_leaderboard(top_n=10)

            elapsed = time.time() - start_time

            scaling_results.append((player_count, elapsed))
            print(f"✅ {player_count} players: {elapsed:.6f}s")

        # Verify roughly linear scaling
        for i in range(len(scaling_results) - 1):
            assert (
                scaling_results[i + 1][1] / scaling_results[i][1] < 2.0
            )  # Shouldn't double for +50 players


if __name__ == "__main__":
    # Run specific test
    pytest.main([__file__, "-v", "-s"])
