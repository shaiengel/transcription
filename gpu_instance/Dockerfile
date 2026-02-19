FROM nvidia/cuda:12.4.0-base-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install Python 3.12 + only the CUDA libs faster_whisper needs
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    curl \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    libcudnn8 \
    libcublas-12-4 \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /var/cache/apt/archives/*

# Install uv package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

RUN uv sync --frozen --no-dev

RUN mkdir -p /opt/models

# AWS Configuration
ENV AWS_REGION=us-east-1
ENV SOURCE_BUCKET=portal-daf-yomi-audio
ENV DEST_BUCKET=portal-daf-yomi-transcription
ENV OUTPUT_PREFIX=
ENV SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/707072965202/sqs-transcription

# Whisper Model Configuration
ENV WHISPER_MODEL=/opt/models/ivrit-ai--whisper-large-v3-ct2
ENV DEVICE=cuda
ENV COMPUTE_TYPE=float16
ENV LANGUAGE=he
ENV BEAM_SIZE=5

CMD ["uv", "run", "transcribe"]
