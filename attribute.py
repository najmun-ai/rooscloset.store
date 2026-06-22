"""
atlas/handlers/attribute.py

Stage 4 of the ATLAS pipeline.
Receives a product record (image URL + sparse merchant description + Rekognition labels).
Calls Bedrock (Claude) with the product image + context to extract 180+ structured attributes.
Writes enriched attributes to DynamoDB and S3.
"""

import json
import os
import base64
import boto3
from datetime import datetime, timezone
from typing import Any

bedrock = boto3.client('bedrock-runtime', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')

SKU_TABLE = os.environ['SKU_TABLE']
DATA_LAKE_BUCKET = os.environ['DATA_LAKE_BUCKET']
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_MODEL_ID', 'anthropic.claude-3-sonnet-20240229-v1:0')

table = dynamodb.Table(SKU_TABLE)

# ── Attribute Extraction Prompt ────────────────────────────────────────────
# Structured so Claude returns a deterministic JSON schema every time.
# 180+ fields covering all dimensions needed for search, recommendations, and return features.
ATTRIBUTE_PROMPT = """You are a fashion product intelligence system. Analyze this product image and merchant description, then extract attributes in the exact JSON schema below.

Merchant description: {merchant_description}
Rekognition detected labels: {rekognition_labels}

Return ONLY valid JSON matching this schema exactly. No explanation, no markdown fences.

{{
  "garment_type": "string (e.g. dress, blouse, trousers, jacket)",
  "silhouette": "string (e.g. A-line, straight, oversized, fitted, wrap)",
  "neckline": "string (e.g. crew, V-neck, square, off-shoulder, turtleneck, none)",
  "sleeve_length": "string (none|sleeveless|short|3/4|long|extra-long)",
  "sleeve_style": "string (e.g. puff, bishop, bell, raglan, cap, standard)",
  "length": "string (mini|midi|maxi|cropped|standard|floor)",
  "fit": "string (slim|regular|relaxed|oversized|tailored|wrap)",
  "fabric_family": "string (e.g. knit, woven, denim, leather, technical)",
  "fabric_texture": "string (e.g. smooth, ribbed, textured, sheer, opaque)",
  "primary_color": "string (closest Pantone family name)",
  "color_family": "string (neutrals|blues|greens|reds|yellows|purples|pinks|multicolor|prints)",
  "print_pattern": "string (solid|stripe|check|floral|abstract|geometric|animal|none)",
  "embellishment": ["array of strings: beading, embroidery, fringe, buttons, none, etc."],
  "closure_type": "string (zip|button|tie|pull-on|hook-eye|none)",
  "occasion_primary": "string (casual|work|evening|activewear|occasion|beach|lounge)",
  "occasion_stack": ["array: up to 3 applicable occasions ranked by fit"],
  "season_affinity": ["array: spring|summer|fall|winter — all that apply"],
  "formality_score": "float 0.0-1.0 (0=most casual, 1=most formal)",
  "trend_alignment": {{
    "quiet_luxury": "float 0.0-1.0",
    "minimalist": "float 0.0-1.0",
    "coastal_grandmother": "float 0.0-1.0",
    "dark_academia": "float 0.0-1.0",
    "cottagecore": "float 0.0-1.0",
    "mob_wife": "float 0.0-1.0",
    "streetwear": "float 0.0-1.0",
    "preppy": "float 0.0-1.0",
    "boho": "float 0.0-1.0",
    "scandinavian_minimal": "float 0.0-1.0"
  }},
  "style_notes": "string (1-2 sentence description in consumer language)",
  "search_keywords": ["array of 10-15 keywords a consumer would use to find this"],
  "pdp_copy_headline": "string (punchy product headline, max 8 words)",
  "pdp_copy_description": "string (2-3 sentence consumer-facing description)",
  "return_risk_flags": {{
    "color_photography_mismatch_risk": "low|medium|high",
    "size_ambiguity_risk": "low|medium|high",
    "fabric_hand_unclear": "boolean",
    "needs_size_chart": "boolean",
    "needs_model_measurements": "boolean"
  }},
  "extraction_confidence": "float 0.0-1.0",
  "extraction_notes": "string (any uncertainty or assumptions made)"
}}"""


def handler(event: dict, context: Any) -> dict:
    """
    Step Functions passes:
    {
      "tenant_id": "...",
      "sku_id": "...",
      "image_s3_key": "raw/{tenant_id}/images/{sku_id}.jpg",
      "merchant_description": "...",
      "rekognition_labels": [...],
      "clip_embedding_s3_key": "processed/{tenant_id}/embeddings/{sku_id}.json"
    }
    """
    tenant_id = event['tenant_id']
    sku_id = event['sku_id']
    image_s3_key = event['image_s3_key']
    merchant_description = event.get('merchant_description', '')
    rekognition_labels = event.get('rekognition_labels', [])

    # Load image from S3 and base64-encode for Bedrock multimodal
    image_obj = s3.get_object(Bucket=DATA_LAKE_BUCKET, Key=image_s3_key)
    image_bytes = image_obj['Body'].read()
    image_b64 = base64.standard_b64encode(image_bytes).decode('utf-8')

    # Determine image media type from key extension
    ext = image_s3_key.rsplit('.', 1)[-1].lower()
    media_type = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext}'

    label_str = ', '.join([f"{l['Name']} ({l['Confidence']:.0f}%)" for l in rekognition_labels[:10]])

    prompt = ATTRIBUTE_PROMPT.format(
        merchant_description=merchant_description or 'Not provided',
        rekognition_labels=label_str or 'None detected',
    )

    # Bedrock multimodal call — image + text → structured JSON attributes
    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        contentType='application/json',
        accept='application/json',
        body=json.dumps({
            'anthropic_version': 'bedrock-2023-05-31',
            'max_tokens': 2048,
            'temperature': 0.1,   # Low temperature for deterministic structured output
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': media_type,
                                'data': image_b64,
                            },
                        },
                        {
                            'type': 'text',
                            'text': prompt,
                        },
                    ],
                }
            ],
        }),
    )

    response_body = json.loads(response['body'].read())
    raw_attributes_text = response_body['content'][0]['text'].strip()

    # Parse and validate — if malformed, flag for QA rather than failing pipeline
    try:
        attributes = json.loads(raw_attributes_text)
    except json.JSONDecodeError:
        # Attempt to extract JSON from any surrounding text
        import re
        json_match = re.search(r'\{.*\}', raw_attributes_text, re.DOTALL)
        if json_match:
            attributes = json.loads(json_match.group())
        else:
            # Write raw to S3 for human QA, continue pipeline with partial data
            s3.put_object(
                Bucket=DATA_LAKE_BUCKET,
                Key=f'qa/{tenant_id}/failed_extraction/{sku_id}.txt',
                Body=raw_attributes_text.encode(),
            )
            attributes = {'extraction_confidence': 0.0, 'extraction_notes': 'Parse failed — queued for QA'}

    # Enrich with pipeline metadata
    attributes['_meta'] = {
        'tenant_id': tenant_id,
        'sku_id': sku_id,
        'extracted_at': datetime.now(timezone.utc).isoformat(),
        'model_id': BEDROCK_MODEL_ID,
        'pipeline_stage': 'attribute_extraction',
    }

    # Write full attributes to S3 (source of truth)
    attributes_s3_key = f'processed/{tenant_id}/attributes/{sku_id}.json'
    s3.put_object(
        Bucket=DATA_LAKE_BUCKET,
        Key=attributes_s3_key,
        Body=json.dumps(attributes, ensure_ascii=False).encode(),
        ContentType='application/json',
    )

    # Write key fields to DynamoDB for fast lookup + MIRROR feature access
    table.update_item(
        Key={'tenant_id': tenant_id, 'sku_id': sku_id},
        UpdateExpression="""
            SET processing_status = :status,
                attributes_s3_key = :s3key,
                garment_type = :garment_type,
                occasion_primary = :occasion,
                formality_score = :formality,
                color_family = :color,
                return_risk_flags = :risk_flags,
                extraction_confidence = :confidence,
                attributes_updated_at = :updated_at
        """,
        ExpressionAttributeValues={
            ':status': 'attributes_complete',
            ':s3key': attributes_s3_key,
            ':garment_type': attributes.get('garment_type', 'unknown'),
            ':occasion': attributes.get('occasion_primary', 'unknown'),
            ':formality': str(attributes.get('formality_score', 0.5)),
            ':color': attributes.get('color_family', 'unknown'),
            ':risk_flags': attributes.get('return_risk_flags', {}),
            ':confidence': str(attributes.get('extraction_confidence', 0.0)),
            ':updated_at': datetime.now(timezone.utc).isoformat(),
        },
    )

    return {
        **event,
        'attributes_s3_key': attributes_s3_key,
        'extraction_confidence': attributes.get('extraction_confidence', 0.0),
        'garment_type': attributes.get('garment_type'),
        'return_risk_flags': attributes.get('return_risk_flags', {}),
    }
