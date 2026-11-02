FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
COPY src ./src
COPY tests ./tests
COPY proof ./proof
COPY traces ./traces
COPY scout ./scout
COPY docs ./docs
COPY tools ./tools
COPY .env.example ./

RUN uv sync --frozen

# Default command verifies the offline test suite and cryptographic trace chain
CMD ["sh", "-c", "uv run pytest -m 'not slow' && uv run ruff check src tests && uv run issa verify 20261102-201027-psobot-keynote-parser-91"]
