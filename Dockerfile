FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies needed for snowflake-connector-python
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application code (uses .dockerignore to exclude secrets)
COPY . .

# On the VM, environment variables come from /etc/scout.env
# The bot reads from os.environ directly

CMD ["python", "scout_bot.py"]
