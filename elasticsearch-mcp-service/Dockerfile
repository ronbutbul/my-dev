FROM python:3.10-slim

# Apply Debian security fixes released after the base image was built
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .
# The base image ships an old setuptools that vendors vulnerable jaraco.context and wheel.
# pip is removed afterwards: it is not needed at runtime and vendors its own copies of urllib3, msgpack, etc.
RUN pip install --no-cache-dir --upgrade setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y pip

# Copy application code
COPY config.py .
COPY service.py .

# Expose API port
EXPOSE 8000

# Run the service
CMD ["python", "-m", "service"]

