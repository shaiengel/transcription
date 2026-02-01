# ASG Scaling with SQS - Setup Summary

## Overview

Scale EC2 instances (GPU workers) based on SQS queue depth with the ability to scale to zero when idle.

**Scaling rule:** 1 instance per 8 messages in queue, max 5 instances.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   SQS Queue                                                         │
│       │                                                             │
│       ├──► Native Metrics (automatic)                               │
│       │       • ApproximateNumberOfMessagesVisible                  │
│       │       • ApproximateNumberOfMessagesNotVisible               │
│       │                                                             │
│       └──► Lambda (every 1 min) ──► Custom Metric                   │
│                                       • BacklogPerInstance          │
│                                                                     │
│   CloudWatch Alarms                                                 │
│       │                                                             │
│       ├──► Bootstrap Alarm ──────► ASG (0 → 1)                      │
│       ├──► Target Tracking ──────► ASG (1 → 5)                      │
│       └──► Shutdown Alarm ───────► ASG (→ 0)                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Lambda: Custom Metric Publisher

### Purpose

Publishes `BacklogPerInstance` metric to CloudWatch, which target tracking uses for scaling 1→5.

### Logic

```python
backlog_per_instance = (visible_messages + in_flight_messages) / running_instances
```

### Code

```python
import boto3
import os
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sqs = boto3.client('sqs')
autoscaling = boto3.client('autoscaling')
cloudwatch = boto3.client('cloudwatch')

def handler(event, context):
    queue_url = os.environ['QUEUE_URL']
    asg_name = os.environ['ASG_NAME']
    
    # Get queue state
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            'ApproximateNumberOfMessages',           # visible
            'ApproximateNumberOfMessagesNotVisible'  # in-flight
        ]
    )
    
    visible = int(attrs['Attributes']['ApproximateNumberOfMessages'])
    not_visible = int(attrs['Attributes']['ApproximateNumberOfMessagesNotVisible'])
    total = visible + not_visible
    
    logger.info(f"Queue state: visible={visible}, not_visible={not_visible}, total={total}")
    
    # Exit if queue empty
    if total == 0:
        logger.info("Queue empty, exiting")
        return
    
    # Get running instances
    response = autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg_name]
    )
    running_instances = len([
        i for i in response['AutoScalingGroups'][0]['Instances']
        if i['LifecycleState'] == 'InService'
    ])
    
    logger.info(f"ASG '{asg_name}': running_instances={running_instances}")
    
    # Exit if no instances (bootstrap alarm handles 0→1)
    if running_instances == 0:
        logger.info("No running instances, exiting")
        return
    
    # Publish metric
    backlog_per_instance = total / running_instances
    
    logger.info(f"Publishing metric: BacklogPerInstance={backlog_per_instance}")
    
    cloudwatch.put_metric_data(
        Namespace='Custom/SQS',
        MetricData=[{
            'MetricName': 'BacklogPerInstance',
            'Value': backlog_per_instance,
            'Unit': 'Count',
            'Dimensions': [
                {'Name': 'AutoScalingGroupName', 'Value': asg_name}
            ]
        }]
    )
    
    logger.info("Metric published successfully")
```

### Environment Variables

| Variable | Value |
|----------|-------|
| `QUEUE_URL` | `https://sqs.region.amazonaws.com/123456789/sqs-transcription` |
| `ASG_NAME` | `portal-transcription-asg` |

### Trigger

EventBridge rule: runs every 1 minute.

### Cost

~$0.73/month (Lambda free tier + CloudWatch custom metric)

---

## Alarm 1: Bootstrap (0 → 1)

### Purpose

Starts the first instance when messages appear in an empty queue.

### Why Needed

Target tracking can't scale from 0 because:
- No instances running → Lambda exits early → No custom metric → Nothing to track

### Configuration

**Metric Math:** Only fire when messages exist AND no instances running

```
Expression: IF(m1 > 0 AND m2 == 0, 1, 0)

m1 = ApproximateNumberOfMessagesVisible (AWS/SQS)
m2 = GroupDesiredCapacity (AWS/AutoScaling)
```

| Setting | Value |
|---------|-------|
| Threshold | Greater than 0 |
| Period | 1 minute |
| Datapoints | 1 of 1 |
| Action | Set ASG to exactly 1 |

### Note

Requires enabling "Group metrics collection" on the ASG (free):
EC2 → Auto Scaling Groups → [your ASG] → Monitoring → Enable group metrics collection

---

## Alarm 2: Target Tracking (1 → 5)

### Purpose

Scales instances up/down to maintain ~8 messages per instance.

### Configuration

**Type:** Target Tracking Scaling Policy (not an alarm you create manually)

```bash
aws autoscaling put-scaling-policy \
  --auto-scaling-group-name portal-transcription-asg \
  --policy-name backlog-scaling \
  --policy-type TargetTrackingScaling \
  --target-tracking-configuration '{
    "TargetValue": 8,
    "CustomizedMetricSpecification": {
      "MetricName": "BacklogPerInstance",
      "Namespace": "Custom/SQS",
      "Dimensions": [
        {
          "Name": "AutoScalingGroupName",
          "Value": "portal-transcription-asg"
        }
      ],
      "Statistic": "Average"
    }
  }'
```

### How It Works

| BacklogPerInstance | vs Target (8) | Action |
|--------------------|---------------|--------|
| 24 | > 8 | Scale out |
| 8 | = 8 | Do nothing |
| 4 | < 8 | Scale in |

### Example

| Queue Depth | Target | Instances |
|-------------|--------|-----------|
| 8 | 8 | 1 |
| 16 | 8 | 2 |
| 24 | 8 | 3 |
| 32 | 8 | 4 |
| 40 | 8 | 5 (max) |

---

## Alarm 3: Shutdown (→ 0)

### Purpose

Scales to zero when queue is completely empty (no visible AND no in-flight messages).

### Configuration

**Metric Math:** visible + in-flight = 0

```
Expression: m1 + m2

m1 = ApproximateNumberOfMessagesVisible (AWS/SQS)
m2 = ApproximateNumberOfMessagesNotVisible (AWS/SQS)
```

| Setting | Value |
|---------|-------|
| Threshold | Less than or equal to 0 |
| Period | 1 minute |
| Datapoints | 5 of 5 (waits 5 minutes) |
| Action | Set ASG to exactly 0 |

### Why Check Both Metrics

- `Visible` = messages waiting in queue
- `NotVisible` = messages being processed

If you only check visible = 0, you might kill instances while they're still processing messages.

---

## ASG Configuration

| Setting | Value |
|---------|-------|
| Min capacity | 0 |
| Max capacity | 5 |
| Desired capacity | 0 (initial) |
| Group metrics collection | Enabled |

---

## Complete Flow

```
IDLE STATE:
  Queue: 0 messages
  Instances: 0
  Cost: $0

MESSAGE ARRIVES:
  1. SQS receives message
  2. Bootstrap alarm fires (messages > 0, instances = 0)
  3. ASG starts 1 instance
  4. Lambda starts publishing BacklogPerInstance metric

SCALING UP:
  1. More messages arrive, queue depth = 24
  2. BacklogPerInstance = 24 / 1 = 24
  3. Target tracking: 24 > 8, scale out
  4. ASG adds instances until BacklogPerInstance ≈ 8
  5. Result: 3 instances (24 / 3 = 8)

SCALING DOWN:
  1. Messages processed, queue depth drops to 8
  2. BacklogPerInstance = 8 / 3 = 2.7
  3. Target tracking: 2.7 < 8, scale in
  4. ASG removes instances until BacklogPerInstance ≈ 8
  5. Result: 1 instance (8 / 1 = 8)

SHUTDOWN:
  1. All messages processed
  2. visible = 0, in-flight = 0
  3. Shutdown alarm waits 5 minutes
  4. Alarm fires, ASG sets desired = 0
  5. All instances terminated
  6. Back to idle state
```

---

## Summary Table

| Component | Purpose | Metric Used |
|-----------|---------|-------------|
| Lambda | Publish BacklogPerInstance | (custom calculation) |
| Bootstrap Alarm | 0 → 1 | Native SQS + ASG GroupDesiredCapacity |
| Target Tracking | 1 → 5 | Custom BacklogPerInstance |
| Shutdown Alarm | → 0 | Native SQS (visible + in-flight) |