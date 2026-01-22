FROM nvidia/cuda:12.4.0-runtime-ubuntu22.04

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install Python 3.12 via deadsnakes PPA and curl for uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Create model directory mount point
RUN mkdir -p /opt/models

# AWS Configuration
ENV AWS_REGION=us-east-1

# S3 Buckets
ENV SOURCE_BUCKET=portal-daf-yomi-audio
ENV DEST_BUCKET=portal-daf-yomi-transcription
ENV OUTPUT_PREFIX=

# SQS
ENV SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/707072965202/sqs-transcription

# Whisper Model Configuration
/opt/models/models--ivrit-ai--whisper-large-v3-ct2/snapshots/e9ed4a4a98d761b0f617d668303de2c514236c66/
ENV WHISPER_MODEL=/opt/models/models--ivrit-ai--whisper-large-v3-ct2/snapshots/e9ed4a4a98d761b0f617d668303de2c514236c66
ENV DEVICE=cuda
ENV COMPUTE_TYPE=float16
ENV LANGUAGE=he
ENV BEAM_SIZE=7

# Run the transcription worker
CMD ["uv", "run", "transcribe"]
