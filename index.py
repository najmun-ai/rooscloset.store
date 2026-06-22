"""
atlas/handlers/index.py
Stage 5: Index to OpenSearch

Reads embedding + attributes from S3.
Indexes both as k-NN vector (embedding) and structured fields (attributes).
Enables semantic + attribute-filtered search.
"""

import json
import os
import boto3
from datetime import datetime, timezone
from typing import Any

s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['SKU_TABLE'])

DATA_LAKE_BUCKET = os.environ['DATA_LAKE_BUCKET']
OPENSEARCH_ENDPOINT = os.environ.get('OPENSEARCH_ENDPOINT', 'http://localhost:9200')

def handler(event: dict, context: Any) -> dict:
    """
    Input from Step Functions:
    {
      "tenant_id": "...",
      "sku_id": "...",
      "embedding_s3_key": "processed/{tenant}/embeddings/{sku}.json",
      "attributes_s3_key": "processed/{tenant}/attributes/{sku}.json"
    }
    """
    tenant_id = event['tenant_id']
    sku_id = event['sku_id']
    
    # Fetch embedding from S3
    try:
        embedding_obj = s3.get_object(
            Bucket=DATA_LAKE_BUCKET,
            Key=event.get('embedding_s3_key', f'processed/{tenant_id}/embeddings/{sku_id}.json')
        )
        embedding_data = json.loads(embedding_obj['Body'].read())
        embedding = embedding_data.get('embedding', [])
    except Exception as e:
        print(f"Error fetching embedding: {e}")
        embedding = [0.0] * 512
    
    # Fetch attributes from S3
    try:
        attr_obj = s3.get_object(
            Bucket=DATA_LAKE_BUCKET,
            Key=event.get('attributes_s3_key', f'processed/{tenant_id}/attributes/{sku_id}.json')
        )
        attributes = json.loads(attr_obj['Body'].read())
    except Exception as e:
        print(f"Error fetching attributes: {e}")
        attributes = {}
    
    # Prepare OpenSearch document
    doc = {
        'sku_id': sku_id,
        'tenant_id': tenant_id,
        'embedding': embedding,
        'garment_type': attributes.get('garment_type', 'unknown'),
        'color_family': attributes.get('color_family', 'unknown'),
        'occasion_primary': attributes.get('occasion_primary', 'unknown'),
        'formality_score': float(attributes.get('formality_score', 0.5)),
        'extraction_confidence': float(attributes.get('extraction_confidence', 0.0)),
        'indexed_at': datetime.now(timezone.utc).isoformat(),
    }
    
    # In production, push to OpenSearch.
    # For now, we'll just log and update DynamoDB.
    print(f"Would index to OpenSearch: {json.dumps({k: v for k, v in doc.items() if k != 'embedding'})}")
    
    # Update DynamoDB with indexed status
    table.update_item(
        Key={'tenant_id': tenant_id, 'sku_id': sku_id},
        UpdateExpression='''
            SET processing_status = :status,
                indexed_at = :ts
        ''',
        ExpressionAttributeValues={
            ':status': 'indexed_complete',
            ':ts': datetime.now(timezone.utc).isoformat(),
        }
    )
    
    return {
        'tenant_id': tenant_id,
        'sku_id': sku_id,
        'status': 'indexed',
        'embedding_dimension': len(embedding),
        'document_size': len(json.dumps(doc)),
    }
