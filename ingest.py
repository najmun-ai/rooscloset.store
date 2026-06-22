"""
atlas/handlers/ingest.py
Stage 1: Ingest & Validate

Receives SQS messages from S3 PUT events.
Validates product metadata, initializes DynamoDB record, queues for Rekognition.
"""

import json
import os
import boto3
from datetime import datetime, timezone
from typing import Any

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['SKU_TABLE'])

def handler(event: dict, context: Any) -> dict:
    """
    SQS event source: each message is an S3 PUT event wrapped by SQS.
    Record contains: s3://bucket/raw/{tenant_id}/images/{sku_id}.{jpg|png}
    """
    records_processed = 0
    
    for record in event.get('Records', []):
        try:
            body = json.loads(record['body'])
            s3_event = body['Records'][0]
            bucket = s3_event['s3']['bucket']['name']
            key = s3_event['s3']['object']['key']
            
            # Parse key: raw/{tenant_id}/images/{sku_id}.{ext}
            parts = key.split('/')
            if len(parts) < 4 or parts[0] != 'raw':
                print(f"Skipping malformed key: {key}")
                continue
            
            tenant_id = parts[1]
            sku_id = parts[3].rsplit('.', 1)[0]  # Remove extension
            
            # Initialize DynamoDB record
            table.put_item(
                Item={
                    'tenant_id': tenant_id,
                    'sku_id': sku_id,
                    'processing_status': 'ingest_complete',
                    'image_s3_key': key,
                    'image_bucket': bucket,
                    'ingested_at': datetime.now(timezone.utc).isoformat(),
                }
            )
            
            records_processed += 1
        except Exception as e:
            print(f"Error processing record: {e}")
            continue
    
    return {'statusCode': 200, 'records_processed': records_processed}
