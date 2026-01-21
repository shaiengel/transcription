import boto3
import os

sqs = boto3.client('sqs')
ecs = boto3.client('ecs')
cloudwatch = boto3.client('cloudwatch')

def handler(event, context):
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

    # Exit early if idle
    if total == 0:
        return

    # Get running tasks
    response = ecs.describe_services(
        cluster=os.environ['CLUSTER_NAME'],
        services=[os.environ['SERVICE_NAME']]
    )
    running_tasks = response['services'][0]['runningCount']

    # Skip if no tasks yet (bootstrap policy handles 0→1)
    if running_tasks == 0:
        return

    # Publish metric
    backlog_per_instance = total / running_tasks

    cloudwatch.put_metric_data(
        Namespace='Custom/SQS',
        MetricData=[{
            'MetricName': 'BacklogPerInstance',
            'Value': backlog_per_instance,
            'Unit': 'Count',
            'Dimensions': [
                {'Name': 'ServiceName', 'Value': os.environ['SERVICE_NAME']}
            ]
        }]
    )
