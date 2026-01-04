# Frontend Local Testing Guide (Ubuntu)

## Prerequisites

### 1. Install Node.js and npm

On Ubuntu, you have a few options:

**Option A: Using NodeSource (Recommended - Latest LTS)**
```bash
# Install Node.js 20.x LTS
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Verify installation
node --version
npm --version
```

**Option B: Using Ubuntu's package manager (Older version)**
```bash
sudo apt update
sudo apt install nodejs npm

# Verify installation
node --version
npm --version
```

**Option C: Using nvm (Node Version Manager - Most flexible)**
```bash
# Install nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash

# Reload your shell
source ~/.bashrc

# Install Node.js LTS
nvm install --lts
nvm use --lts

# Verify installation
node --version
npm --version
```

## Running the Frontend

### Step 1: Navigate to frontend directory
```bash
cd /home/ronbut/games/tic-tac-toe/frontend
```

### Step 2: Install dependencies
```bash
npm install
```

This will install:
- React and React DOM
- TypeScript
- Webpack and webpack-dev-server
- All other dependencies listed in `package.json`

### Step 3: Start the development server
```bash
npm start
```

This will:
- Start webpack-dev-server on port 3000
- Automatically open your browser to `http://localhost:3000`
- Enable hot-reloading (changes refresh automatically)

### Step 4: Access the frontend

Open your browser and go to:
```
http://localhost:3000
```

## Testing Without Backend

The frontend will try to connect to `ws://localhost:8080` (the Gateway Service).

**If you don't have the backend running yet**, you can:

1. **Test the UI only**: The frontend will show "Waiting for game state..." and you can see the board UI, but clicking won't do anything (WebSocket connection will fail).

2. **Use browser console**: Open browser DevTools (F12) to see WebSocket connection errors.

3. **Mock the backend later**: Once you have the Gateway Service running, it will work automatically.

## Testing With Backend

To test the full flow, you'll need:

1. **Kafka running** (or use Docker):
   ```bash
   # Using Docker (easiest)
   docker run -d --name kafka -p 9092:9092 apache/kafka:latest
   ```

2. **Gateway Service running**:
   ```bash
   cd /home/ronbut/games/tic-tac-toe/gateway-service
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   # Set environment variables
   export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
   python app.py
   ```

3. **Game Logic Service running**:
   ```bash
   cd /home/ronbut/games/tic-tac-toe/game-logic-service
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
   python -m service
   ```

4. **Frontend running** (from this directory):
   ```bash
   npm start
   ```

## Troubleshooting

### Port 3000 already in use
```bash
# Find what's using port 3000
sudo lsof -i :3000

# Kill the process or change webpack port in webpack.config.js
```

### npm install fails
```bash
# Clear npm cache
npm cache clean --force

# Delete node_modules and package-lock.json, then reinstall
rm -rf node_modules package-lock.json
npm install
```

### WebSocket connection fails
- Make sure Gateway Service is running on port 8080
- Check browser console for errors
- Verify `REACT_APP_GATEWAY_WS_URL` environment variable if you set one

### TypeScript errors
```bash
# Reinstall TypeScript
npm install --save-dev typescript @types/react @types/react-dom
```

## Quick Start (Just UI Testing)

If you just want to see the frontend UI without the backend:

```bash
cd /home/ronbut/games/tic-tac-toe/frontend
npm install
npm start
```

Then open `http://localhost:3000` in your browser. The UI will load, but WebSocket will fail (which is expected without the backend).

