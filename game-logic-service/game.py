from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Literal, Optional, Dict, Any


PlayerSymbol = Literal["X", "O"]
Cell = Optional[PlayerSymbol]


class GameStatus(str, Enum):
    RUNNING = "RUNNING"
    GAME_OVER = "GAME_OVER"
    DRAW = "DRAW"


@dataclass
class GameState:
    """
    Authoritative Tic-Tac-Toe game state.

    This structure is designed to match the JSON examples from the README:

    {
      "gameId": "game-123",
      "status": "RUNNING",
      "currentTurn": "X",
      "board": [
        ["X", "O", null],
        [null, "X", null],
        [null, "O", null]
      ],
      "players": {
        "player1": "X",
        "player2": "O"
      }
    }
    """

    game_id: str
    status: GameStatus
    current_turn: PlayerSymbol
    board: List[List[Cell]] = field(
        default_factory=lambda: [[None for _ in range(3)] for _ in range(3)]
    )
    players: Dict[str, PlayerSymbol] = field(default_factory=dict)
    winner: Optional[PlayerSymbol] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the game state to a JSON-serializable dictionary that follows the
        README examples (enums converted to strings, camelCase field names, etc.).
        """
        raw = asdict(self)
        # Convert to camelCase and handle enums
        result = {
            "gameId": raw["game_id"],
            "status": self.status.value,
            "currentTurn": raw["current_turn"],
            "board": raw["board"],
            "players": raw["players"],
        }
        if raw["winner"] is not None:
            result["winner"] = raw["winner"]
        return result


class InvalidMoveError(Exception):
    """Raised when a move is invalid according to the game rules."""


class TicTacToeGame:
    """
    Core Tic-Tac-Toe game logic.

    This class is intentionally pure Python and self-contained so it can be
    easily unit tested and later integrated behind Kafka consumers/producers.
    """

    def __init__(self, game_id: str, player1_id: str, player2_id: str) -> None:
        # By convention, player1 is X and player2 is O as described in the README.
        players = {
            player1_id: "X",
            player2_id: "O",
        }
        self._state = GameState(
            game_id=game_id,
            status=GameStatus.RUNNING,
            current_turn="X",
            board=[[None for _ in range(3)] for _ in range(3)],
            players=players,
            winner=None,
        )

    @property
    def state(self) -> GameState:
        """Return the current authoritative game state."""
        return self._state

    def reset(self) -> None:
        """
        Reset the game to initial state (new round).
        
        Keeps the same players but resets board, status, turn, and winner.
        """
        self._state.status = GameStatus.RUNNING
        self._state.current_turn = "X"
        self._state.board = [[None for _ in range(3)] for _ in range(3)]
        self._state.winner = None

    def handle_move(self, *, player_id: str, row: int, col: int) -> GameState:
        """
        Apply a move for the given player at (row, col).

        This enforces all of the rules from the README:
        - Only the player whose turn it is may move
        - The cell must be empty
        - No moves after GAME_OVER or DRAW
        """
        self._validate_move(player_id=player_id, row=row, col=col)

        symbol = self._state.players[player_id]
        self._state.board[row][col] = symbol

        # After placing, check win/draw and update status accordingly.
        if self._is_winning_move(symbol):
            self._state.status = GameStatus.GAME_OVER
            self._state.winner = symbol
        elif self._is_draw():
            self._state.status = GameStatus.DRAW
        else:
            # Switch turns only if the game is still running.
            self._switch_turn()

        return self._state

    def _validate_move(self, *, player_id: str, row: int, col: int) -> None:
        # Game must still be running
        if self._state.status in (GameStatus.GAME_OVER, GameStatus.DRAW):
            raise InvalidMoveError("No moves allowed after the game has ended.")

        # Player must be part of this game
        if player_id not in self._state.players:
            raise InvalidMoveError("Unknown player for this game.")

        expected_symbol = self._state.current_turn
        actual_symbol = self._state.players[player_id]

        # Correct player's turn
        if actual_symbol != expected_symbol:
            raise InvalidMoveError("It is not this player's turn.")

        # Row/column must be on the 3x3 board
        if not (0 <= row < 3 and 0 <= col < 3):
            raise InvalidMoveError("Move is outside the board.")

        # Cell must be empty
        if self._state.board[row][col] is not None:
            raise InvalidMoveError("Cell is already occupied.")

    def _switch_turn(self) -> None:
        self._state.current_turn = "O" if self._state.current_turn == "X" else "X"

    def _is_winning_move(self, symbol: PlayerSymbol) -> bool:
        b = self._state.board

        # Rows
        for r in range(3):
            if b[r][0] == b[r][1] == b[r][2] == symbol:
                return True

        # Columns
        for c in range(3):
            if b[0][c] == b[1][c] == b[2][c] == symbol:
                return True

        # Diagonals
        if b[0][0] == b[1][1] == b[2][2] == symbol:
            return True
        if b[0][2] == b[1][1] == b[2][0] == symbol:
            return True

        return False

    def _is_draw(self) -> bool:
        # A draw occurs when the board is full and nobody has won.
        if any(cell is None for row in self._state.board for cell in row):
            return False
        # If board is full and we haven't declared a winner, it's a draw.
        return True


