FROM nvidia/cuda:12.4.0-base-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install Python 3.12 + CUDA libs + FFmpeg (required by stable-whisper)
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    curl \
    ffmpeg \
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

# AWS Configuration
ENV AWS_REGION=us-east-1
ENV AUDIO_BUCKET=portal-daf-yomi-audio
ENV TEXT_BUCKET=portal-daf-yomi-fixed-text
ENV OUTPUT_BUCKET=final-transcription
ENV SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/707072965202/sqs-fix-transcribes
ENV SQS_FINAL_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/707072965202/sqs-final-transcribes

# stable-whisper Model Configuration
ENV WHISPER_CACHE=/opt/dlami/nvme
ENV WHISPER_MODEL=large-v3
ENV DEVICE=cuda
ENV LANGUAGE=he
ENV TOKEN_STEP=200

CMD ["uv", "run", "timestamp"]
