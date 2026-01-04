import React, { useEffect, useState } from "react";

type PlayerSymbol = "X" | "O" | null;

type GameState = {
  gameId: string;
  status: "RUNNING" | "GAME_OVER" | "DRAW";
  currentTurn: "X" | "O";
  board: PlayerSymbol[][];
  players: Record<string, "X" | "O">;
  winner?: "X" | "O" | null;
};

const defaultBoard: PlayerSymbol[][] = [
  [null, null, null],
  [null, null, null],
  [null, null, null]
];

export const App: React.FC = () => {
  const [socket, setSocket] = useState<WebSocket | null>(null);
  const [gameState, setGameState] = useState<GameState | null>(null);
  const [playerId, setPlayerId] = useState<string>("player1");
  const [gameId, setGameId] = useState<string>("game-1");
  const [wsStatus, setWsStatus] = useState<string>("Disconnected");

  useEffect(() => {
    // Connect to Gateway Service - use config from window.__APP_CONFIG__ or fallback
    // The config is loaded from /config.js which is generated at runtime from ConfigMap
    const config = (window as any).__APP_CONFIG__ || {};
    const defaultPort = 9000; // Default port, but should come from config
    const wsUrl = config.gatewayWsUrl || `ws://${window.location.hostname}:${defaultPort}`;
    const url = wsUrl.startsWith('ws://') || wsUrl.startsWith('wss://') 
      ? wsUrl 
      : `ws://${wsUrl}`;

    console.log("Connecting to WebSocket:", url);
    const ws = new WebSocket(url);

    ws.onopen = () => {
      console.log("WebSocket connected");
      setWsStatus("Connected");
    };

    ws.onerror = (error) => {
      console.error("WebSocket error:", error);
      setWsStatus("Error - check console");
    };

    ws.onclose = (event) => {
      console.log("WebSocket closed:", event.code, event.reason);
      setWsStatus(`Disconnected (${event.code})`);
    };

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        console.log("Received message:", payload);
        // Frontend is dumb: it renders whatever state it receives.
        // Only update if this state is for our current gameId
        if (payload && payload.board && payload.status) {
          // Check gameId match, but also accept if gameId is missing (shouldn't happen but be defensive)
          if (!payload.gameId || payload.gameId === gameId) {
            console.log("Updating game state:", payload);
            setGameState(payload as GameState);
          } else {
            console.log("Ignoring state for different game:", payload.gameId, "expected:", gameId);
          }
        } else {
          console.log("Received message without board/status:", payload);
        }
      } catch (error) {
        console.error("Failed to parse message:", error);
      }
    };

    setSocket(ws);

    return () => {
      console.log("Closing WebSocket");
      ws.close();
    };
  }, [gameId]);

  const handleCellClick = (row: number, col: number) => {
    if (!socket) {
      console.error("WebSocket not initialized");
      return;
    }
    
    if (socket.readyState !== WebSocket.OPEN) {
      console.error("WebSocket not open. State:", socket.readyState);
      return;
    }

    const payload = {
      gameId,
      playerId,
      row,
      col
    };

    console.log("Sending move:", payload);
    socket.send(JSON.stringify(payload));
    console.log("Move sent successfully");
  };

  const handleRestart = () => {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      console.error("WebSocket not open");
      return;
    }

    const payload = {
      gameId,
      playerId,
      action: "restart"
    };

    console.log("Sending restart request:", payload);
    socket.send(JSON.stringify(payload));
  };

  const board = gameState?.board || defaultBoard;

  const renderStatus = () => {
    if (!gameState) {
      return "Waiting for game state...";
    }
    if (gameState.status === "GAME_OVER") {
      return `Game over. Winner: ${gameState.winner ?? "unknown"}`;
    }
    if (gameState.status === "DRAW") {
      return "Draw.";
    }
    return `Current turn: ${gameState.currentTurn}`;
  };

  return (
    <div style={{ fontFamily: "sans-serif", padding: 16 }}>
      <h1>Multiplayer Tic-Tac-Toe</h1>
      <div style={{ marginBottom: 8 }}>
        <label>
          Player ID:{" "}
          <input
            value={playerId}
            onChange={(e) => setPlayerId(e.target.value)}
          />
        </label>
      </div>
      <div style={{ marginBottom: 8 }}>
        <label>
          Game ID:{" "}
          <input value={gameId} onChange={(e) => setGameId(e.target.value)} />
        </label>
      </div>
      <div style={{ marginBottom: 8 }}>
        <strong>WebSocket:</strong> {wsStatus}
      </div>
      <div style={{ marginBottom: 16 }}>{renderStatus()}</div>
      <div style={{ marginBottom: 16 }}>
        <button
          onClick={handleRestart}
          style={{
            padding: "8px 16px",
            fontSize: 16,
            cursor: "pointer",
            backgroundColor: "#4CAF50",
            color: "white",
            border: "none",
            borderRadius: 4
          }}
        >
          Restart Game
        </button>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 80px)",
          gridTemplateRows: "repeat(3, 80px)",
          gap: 4
        }}
      >
        {board.map((row, r) =>
          row.map((cell, c) => (
            <button
              key={`${r}-${c}`}
              onClick={() => handleCellClick(r, c)}
              style={{
                width: 80,
                height: 80,
                fontSize: 32,
                cursor: "pointer"
              }}
            >
              {cell ?? ""}
            </button>
          ))
        )}
      </div>
    </div>
  );
};


