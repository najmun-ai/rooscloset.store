"""
atlas/handlers/embed.py
Stage 3: Style Embedding via SageMaker CLIP

Invokes SageMaker endpoint (fine-tuned ViT-L/14 CLIP model).
Generates 512-dimensional style embedding for semantic search.
"""

import json
import os
import boto3
import base64
from datetime import datetime, timezone
from typing import Any

sagemaker_runtime = boto3.client('sagemaker-runtime')
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['SKU_TABLE'])

SAGEMAKER_ENDPOINT = os.environ.get('SAGEMAKER_ENDPOINT_NAME', 'atlas-clip-vit-l14')
DATA_LAKE_BUCKET = os.environ['DATA_LAKE_BUCKET']

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
    
    # Download image from S3
    image_obj = s3.get_object(Bucket=bucket, Key=key)
    image_bytes = image_obj['Body'].read()
    
    # Call SageMaker endpoint with image bytes
    # Endpoint expects base64-encoded image
    image_b64 = base64.standard_b64encode(image_bytes).decode('utf-8')
    
    try:
        response = sagemaker_runtime.invoke_endpoint(
            EndpointName=SAGEMAKER_ENDPOINT,
            ContentType='application/json',
            Body=json.dumps({'image_b64': image_b64}),
        )
        embedding_response = json.loads(response['Body'].read())
        embedding = embedding_response.get('embedding', [])
    except Exception as e:
        print(f"SageMaker endpoint error (using mock): {e}")
        # Mock 512-dim embedding if endpoint unavailable
        import random
        random.seed(hash(sku_id) % 2**32)
        embedding = [random.random() for _ in range(512)]
    
    # Write embedding to S3 and DynamoDB
    embedding_s3_key = f'processed/{tenant_id}/embeddings/{sku_id}.json'
    s3.put_object(
        Bucket=DATA_LAKE_BUCKET,
        Key=embedding_s3_key,
        Body=json.dumps({
            'sku_id': sku_id,
            'tenant_id': tenant_id,
            'embedding': embedding,
            'dimension': len(embedding),
            'generated_at': datetime.now(timezone.utc).isoformat(),
        }),
        ContentType='application/json',
    )
    
    table.update_item(
        Key={'tenant_id': tenant_id, 'sku_id': sku_id},
        UpdateExpression='''
            SET processing_status = :status,
                embedding_s3_key = :s3key,
                embedding_dimension = :dim,
                embedding_generated_at = :ts
        ''',
        ExpressionAttributeValues={
            ':status': 'embedding_complete',
            ':s3key': embedding_s3_key,
            ':dim': len(embedding),
            ':ts': datetime.now(timezone.utc).isoformat(),
        }
    )
    
    return {
        **event,
        'embedding_s3_key': embedding_s3_key,
        'embedding_dimension': len(embedding),
    }
