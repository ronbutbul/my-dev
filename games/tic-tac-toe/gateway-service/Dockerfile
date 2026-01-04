FROM python:3.10-slim

WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config.py .
COPY app.py .

# Expose WebSocket port
EXPOSE 9000

# Run the service
CMD ["python", "-m", "app"]

