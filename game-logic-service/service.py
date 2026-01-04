from __future__ import annotations

import json
import logging
import time
from typing import Dict

from kafka import KafkaConsumer, KafkaProducer

from config import get_str
from game import TicTacToeGame, InvalidMoveError, GameStatus


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _create_consumer() -> KafkaConsumer:
    bootstrap_servers = get_str("KAFKA_BOOTSTRAP_SERVERS")
    if not bootstrap_servers:
        raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS environment variable is required")

    topic = get_str("KAFKA_MOVES_TOPIC", "game-moves")

    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        enable_auto_commit=True,
        auto_offset_reset="earliest",
        group_id=get_str("KAFKA_CONSUMER_GROUP", "game-logic-service"),
    )
    logger.info("Game Logic Service consuming moves from topic %s", topic)
    return consumer


def _create_producer() -> KafkaProducer:
    bootstrap_servers = get_str("KAFKA_BOOTSTRAP_SERVERS")
    if not bootstrap_servers:
        raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS environment variable is required")

    producer = KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    logger.info("Game Logic Service will publish state updates")
    return producer


def run() -> None:
    """
    Main loop for the Game Logic Service once Kafka is available.

    - Consumes move events from `game-moves` (or KAFKA_MOVES_TOPIC).
    - Maintains one TicTacToeGame instance per gameId.
    - Auto-creates games when the first move arrives for a new gameId.
    - Publishes authoritative game state snapshots to `game-state-updates`.
    """
    consumer = _create_consumer()
    producer = _create_producer()

    games: Dict[str, TicTacToeGame] = {}
    # Track which players have joined each game (for auto-creation)
    game_players: Dict[str, list[str]] = {}
    # Queue moves for games that don't exist yet (waiting for second player)
    pending_moves: Dict[str, list[Dict]] = {}
    state_topic = get_str("KAFKA_STATE_TOPIC", "game-state-updates")

    logger.info("Game Logic Service started")

    for message in consumer:
        move = message.value
        game_id = move.get("gameId")
        player_id = move.get("playerId")
        action = move.get("action")
        
        # Handle restart action
        if action == "restart":
            if not game_id or not player_id:
                logger.warning("Ignoring malformed restart event: %s", move)
                continue
            
            game = games.get(game_id)
            if game is None:
                logger.warning("Restart requested for non-existent game %s", game_id)
                continue
            
            # Reset the game
            logger.info("Restart requested for game %s by player %s", game_id, player_id)
            game.reset()
            reset_state = game.state.to_dict()
            producer.send(state_topic, value=reset_state)
            producer.flush()
            logger.info("Published reset state for game %s after restart request", game_id)
            continue
        
        # Handle regular move
        row = move.get("row")
        col = move.get("col")

        if not all(v is not None for v in (game_id, player_id, row, col)):
            logger.warning("Ignoring malformed move event: %s", move)
            continue

        game = games.get(game_id)
        
        # If game exists but has placeholder player, we need to handle new players
        if game is not None:
            placeholder_key = f"_waiting_{game_id}"
            if placeholder_key in game._state.players:
                # Game exists with placeholder - track this player if new
                if game_id not in game_players:
                    game_players[game_id] = []
                
                if player_id not in game_players[game_id]:
                    game_players[game_id].append(player_id)
                    logger.info(
                        "New player %s joined game %s. Total players: %d",
                        player_id,
                        game_id,
                        len(game_players[game_id])
                    )
                
                # If we now have 2 players, update the placeholder
                if len(game_players[game_id]) >= 2:
                    player1_id = game_players[game_id][0]
                    player2_id = game_players[game_id][1]
                    # Update players dict to replace placeholder
                    game._state.players = {
                        player1_id: "X",
                        player2_id: "O"
                    }
                    logger.info(
                        "Updated game %s players: replaced %s with %s. Players now: %s",
                        game_id,
                        placeholder_key,
                        player2_id,
                        game._state.players
                    )
        
        if game is None:
            # Auto-create game when first move arrives
            # Track players: first player becomes player1 (X), second becomes player2 (O)
            if game_id not in game_players:
                game_players[game_id] = []
                pending_moves[game_id] = []
            
            if player_id not in game_players[game_id]:
                game_players[game_id].append(player_id)
            
            # Queue this move
            pending_moves[game_id].append(move)
            
            # If we only have one player, create game with placeholder and publish state immediately
            if len(game_players[game_id]) == 1:
                player1_id = game_players[game_id][0]
                # Create game with placeholder for player2 (will be updated when they join)
                placeholder_player2 = f"_waiting_{game_id}"
                game = TicTacToeGame(game_id, player1_id, placeholder_player2)
                games[game_id] = game
                logger.info(
                    "Created game %s with player %s (X), waiting for second player",
                    game_id,
                    player1_id
                )
                # Process the first player's move immediately
                queued_move = pending_moves[game_id][0]
                queued_player_id = queued_move.get("playerId")
                queued_row = queued_move.get("row")
                queued_col = queued_move.get("col")
                try:
                    state = game.handle_move(
                        player_id=queued_player_id,
                        row=queued_row,
                        col=queued_col
                    )
                    state_payload = state.to_dict()
                    producer.send(state_topic, value=state_payload)
                    producer.flush()
                    logger.info(
                        "Published state for first player move in game %s: status=%s, currentTurn=%s",
                        game_id,
                        state_payload.get("status"),
                        state_payload.get("currentTurn")
                    )
                    # Remove the processed move from queue
                    pending_moves[game_id] = []
                except InvalidMoveError as exc:
                    logger.warning(
                        "Failed to process first player move for game %s: %s",
                        game_id,
                        exc
                    )
                continue
            
            # Now we have 2 players - update the game with real player2
            if len(game_players[game_id]) == 2:
                player1_id = game_players[game_id][0]
                player2_id = game_players[game_id][1]
                
                # If game exists with placeholder, update it
                if game_id in games:
                    game = games[game_id]
                    placeholder_key = f"_waiting_{game_id}"
                    # Update players dict to replace placeholder
                    old_players = game._state.players.copy()
                    game._state.players = {
                        player1_id: "X",
                        player2_id: "O"
                    }
                    logger.info(
                        "Updated game %s players: replaced %s with %s. Players now: %s",
                        game_id,
                        placeholder_key,
                        player2_id,
                        game._state.players
                    )
                else:
                    # Create new game
                    game = TicTacToeGame(game_id, player1_id, player2_id)
                    games[game_id] = game
                    logger.info(
                        "Auto-created game %s with players %s (X) and %s (O)",
                        game_id,
                        player1_id,
                        player2_id
                    )
                
                # Process any remaining queued moves (second player's move)
                for queued_move in pending_moves[game_id]:
                    queued_player_id = queued_move.get("playerId")
                    queued_row = queued_move.get("row")
                    queued_col = queued_move.get("col")
                    try:
                        state = game.handle_move(
                            player_id=queued_player_id,
                            row=queued_row,
                            col=queued_col
                        )
                        state_payload = state.to_dict()
                        producer.send(state_topic, value=state_payload)
                        producer.flush()
                        logger.info(
                            "Processed second player move for game %s: status=%s, currentTurn=%s",
                            game_id,
                            state_payload.get("status"),
                            state_payload.get("currentTurn")
                        )
                        time.sleep(0.1)
                    except InvalidMoveError as exc:
                        logger.info(
                            "Rejected queued move for game %s from player %s: %s",
                            game_id,
                            queued_player_id,
                            exc
                        )
                
                # Clear pending moves
                del pending_moves[game_id]
                continue

        # If game has ended (GAME_OVER or DRAW), reset it for a new round
        if game.state.status in (GameStatus.GAME_OVER, GameStatus.DRAW):
            logger.info(
                "Game %s has ended (%s). Resetting for new round.",
                game_id,
                game.state.status.value
            )
            game.reset()
            # Publish the reset state so players know a new game has started
            reset_state = game.state.to_dict()
            producer.send(state_topic, value=reset_state)
            producer.flush()
            logger.info("Published reset state for game %s", game_id)

        # After reset, allow new players to join by updating players dict
        player_was_updated = False
        if player_id not in game._state.players:
            player_was_updated = True
            # Check if we have space for new players (should have exactly 2)
            current_players = list(game._state.players.keys())
            old_turn = game._state.current_turn
            
            if len(current_players) >= 2:
                # Determine which player to replace based on the current turn
                # Find which player has the current turn symbol
                player_to_replace = None
                for p_id, symbol in game._state.players.items():
                    if symbol == old_turn:
                        player_to_replace = p_id
                        break
                
                if player_to_replace is None:
                    # Fallback: if we can't find the player, replace based on position
                    # This shouldn't happen, but defensive programming
                    player_to_replace = current_players[0] if old_turn == "X" else current_players[1]
                
                # Create new players dict with the replacement
                new_players = {}
                for p_id, symbol in game._state.players.items():
                    if p_id == player_to_replace:
                        # Replace this player with the new one, keeping the same symbol
                        new_players[player_id] = symbol
                    else:
                        # Keep the other player
                        new_players[p_id] = symbol
                
                game._state.players = new_players
                # Keep the turn the same since we replaced the player whose turn it is
                game._state.current_turn = old_turn
                logger.info(
                    "Updated game %s players: replaced %s (%s) with %s. Players now: %s, turn: %s",
                    game_id,
                    player_to_replace,
                    old_turn,
                    player_id,
                    game._state.players,
                    game._state.current_turn
                )
            elif len(current_players) == 1:
                # Add as second player
                game._state.players[player_id] = "O"
                logger.info(
                    "Added player %s to game %s. Players now: %s",
                    player_id,
                    game_id,
                    game._state.players
                )
        
        # After player update, if the board is empty (fresh reset), adjust turn to match the player making the move
        board_is_empty = all(cell is None for row in game._state.board for cell in row)
        if board_is_empty and player_id in game._state.players:
            player_symbol = game._state.players[player_id]
            # Set turn to match the player making the first move
            game._state.current_turn = player_symbol
            logger.info(
                "Adjusted turn for game %s to %s (first move after reset by player %s)",
                game_id,
                player_symbol,
                player_id
            )

        # Log current state before move validation for debugging
        if player_id in game._state.players:
            player_symbol = game._state.players[player_id]
            logger.info(
                "Before move: game %s, player %s (%s), current turn: %s, board empty: %s, player_updated: %s",
                game_id,
                player_id,
                player_symbol,
                game._state.current_turn,
                board_is_empty,
                player_was_updated
            )

        try:
            state = game.handle_move(player_id=player_id, row=row, col=col)
        except InvalidMoveError as exc:
            # The move is rejected according to the rules; we log and skip
            # publishing a new state snapshot. The authoritative state therefore
            # remains unchanged.
            logger.info(
                "Rejected move for game %s from player %s at (%s,%s): %s",
                game_id,
                player_id,
                row,
                col,
                exc,
            )
            continue

        state_payload = state.to_dict()
        producer.send(state_topic, value=state_payload)
        producer.flush()
        logger.info("Published updated state for game %s", game_id)


if __name__ == "__main__":
    run()


