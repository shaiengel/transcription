# GPU Timestamp Alignment Worker

GPU-based worker that re-aligns LLM-corrected transcriptions with original audio using stable-whisper.

## Overview

After the LLM reviewer fixes spelling/grammar in transcriptions, the timestamps may drift from the actual audio. This worker:
1. Downloads audio from `portal-daf-yomi-audio`
2. Downloads corrected text from `final-transcription`
3. Uses stable-whisper to align text with audio
4. Uploads new VTT and JSON files to `final-transcription`

## Usage

```bash
# Install dependencies
uv sync

# Run locally
uv run timestamp
```

## Docker

```bash
# Build
docker build -t gpu-timestamp .

# Run
docker run --gpus all gpu-timestamp
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region |
| `AUDIO_BUCKET` | `portal-daf-yomi-audio` | Source bucket for audio |
| `TEXT_BUCKET` | `final-transcription` | Bucket for text files |
| `OUTPUT_BUCKET` | `final-transcription` | Output bucket for VTT |
| `SQS_QUEUE_URL` | - | SQS queue URL |
| `WHISPER_MODEL` | `base` | stable-whisper model name |
| `DEVICE` | `cuda` | Device (cuda or cpu) |
| `LANGUAGE` | `he` | Language code |
| `TOKEN_STEP` | `200` | Token step for alignment |
| `WHISPER_CACHE` | `/opt/models/whisper` | Whisper model cache directory |
| `SQS_FINAL_QUEUE_URL` | - | SQS queue for completion notifications |

## ASG SQS Scaling Setup

Auto Scaling Group `portal-timestamp-fix` scales based on SQS queue messages.

### 1. Create Scaling Policies

```bash
# Scale to 0 when queue is empty
aws autoscaling put-scaling-policy \
  --auto-scaling-group-name portal-timestamp-fix \
  --policy-name scale-to-0-policy \
  --policy-type SimpleScaling \
  --adjustment-type ExactCapacity \
  --scaling-adjustment 0

# Scale to 2 when queue has messages
aws autoscaling put-scaling-policy \
  --auto-scaling-group-name portal-timestamp-fix \
  --policy-name scale-to-2-policy \
  --policy-type SimpleScaling \
  --adjustment-type ExactCapacity \
  --scaling-adjustment 2
```

### 2. Get Policy ARNs

```bash
aws autoscaling describe-policies \
  --auto-scaling-group-name portal-timestamp-fix \
  --query "ScalingPolicies[*].[PolicyName,PolicyARN]" \
  --output table
```

### 3. Create CloudWatch Alarms

Replace `POLICY_ARN` with the actual ARNs from step 2.

```bash
# Scale to 0 when queue is completely empty (visible + not visible = 0) for 5 minutes
aws cloudwatch put-metric-alarm \
  --alarm-name "scale to 0" \
  --evaluation-periods 5 \
  --comparison-operator LessThanOrEqualToThreshold \
  --threshold 0 \
  --metrics '[
    {
      "Id": "m1",
      "MetricStat": {
        "Metric": {
          "Namespace": "AWS/SQS",
          "MetricName": "ApproximateNumberOfMessagesVisible",
          "Dimensions": [{"Name": "QueueName", "Value": "sqs-fix-transcribes"}]
        },
        "Period": 60,
        "Stat": "Average"
      },
      "ReturnData": false
    },
    {
      "Id": "m2",
      "MetricStat": {
        "Metric": {
          "Namespace": "AWS/SQS",
          "MetricName": "ApproximateNumberOfMessagesNotVisible",
          "Dimensions": [{"Name": "QueueName", "Value": "sqs-fix-transcribes"}]
        },
        "Period": 60,
        "Stat": "Average"
      },
      "ReturnData": false
    },
    {
      "Id": "total",
      "Expression": "m1 + m2",
      "Label": "TotalMessages",
      "ReturnData": true
    }
  ]' \
  --alarm-actions <SCALE_TO_0_POLICY_ARN>

# Scale to 2 when queue has messages
aws cloudwatch put-metric-alarm \
  --alarm-name alarm-for-sqs-fix-visible \
  --metric-name ApproximateNumberOfMessagesVisible \
  --namespace AWS/SQS \
  --statistic Average \
  --period 60 \
  --threshold 0 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 \
  --dimensions Name=QueueName,Value=sqs-fix-transcribes \
  --alarm-actions <SCALE_TO_2_POLICY_ARN>
```

### Alarm Summary

| Alarm | Condition | Action |
|-------|-----------|--------|
| `scale to 0` | Visible + NotVisible <= 0 for 5 consecutive 60s periods | Scale to 0 |
| `alarm-for-sqs-fix-visible` | Visible > 0 | Scale to 2 |
