import React, { useEffect, useState } from "react";
import { Bar } from "react-chartjs-2";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend,
} from "chart.js";

ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend);

type PlayerWins = { playerId: string; wins: number };
type StatsResponse = { totalWins: number; players: PlayerWins[] };

const getConfig = () => (window as any).__APP_CONFIG__ || {};

export const AdminPage: React.FC = () => {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const cfg = getConfig();
    const baseUrl = cfg.statsApiUrl || "";
    // In dev, use the webpack proxy path to avoid CORS
    const url = baseUrl ? (baseUrl.endsWith("/") ? baseUrl + "statistic" : baseUrl + "/statistic") : "/api/statistic";

    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: StatsResponse) => {
        setStats(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  if (loading) return <div style={styles.container}><p>Loading stats...</p></div>;
  if (error) return <div style={styles.container}><p style={{ color: "#e74c3c" }}>Error: {error}</p></div>;
  if (!stats) return null;

  const sorted = [...stats.players].sort((a, b) => b.wins - a.wins);

  const chartData = {
    labels: sorted.map((p) => p.playerId),
    datasets: [
      {
        label: "Wins",
        data: sorted.map((p) => p.wins),
        backgroundColor: sorted.map((_, i) => {
          const colors = ["#3498db", "#e74c3c", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c"];
          return colors[i % colors.length];
        }),
        borderRadius: 6,
      },
    ],
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      title: { display: true, text: "Wins per Player", font: { size: 20 } },
      legend: { display: false },
    },
    scales: {
      y: {
        beginAtZero: true,
        ticks: { stepSize: 1, font: { size: 14 } },
        title: { display: true, text: "Wins", font: { size: 14 } },
      },
      x: {
        ticks: { font: { size: 14 } },
        title: { display: true, text: "Player", font: { size: 14 } },
      },
    },
  };

  return (
    <div style={styles.container}>
      <h1 style={styles.heading}>Admin Dashboard</h1>
      <div style={styles.totalCard}>
        <span style={styles.totalLabel}>Total Games Won</span>
        <span style={styles.totalValue}>{stats.totalWins}</span>
      </div>
      <div style={styles.chartWrapper}>
        <Bar data={chartData} options={chartOptions} />
      </div>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>Rank</th>
            <th style={styles.th}>Player</th>
            <th style={styles.th}>Wins</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p, i) => (
            <tr key={p.playerId} style={i % 2 === 0 ? styles.rowEven : styles.rowOdd}>
              <td style={styles.td}>{i + 1}</td>
              <td style={styles.td}>{p.playerId}</td>
              <td style={styles.td}>{p.wins}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    fontFamily: "'Segoe UI', Tahoma, Geneva, Verdana, sans-serif",
    maxWidth: 700,
    margin: "0 auto",
    padding: 24,
  },
  heading: { textAlign: "center" as const, marginBottom: 24 },
  totalCard: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    background: "#f0f4f8",
    borderRadius: 8,
    padding: "16px 24px",
    marginBottom: 24,
  },
  totalLabel: { fontSize: 18, color: "#555" },
  totalValue: { fontSize: 32, fontWeight: 700, color: "#2c3e50" },
  chartWrapper: { height: 300, marginBottom: 32 },
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
    borderRadius: 8,
    overflow: "hidden",
  },
  th: {
    textAlign: "left" as const,
    padding: "10px 16px",
    background: "#34495e",
    color: "#fff",
    fontSize: 14,
  },
  td: { padding: "10px 16px", fontSize: 14 },
  rowEven: { background: "#f9f9f9" },
  rowOdd: { background: "#fff" },
};
