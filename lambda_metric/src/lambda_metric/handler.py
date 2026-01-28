import boto3
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sqs = boto3.client('sqs')
# ecs = boto3.client('ecs')
autoscaling = boto3.client('autoscaling')
cloudwatch = boto3.client('cloudwatch')

def handler(event, context):
    logger.info("Starting metric calculation")

    # Get queue state
    attrs = sqs.get_queue_attributes(
        QueueUrl=os.environ['SQS_QUEUE_URL'],
        AttributeNames=[
            'ApproximateNumberOfMessages',
            'ApproximateNumberOfMessagesNotVisible'
        ]
    )

    visible = int(attrs['Attributes']['ApproximateNumberOfMessages'])
    not_visible = int(attrs['Attributes']['ApproximateNumberOfMessagesNotVisible'])
    total = visible + not_visible

    logger.info(f"Queue state: visible={visible}, not_visible={not_visible}, total={total}")

    # Exit early if idle
    if total == 0:
        logger.info("Queue is empty, exiting")
        return

    # # Get running tasks (ECS)
    # response = ecs.describe_services(
    #     cluster=os.environ['CLUSTER_NAME'],
    #     services=[os.environ['SERVICE_NAME']]
    # )
    # running_tasks = response['services'][0]['runningCount']

    # Get running instances (ASG)
    asg_name = os.environ['ASG_NAME']
    response = autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg_name]
    )

    if not response['AutoScalingGroups']:
        logger.warning(f"ASG not found: {asg_name}")
        return

    asg = response['AutoScalingGroups'][0]
    running_instances = len([
        i for i in asg['Instances']
        if i['LifecycleState'] == 'InService'
    ])

    logger.info(f"ASG '{asg_name}': running_instances={running_instances}")

    # Skip if no instances yet (bootstrap policy handles 0→1)
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
