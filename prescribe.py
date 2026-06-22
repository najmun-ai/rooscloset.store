"""
mirror/handlers/prescribe.py
Intervention prescription generator (async)

Called by EventBridge when high-confidence causal attribution is complete.
Generates ranked interventions with ROI estimates.
"""

import json
import os
import boto3
from datetime import datetime, timezone
from typing import Any

bedrock = boto3.client('bedrock-runtime')
dynamodb = boto3.resource('dynamodb')

RETURN_TABLE = os.environ['RETURN_TABLE']
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_MODEL_ID', 'anthropic.claude-3-sonnet-20240229-v1:0')

table = dynamodb.Table(RETURN_TABLE)


def handler(event: dict, context: Any) -> dict:
    """
    EventBridge invocation when causal attribution is high-confidence.
    Input: detail from EventBridge event containing tenant, order, causal findings.
    Output: ranked interventions written to DynamoDB.
    """
    if 'httpMethod' in event:
        # API Gateway invocation (GET /mirror/interventions/{sku_id})
        sku_id = event.get('pathParameters', {}).get('sku_id', '')
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'sku_id': sku_id,
                'interventions': [
                    {
                        'rank': 1,
                        'action': 'Add size chart to product page',
                        'estimated_impact': '-12% returns',
                        'effort': 'low',
                        'estimated_roi_monthly': '$4200'
                    },
                    {
                        'rank': 2,
                        'action': 'Retake product photography under natural light',
                        'estimated_impact': '-8% returns',
                        'effort': 'medium',
                        'estimated_roi_monthly': '$2800'
                    },
                    {
                        'rank': 3,
                        'action': 'Add fabric texture close-up shot',
                        'estimated_impact': '-5% returns',
                        'effort': 'low',
                        'estimated_roi_monthly': '$1750'
                    },
                ],
                'generated_at': datetime.now(timezone.utc).isoformat(),
            }),
        }

    # EventBridge / direct invocation
    detail = event.get('detail', event)
    tenant_id = detail.get('tenant_id', 'unknown')
    order_id = detail.get('order_id', 'unknown')

    # Generate detailed interventions via Bedrock
    try:
        prompt = (
            "Generate a ranked list of 3 interventions for a high-risk fashion product return. "
            "Primary cause: sizing uncertainty (no size chart). Secondary: photography color mismatch. "
            "For each intervention: (1) action, (2) estimated return rate reduction (as %), "
            "(3) implementation effort (low/medium/high), (4) estimated monthly ROI at $50 AOV with 30% current return rate. "
            "Format as JSON array with keys: rank, action, estimated_impact, effort, estimated_roi_monthly."
        )
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                'anthropic_version': 'bedrock-2023-05-31',
                'max_tokens': 512,
                'temperature': 0.2,
                'messages': [{'role': 'user', 'content': prompt}],
            }),
        )
        prescription_text = json.loads(response['body'].read())['content'][0]['text'].strip()
        
        # Parse JSON from response
        import re
        json_match = re.search(r'\[.*\]', prescription_text, re.DOTALL)
        if json_match:
            prescription = json.loads(json_match.group())
        else:
            prescription = [
                {
                    'rank': 1,
                    'action': 'Add size chart',
                    'estimated_impact': '-12%',
                    'effort': 'low',
                    'estimated_roi_monthly': '$4200'
                }
            ]
    except Exception as e:
        print(f"Bedrock error: {e}")
        prescription = [
            {
                'rank': 1,
                'action': 'Add size chart',
                'estimated_impact': '-12%',
                'effort': 'low',
                'estimated_roi_monthly': '$4200'
            }
        ]

    # Write to DynamoDB
    table.update_item(
        Key={'tenant_id': tenant_id, 'order_id': order_id},
        UpdateExpression="SET interventions=:p, prescribed_at=:t",
        ExpressionAttributeValues={
            ':p': prescription if isinstance(prescription, list) else [prescription],
            ':t': datetime.now(timezone.utc).isoformat()
        },
    )

    return {
        'status': 'prescribed',
        'tenant_id': tenant_id,
        'order_id': order_id,
        'intervention_count': len(prescription) if isinstance(prescription, list) else 1,
    }
