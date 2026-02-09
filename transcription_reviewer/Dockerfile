FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ src/
COPY entrypoint.py .

# Install dependencies
RUN uv sync --frozen --no-dev

# Environment variables will be loaded from mounted .env file
ENV DOTENV_PATH=/app/.env

# Run the handler
CMD ["uv", "run", "python", "entrypoint.py"]
