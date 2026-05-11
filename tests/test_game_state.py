"""Unit tests for GameState class"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from main import GameState


class TestGameStateInitialization:
    def setup_method(self):
        self.game = GameState()

    def test_initial_phase_is_lobby(self):
        assert self.game.phase == "LOBBY"

    def test_initial_current_question_index(self):
        assert self.game.current_q_index == -1

    def test_initial_no_players(self):
        assert len(self.game.players) == 0

    def test_initial_empty_answers(self):
        assert len(self.game.answers) == 0


class TestCalculateScore:
    def setup_method(self):
        self.game = GameState()

    def test_score_at_zero_seconds(self):
        score = self.game.calc_score(0)
        assert score == 1000

    def test_score_at_five_seconds(self):
        score = self.game.calc_score(5)
        assert score == 750  # 1000 - (5 * 50)

    def test_score_at_ten_seconds(self):
        score = self.game.calc_score(10)
        assert score == 500  # 1000 - (10 * 50)

    def test_score_minimum_is_100(self):
        score = self.game.calc_score(20)
        assert score == 100  # 1000 - (20 * 50) = 0, but min is 100


class TestAnswerDistribution:
    def setup_method(self):
        self.game = GameState()

    def test_empty_distribution(self):
        dist = self.game.get_answer_distribution()
        assert dist == [0, 0, 0, 0]

    def test_single_answer(self):
        self.game.answers = {"player1": 0}
        dist = self.game.get_answer_distribution()
        assert dist == [1, 0, 0, 0]

    def test_multiple_answers_same_option(self):
        self.game.answers = {"player1": 1, "player2": 1, "player3": 1}
        dist = self.game.get_answer_distribution()
        assert dist == [0, 3, 0, 0]

    def test_all_options_selected(self):
        self.game.answers = {"p1": 0, "p2": 1, "p3": 2, "p4": 3}
        dist = self.game.get_answer_distribution()
        assert dist == [1, 1, 1, 1]

    def test_invalid_option_ignored(self):
        self.game.answers = {"p1": 0, "p2": 5}  # 5 is invalid
        dist = self.game.get_answer_distribution()
        assert dist == [1, 0, 0, 0]


class TestLeaderboard:
    def setup_method(self):
        self.game = GameState()

    def test_empty_leaderboard(self):
        board = self.game.get_leaderboard()
        assert board == []

    def test_single_player_leaderboard(self):
        self.game.players["conn1"] = {
            "nickname": "Player1",
            "score": 800,
            "last_points": 100
        }
        board = self.game.get_leaderboard()
        assert len(board) == 1
        assert board[0]["nickname"] == "Player1"
        assert board[0]["rank"] == 1
        assert board[0]["score"] == 800

    def test_leaderboard_sorted_by_score_descending(self):
        self.game.players = {
            "c1": {"nickname": "P1", "score": 500, "last_points": 0},
            "c2": {"nickname": "P2", "score": 1000, "last_points": 0},
            "c3": {"nickname": "P3", "score": 750, "last_points": 0},
        }
        board = self.game.get_leaderboard()
        assert board[0]["nickname"] == "P2"
        assert board[1]["nickname"] == "P3"
        assert board[2]["nickname"] == "P1"

    def test_leaderboard_top_n_limit(self):
        for i in range(10):
            self.game.players[f"c{i}"] = {
                "nickname": f"P{i}",
                "score": 1000 - i * 100,
                "last_points": 0
            }
        board = self.game.get_leaderboard(top_n=3)
        assert len(board) == 3
        assert board[0]["score"] == 1000
        assert board[2]["score"] == 800

    def test_full_leaderboard(self):
        for i in range(5):
            self.game.players[f"c{i}"] = {
                "nickname": f"P{i}",
                "score": 1000 - i * 100,
                "last_points": 0
            }
        board = self.game.get_full_leaderboard()
        assert len(board) == 5


class TestCurrentQuestion:
    def setup_method(self):
        self.game = GameState()

    def test_no_question_when_index_negative(self):
        assert self.game.current_question is None

    def test_load_question(self):
        self.game.questions = [{"text": "Q1", "correct": 0}]
        self.game.current_q_index = 0
        assert self.game.current_question["text"] == "Q1"

    def test_none_when_index_out_of_range(self):
        self.game.questions = [{"text": "Q1"}]
        self.game.current_q_index = 5
        assert self.game.current_question is None
