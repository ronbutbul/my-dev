from __future__ import annotations

import asyncio
import json
import logging
from typing import Set

import websockets
from kafka import KafkaProducer, KafkaConsumer

from config import get_str


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class Gateway:
    """
    WebSocket gateway between clients and Kafka.

    - Receives raw move JSON from clients over WebSocket and forwards it to
      Kafka topic `game-moves` (or KAFKA_MOVES_TOPIC).
    - Subscribes to `game-state-updates` and broadcasts each state snapshot to
      all connected clients as JSON over WebSocket.

    The gateway is intentionally "dumb": it does not validate moves or apply
    game rules; it simply forwards messages.
    """

    def __init__(self) -> None:
        bootstrap_servers = get_str("KAFKA_BOOTSTRAP_SERVERS")
        if not bootstrap_servers:
            raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS is required")

        self._moves_topic = get_str("KAFKA_MOVES_TOPIC", "game-moves")
        self._state_topic = get_str("KAFKA_STATE_TOPIC", "game-state-updates")

        self._producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )

        self._consumer = KafkaConsumer(
            self._state_topic,
            bootstrap_servers=bootstrap_servers,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            enable_auto_commit=True,
            auto_offset_reset="earliest",
            group_id=get_str(
                "GATEWAY_CONSUMER_GROUP", "gateway-service-state-consumer"
            ),
        )

        self._clients: Set[websockets.WebSocketServerProtocol] = set()

    async def _handle_client(
        self, websocket: websockets.WebSocketServerProtocol, path: str = ""
    ) -> None:
        """Handle a new WebSocket client connection."""
        remote = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        self._clients.add(websocket)
        logger.info("Client connected from %s; total clients = %d", remote, len(self._clients))
        try:
            # Keep connection alive and handle messages
            async for message in websocket:
                try:
                    payload = json.loads(message)
                    logger.debug("Received message from %s: %s", remote, payload)
                except json.JSONDecodeError:
                    logger.warning("Ignoring non-JSON message from %s: %s", remote, message)
                    continue

                # Forward the payload to Kafka
                self._producer.send(self._moves_topic, value=payload)
                self._producer.flush()
                logger.debug("Forwarded message to Kafka topic %s", self._moves_topic)
        except websockets.exceptions.ConnectionClosed:
            logger.info("Client %s disconnected normally", remote)
        except Exception as exc:
            logger.exception("Error handling client %s: %s", remote, exc)
        finally:
            self._clients.discard(websocket)
            logger.info("Client %s removed; total clients = %d", remote, len(self._clients))

    def _start_broadcast_states(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Start consuming state updates from Kafka and broadcast to WebSocket clients.
        
        The Kafka consumer is BLOCKING (synchronous), so we run it in an executor thread.
        This function starts the background thread but doesn't wait for it.
        The _consume() function runs forever, so we never await its completion.
        """
        def _consume() -> None:
            try:
                for message in self._consumer:
                    state = message.value
                    game_id = state.get("gameId", "unknown")
                    logger.info(
                        "Gateway received state update for game %s: status=%s, turn=%s, clients=%d",
                        game_id,
                        state.get("status"),
                        state.get("currentTurn"),
                        len(self._clients)
                    )
                    text = json.dumps(state)
                    # Schedule sending on the asyncio loop from the executor thread
                    asyncio.run_coroutine_threadsafe(
                        self._broadcast_text(text), loop=loop
                    )
            except Exception as exc:
                logger.exception("Error in Kafka consumer: %s", exc)

        # Run blocking consumer in executor thread - returns Future but we don't await
        # because _consume() runs forever. The executor thread handles the blocking I/O.
        loop.run_in_executor(None, _consume)

    async def _broadcast_text(self, text: str) -> None:
        if not self._clients:
            return
        await asyncio.gather(
            *[self._safe_send(client, text) for client in list(self._clients)]
        )

    @staticmethod
    async def _safe_send(
        websocket: websockets.WebSocketServerProtocol, text: str
    ) -> None:
        try:
            await websocket.send(text)
        except Exception:
            # If sending fails, just ignore; connection cleanup happens elsewhere.
            logger.exception("Failed to send state to client")

    async def run(self) -> None:
        host = get_str("GATEWAY_HOST", "0.0.0.0") or "0.0.0.0"
        port = int(get_str("GATEWAY_PORT", "8080") or "8080")

        server = await websockets.serve(self._handle_client, host, port)
        logger.info("Gateway WebSocket server listening on %s:%s", host, port)

        # Start Kafka consumer in background thread
        # KafkaConsumer is BLOCKING, so we run it in executor to not block event loop
        loop = asyncio.get_running_loop()
        self._start_broadcast_states(loop)
        
        # Keep server running - this handles WebSocket connections
        await server.wait_closed()


async def main() -> None:
    gateway = Gateway()
    await gateway.run()


if __name__ == "__main__":
    asyncio.run(main())


