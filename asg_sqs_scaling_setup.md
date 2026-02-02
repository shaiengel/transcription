# ASG Scaling with SQS - Setup Summary

## Overview

Scale EC2 instances (GPU workers) based on SQS queue depth with the ability to scale to zero when idle.

**Scaling rule:** Minimum 2 instances when working, max 5 instances, scale to 0 when idle.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   SQS Queue                                                         │
│       │                                                             │
│       └──► Native Metrics (automatic, free)                         │
│               • ApproximateNumberOfMessagesVisible                  │
│               • ApproximateNumberOfMessagesNotVisible               │
│                                                                     │
│   CloudWatch Alarms                                                 │
│       │                                                             │
│       ├──► Step Scaling Alarm ───► ASG (0 → 2-5)                    │
│       └──► Shutdown Alarm ───────► ASG (→ 0)                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**No Lambda needed. Just 2 policies + 2 alarms.**

---

## ASG Configuration

| Setting | Value |
|---------|-------|
| Min capacity | 0 |
| Max capacity | 5 |
| Desired capacity | 0 (initial) |

---

## Policy 1: Step Scaling (0 → 2-5)

### Purpose

Scales instances based on queue depth. Handles both initial scale-up (0→2) and scaling while working (2→5).

### Scaling Steps

| Messages in Queue | Instances |
|-------------------|-----------|
| 0 | 0 (alarm OK, no action) |
| 1-16 | 2 |
| 17-24 | 3 |
| 25-32 | 4 |
| 33+ | 5 |

### AWS CLI

```bash
# Create the step scaling policy
aws autoscaling put-scaling-policy \
  --auto-scaling-group-name portal-transcription-asg \
  --policy-name backlog-step-scaling \
  --policy-type StepScaling \
  --adjustment-type ExactCapacity \
  --step-adjustments \
    "MetricIntervalLowerBound=0,MetricIntervalUpperBound=16,ScalingAdjustment=2" \
    "MetricIntervalLowerBound=16,MetricIntervalUpperBound=24,ScalingAdjustment=3" \
    "MetricIntervalLowerBound=24,MetricIntervalUpperBound=32,ScalingAdjustment=4" \
    "MetricIntervalLowerBound=32,ScalingAdjustment=5"
```

```bash
# Create the alarm to trigger the policy
# Replace REGION, ACCOUNT_ID, and POLICY_ID with your values
aws cloudwatch put-metric-alarm \
  --alarm-name "alarm for sqs visible" \
  --metric-name ApproximateNumberOfMessagesVisible \
  --namespace AWS/SQS \
  --dimensions Name=QueueName,Value=sqs-transcription \
  --statistic Average \
  --period 60 \
  --evaluation-periods 1 \
  --threshold 0 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions "arn:aws:autoscaling:REGION:ACCOUNT_ID:scalingPolicy:POLICY_ID:autoScalingGroupName/portal-transcription-asg:policyName/backlog-step-scaling"
```

### How the Bounds Work

The bounds are **relative to the alarm threshold** (which is 0):

| Bound | Meaning | Messages |
|-------|---------|----------|
| LowerBound=0, UpperBound=16 | threshold+0 to threshold+16 | 1-16 |
| LowerBound=16, UpperBound=24 | threshold+16 to threshold+24 | 17-24 |
| LowerBound=24, UpperBound=32 | threshold+24 to threshold+32 | 25-32 |
| LowerBound=32 | threshold+32 to infinity | 33+ |

**Important:** The policy only triggers when the alarm is in ALARM state (messages > 0). When messages = 0, the alarm is OK and the policy doesn't run.

---

## Policy 2: Shutdown (→ 0)

### Purpose

Scales to zero when queue is completely empty (no visible AND no in-flight messages) for 5 minutes.

### Why Check Both Metrics

- `Visible` = messages waiting in queue
- `NotVisible` = messages being processed (in-flight)

If you only check visible = 0, you might kill instances while they're still processing messages.

### AWS CLI

```bash
# Create the shutdown policy (Simple scaling - no step bounds needed)
aws autoscaling put-scaling-policy \
  --auto-scaling-group-name portal-transcription-asg \
  --policy-name shutdown-to-zero \
  --policy-type SimpleScaling \
  --adjustment-type ExactCapacity \
  --scaling-adjustment 0 \
  --cooldown 300
```

```bash
# Create the alarm with metric math (visible + in-flight = 0)
# Replace REGION, ACCOUNT_ID, and POLICY_ID with your values
aws cloudwatch put-metric-alarm \
  --alarm-name "scale to 0" \
  --evaluation-periods 5 \
  --datapoints-to-alarm 5 \
  --comparison-operator LessThanOrEqualToThreshold \
  --threshold 0 \
  --metrics '[
    {
      "Id": "m1",
      "MetricStat": {
        "Metric": {
          "Namespace": "AWS/SQS",
          "MetricName": "ApproximateNumberOfMessagesVisible",
          "Dimensions": [{"Name": "QueueName", "Value": "sqs-transcription"}]
        },
        "Period": 60,
        "Stat": "Sum"
      },
      "ReturnData": false
    },
    {
      "Id": "m2",
      "MetricStat": {
        "Metric": {
          "Namespace": "AWS/SQS",
          "MetricName": "ApproximateNumberOfMessagesNotVisible",
          "Dimensions": [{"Name": "QueueName", "Value": "sqs-transcription"}]
        },
        "Period": 60,
        "Stat": "Sum"
      },
      "ReturnData": false
    },
    {
      "Id": "total",
      "Expression": "m1 + m2",
      "Label": "Total Messages",
      "ReturnData": true
    }
  ]' \
  --alarm-actions "arn:aws:autoscaling:REGION:ACCOUNT_ID:scalingPolicy:POLICY_ID:autoScalingGroupName/portal-transcription-asg:policyName/shutdown-to-zero"
```

---

## Helper Commands

### Get Policy ARNs

```bash
aws autoscaling describe-policies \
  --auto-scaling-group-name portal-transcription-asg \
  --query "ScalingPolicies[*].[PolicyName,PolicyARN]"
```

### Check Alarm States

```bash
aws cloudwatch describe-alarms \
  --alarm-names "alarm for sqs visible" "scale to 0" \
  --query "MetricAlarms[*].[AlarmName,StateValue]"
```

### Check ASG Status

```bash
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names portal-transcription-asg \
  --query "AutoScalingGroups[0].[MinSize,MaxSize,DesiredCapacity]"
```

---

## Complete Flow

```
IDLE STATE:
  Queue: 0 messages (visible + in-flight)
  Instances: 0
  Cost: $0

MESSAGES ARRIVE (e.g., 10 messages):
  1. SQS receives messages
  2. Step scaling alarm fires (messages > 0)
  3. 10 is in range 0-16 → Set to 2 instances
  4. 2 GPU instances start processing

MORE MESSAGES (e.g., 25 total):
  1. Step scaling alarm still in ALARM state
  2. 25 is in range 24-32 → Set to 4 instances
  3. 2 more GPU instances start

PROCESSING COMPLETES:
  1. Visible messages → 0
  2. In-flight messages → 0 (all deleted after processing)
  3. Shutdown alarm starts counting

SHUTDOWN (after 5 minutes of empty queue):
  1. Shutdown alarm fires (visible + in-flight = 0 for 5 min)
  2. ASG sets desired capacity to 0
  3. All instances terminated
  4. Back to idle state
```

---

## Summary Table

| Component | Type | Metric | Action |
|-----------|------|--------|--------|
| backlog-step-scaling | Step scaling | ApproximateNumberOfMessagesVisible > 0 | Scale 2-5 based on queue depth |
| shutdown-to-zero | Simple scaling | visible + in-flight = 0 for 5 min | Scale to 0 |

---

## Cost

**$0 extra** - Uses only native AWS metrics and alarms. No Lambda, no custom metrics.

---

## Console Setup (Alternative to CLI)

### Step Scaling Policy

1. EC2 → Auto Scaling Groups → portal-transcription-asg → Automatic scaling
2. Create dynamic scaling policy
3. Policy type: Step scaling
4. Name: backlog-step-scaling
5. Create CloudWatch alarm:
   - Metric: SQS → ApproximateNumberOfMessagesVisible
   - Queue: sqs-transcription
   - Threshold: Greater than 0
   - Period: 1 minute
6. Add steps:
   - Set to 2 when 0 <= metric < 16
   - Set to 3 when 16 <= metric < 24
   - Set to 4 when 24 <= metric < 32
   - Set to 5 when 32 <= metric < +infinity

### Shutdown Policy

1. EC2 → Auto Scaling Groups → portal-transcription-asg → Automatic scaling
2. Create dynamic scaling policy
3. Policy type: Simple scaling
4. Name: shutdown-to-zero
5. Create CloudWatch alarm with metric math:
   - Add math → Expression: m1 + m2
   - m1: ApproximateNumberOfMessagesVisible
   - m2: ApproximateNumberOfMessagesNotVisible
   - Threshold: Less than or equal to 0
   - Datapoints: 5 of 5 (waits 5 minutes)
6. Take the action: Set to 0 capacity units
7. Cooldown: 300 seconds