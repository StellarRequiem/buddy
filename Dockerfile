FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv --quiet

# Copy project files
COPY pyproject.toml ./
COPY buddy/ ./buddy/

# Install dependencies
RUN uv pip install --system -e . --quiet

# Create vault directory
RUN mkdir -p /root/BuddyVault

EXPOSE 7437

CMD ["python", "-m", "buddy.main"]
