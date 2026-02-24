#cloud-config
runcmd:
  # wait for docker + network
  - sleep 15

  # download model if not already present
  - |
    if [ ! -f /opt/dlami/nvme/models/ivrit-ai--whisper-large-v3-ct2/model.bin ]; then
      echo "Downloading model from S3..."
      aws s3 sync s3://portal-daf-yomi-models/ivrit-ai--whisper-large-v3-ct2/ /opt/dlami/nvme/models/ivrit-ai--whisper-large-v3-ct2/ --no-progress
    else
      echo "Model already exists, skipping download."
    fi

  # login to ECR
  - aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 707072965202.dkr.ecr.us-east-1.amazonaws.com

  # pull container
  - docker pull 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:4

  # run container
  - docker run -d --gpus all --restart unless-stopped -v /opt/dlami/nvme/models/ivrit-ai--whisper-large-v3-ct2:/opt/models/ivrit-ai--whisper-large-v3-ct2:ro 707072965202.dkr.ecr.us-east-1.amazonaws.com/portal-daf-yomi/whisper-transcribe:4
