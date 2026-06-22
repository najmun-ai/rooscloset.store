"""
atlas/handlers/rekognition_detect.py
Stage 2: Garment Detection via Rekognition

Calls Rekognition DetectLabels on the product image.
Filters for clothing-related labels, writes results to DynamoDB.
"""

import json
import os
import boto3
from datetime import datetime, timezone
from typing import Any

rekognition = boto3.client('rekognition')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['SKU_TABLE'])

CLOTHING_KEYWORDS = [
    'dress', 'blouse', 'shirt', 'pants', 'jeans', 'skirt', 'jacket',
    'coat', 'sweater', 'cardigan', 'top', 'bottom', 'footwear', 'shoe',
    'boot', 'sneaker', 'sandal', 'heel', 'fabric', 'textile', 'clothing',
    'apparel', 'garment', 'knit', 'woven', 'denim', 'leather', 'silk',
]

def handler(event: dict, context: Any) -> dict:
    """
    Input from Step Functions:
    {
      "tenant_id": "...",
      "sku_id": "...",
      "image_s3_key": "raw/{tenant}/images/{sku}.jpg",
      "image_bucket": "rooscloset-data-lake-..."
    }
    """
    tenant_id = event['tenant_id']
    sku_id = event['sku_id']
    bucket = event['image_bucket']
    key = event['image_s3_key']
    
    # Call Rekognition DetectLabels
    response = rekognition.detect_labels(
        Image={'S3Object': {'Bucket': bucket, 'Name': key}},
        MaxLabels=20,
        MinConfidence=70.0
    )
    
    # Filter for clothing labels
    clothing_labels = [
        {
            'Name': label['Name'],
            'Confidence': round(label['Confidence'], 2)
        }
        for label in response.get('Labels', [])
        if any(kw in label['Name'].lower() for kw in CLOTHING_KEYWORDS)
    ]
    
    # Write to DynamoDB
    table.update_item(
        Key={'tenant_id': tenant_id, 'sku_id': sku_id},
        UpdateExpression='''
            SET processing_status = :status,
                rekognition_labels = :labels,
                rekognition_detected_at = :ts
        ''',
        ExpressionAttributeValues={
            ':status': 'rekognition_complete',
            ':labels': clothing_labels,
            ':ts': datetime.now(timezone.utc).isoformat(),
        }
    )
    
    return {
        **event,
        'rekognition_labels': clothing_labels,
        'label_count': len(clothing_labels),
    }
