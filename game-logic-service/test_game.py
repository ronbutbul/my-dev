import unittest

from game import TicTacToeGame, InvalidMoveError, GameStatus


class TicTacToeGameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = TicTacToeGame("game-1", "player1", "player2")

    def test_initial_state(self) -> None:
        state = self.game.state
        self.assertEqual(state.status, GameStatus.RUNNING)
        self.assertEqual(state.current_turn, "X")
        self.assertEqual(state.board, [[None, None, None]] * 3)

    def test_valid_move_alternates_turns(self) -> None:
        # player1 (X) moves
        self.game.handle_move(player_id="player1", row=0, col=0)
        self.assertEqual(self.game.state.current_turn, "O")

        # player2 (O) moves
        self.game.handle_move(player_id="player2", row=1, col=1)
        self.assertEqual(self.game.state.current_turn, "X")

    def test_invalid_when_not_players_turn(self) -> None:
        with self.assertRaises(InvalidMoveError):
            # player2 (O) tries to move first when it's X's turn
            self.game.handle_move(player_id="player2", row=0, col=0)

    def test_prevent_playing_in_occupied_cell(self) -> None:
        self.game.handle_move(player_id="player1", row=0, col=0)
        with self.assertRaises(InvalidMoveError):
            self.game.handle_move(player_id="player2", row=0, col=0)

    def test_win_horizontal(self) -> None:
        # X wins on the top row
        self.game.handle_move(player_id="player1", row=0, col=0)  # X
        self.game.handle_move(player_id="player2", row=1, col=0)  # O
        self.game.handle_move(player_id="player1", row=0, col=1)  # X
        self.game.handle_move(player_id="player2", row=1, col=1)  # O
        final_state = self.game.handle_move(player_id="player1", row=0, col=2)  # X

        self.assertEqual(final_state.status, GameStatus.GAME_OVER)
        self.assertEqual(final_state.winner, "X")

        # No further moves allowed
        with self.assertRaises(InvalidMoveError):
            self.game.handle_move(player_id="player2", row=2, col=2)

    def test_draw(self) -> None:
        # Play out a draw scenario (board full, no winner)
        # Final board: X O X / O O X / X X O
        moves = [
            ("player1", 0, 0),  # X
            ("player2", 0, 1),  # O
            ("player1", 0, 2),  # X
            ("player2", 1, 0),  # O
            ("player1", 1, 2),  # X
            ("player2", 1, 1),  # O
            ("player1", 2, 0),  # X
            ("player2", 2, 2),  # O
            ("player1", 2, 1),  # X
        ]

        for player_id, row, col in moves:
            self.game.handle_move(player_id=player_id, row=row, col=col)

        self.assertEqual(self.game.state.status, GameStatus.DRAW)
        self.assertIsNone(self.game.state.winner)

    def test_state_to_dict_matches_readme_shape(self) -> None:
        state_dict = self.game.state.to_dict()
        # to_dict() returns camelCase keys to match README format
        self.assertIn("gameId", state_dict)
        self.assertIn("status", state_dict)
        self.assertIn("currentTurn", state_dict)
        self.assertIn("board", state_dict)
        self.assertIn("players", state_dict)


if __name__ == "__main__":
    unittest.main()


