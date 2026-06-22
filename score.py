"""
mirror/handlers/score.py

Real-time return risk scorer. Called at checkout.
Target: < 150ms P99 end-to-end.

Receives an order payload from the merchant's checkout system.
Builds a 200+ feature vector and calls the SageMaker XGBoost endpoint.
Returns: risk score, risk level, top contributing features, causal flags.
Writes the scored order to DynamoDB and emits an event to Kinesis.
"""

import json
import os
import boto3
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

sagemaker_runtime = boto3.client('sagemaker-runtime')
dynamodb = boto3.resource('dynamodb')
kinesis = boto3.client('kinesis')
eventbridge = boto3.client('events')

RETURN_TABLE = os.environ['RETURN_TABLE']
ORDER_STREAM = os.environ['ORDER_STREAM']
SAGEMAKER_ENDPOINT = os.environ.get('SAGEMAKER_ENDPOINT', 'mirror-return-xgboost')
RISK_THRESHOLD_HIGH = float(os.environ.get('RISK_THRESHOLD_HIGH', '0.65'))
RISK_THRESHOLD_MEDIUM = float(os.environ.get('RISK_THRESHOLD_MEDIUM', '0.40'))

table = dynamodb.Table(RETURN_TABLE)


def build_feature_vector(order: dict) -> list[float]:
    """
    Construct the 200+ feature vector from an order payload.
    Features are grouped into four causal domains:
      A. Sizing signals  (features 0-49)
      B. Product content quality  (features 50-99)
      C. Customer history  (features 100-149)
      D. Order context  (features 150-199)
    """
    items = order.get('items', [])
    customer = order.get('customer', {})
    first_item = items[0] if items else {}

    # Domain A: Sizing signals
    sizing_features = [
        float(first_item.get('size_chart_present', 0)),
        float(first_item.get('model_measurements_present', 0)),
        float(first_item.get('multiple_fit_images', 0)),
        float(first_item.get('customer_provided_measurements', 0)),
        float(first_item.get('size_ambiguity_risk_score', 0.5)),  # from ATLAS
        float(len(items)),                                         # multi-item orders return more
        float(sum(1 for i in items if i.get('category') == 'dress')),
        float(sum(1 for i in items if i.get('category') == 'pants')),
        float(sum(1 for i in items if i.get('category') == 'outerwear')),
        float(first_item.get('is_new_size_for_customer', 0)),
    ]
    # Pad to 50 features
    sizing_features.extend([0.0] * (50 - len(sizing_features)))

    # Domain B: Product content quality
    content_features = [
        float(first_item.get('image_count', 1)) / 10.0,           # normalized
        float(first_item.get('has_lifestyle_photo', 0)),
        float(first_item.get('has_detail_shot', 0)),
        float(first_item.get('description_word_count', 50)) / 500.0,
        float(first_item.get('color_photography_mismatch_risk', 0.5)),  # from ATLAS
        float(first_item.get('fabric_hand_unclear', 0)),           # from ATLAS
        float(first_item.get('attribute_extraction_confidence', 0.5)),  # from ATLAS
        float(first_item.get('is_new_product', 0)),                # cold start
        float(first_item.get('days_since_product_launch', 30)) / 365.0,
        float(first_item.get('sku_level_return_rate_30d', 0.0)),   # historical signal
    ]
    content_features.extend([0.0] * (50 - len(content_features)))

    # Domain C: Customer history
    history_features = [
        float(customer.get('lifetime_orders', 0)) / 100.0,
        float(customer.get('lifetime_return_rate', 0.0)),
        float(customer.get('returns_last_90d', 0)),
        float(customer.get('orders_last_90d', 0)),
        float(customer.get('is_first_order', 1)),
        float(customer.get('days_since_last_order', 999)) / 365.0,
        float(customer.get('account_age_days', 0)) / 730.0,
        float(customer.get('wishlist_items_purchased_pct', 0.0)),  # wishlist → purchase = lower return
        float(customer.get('has_provided_style_profile', 0)),      # GROVE integration
        float(customer.get('style_drift_velocity', 0.0)),          # GROVE: volatile taste = higher risk
    ]
    history_features.extend([0.0] * (50 - len(history_features)))

    # Domain D: Order context
    hour = datetime.now(timezone.utc).hour
    day_of_week = datetime.now(timezone.utc).weekday()
    context_features = [
        float(order.get('total_value', 0)) / 500.0,               # normalized order value
        float(order.get('discount_pct', 0.0)),                     # heavy discount → impulse buy → return
        float(order.get('is_sale_item', 0)),
        float(order.get('shipping_method') == 'express'),          # express shipping → less considered
        float(hour) / 24.0,                                        # time of day signal
        float(day_of_week) / 7.0,
        float(order.get('items_browsed_before_purchase', 1)) / 20.0,  # research depth
        float(order.get('time_on_pdp_seconds', 30)) / 300.0,
        float(order.get('used_search', 0)),                        # search buyers less uncertain
        float(order.get('used_recommendation', 0)),                # rec-driven → different intent
    ]
    context_features.extend([0.0] * (50 - len(context_features)))

    return sizing_features + content_features + history_features + context_features


def handler(event: dict, context: Any) -> dict:
    """
    API Gateway Lambda proxy integration.
    Called by merchant checkout system.

    Request body:
    {
      "tenant_id": "brand-xyz",
      "order_id": "ORD-12345",
      "customer": { "lifetime_orders": 3, "lifetime_return_rate": 0.2, ... },
      "items": [{ "sku_id": "...", "size": "M", "category": "dress", ... }]
    }

    Response:
    {
      "order_id": "ORD-12345",
      "return_risk_score": 0.73,
      "risk_level": "HIGH",
      "top_risk_factors": ["no_size_chart", "new_customer", "first_order_in_category"],
      "causal_flags": { "sizing_domain_score": 0.81, "content_domain_score": 0.52, ... },
      "recommended_action": "show_size_chart_modal",
      "latency_ms": 87
    }
    """
    start_time = time.time()

    # Parse body (API Gateway wraps it as a string)
    body = event.get('body', '{}')
    if isinstance(body, str):
        order = json.loads(body)
    else:
        order = body

    tenant_id = order.get('tenant_id')
    order_id = order.get('order_id')

    if not tenant_id or not order_id:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'tenant_id and order_id are required'}),
        }

    # Build feature vector
    features = build_feature_vector(order)
    feature_csv = ','.join(str(f) for f in features)

    # Call SageMaker XGBoost endpoint
    sm_response = sagemaker_runtime.invoke_endpoint(
        EndpointName=SAGEMAKER_ENDPOINT,
        ContentType='text/csv',
        Body=feature_csv,
    )
    risk_score = float(sm_response['Body'].read().decode('utf-8').strip())

    # Classify risk level
    if risk_score >= RISK_THRESHOLD_HIGH:
        risk_level = 'HIGH'
        recommended_action = 'show_size_chart_modal'
    elif risk_score >= RISK_THRESHOLD_MEDIUM:
        risk_level = 'MEDIUM'
        recommended_action = 'show_fit_guidance'
    else:
        risk_level = 'LOW'
        recommended_action = 'none'

    # Domain-level breakdown (coarse — full causal analysis runs async via explainFn)
    # These are the summed scores within each feature domain, normalized
    causal_flags = {
        'sizing_domain_score': round(sum(features[0:50]) / 50, 3),
        'content_domain_score': round(sum(features[50:100]) / 50, 3),
        'history_domain_score': round(sum(features[100:150]) / 50, 3),
        'context_domain_score': round(sum(features[150:200]) / 50, 3),
    }

    # Simple top risk factor identification (heuristic — full explanation async)
    top_risk_factors = []
    if features[0] < 0.5:
        top_risk_factors.append('no_size_chart')
    if features[4] > 0.65:
        top_risk_factors.append('high_size_ambiguity')
    if features[51] < 0.3:
        top_risk_factors.append('low_image_count')
    if features[100] < 0.1:
        top_risk_factors.append('new_customer')
    if features[102] > 0:
        top_risk_factors.append('recent_returns')

    latency_ms = round((time.time() - start_time) * 1000)

    result = {
        'order_id': order_id,
        'tenant_id': tenant_id,
        'return_risk_score': round(risk_score, 4),
        'risk_level': risk_level,
        'top_risk_factors': top_risk_factors[:3],
        'causal_flags': causal_flags,
        'recommended_action': recommended_action,
        'latency_ms': latency_ms,
        'scored_at': datetime.now(timezone.utc).isoformat(),
    }

    # Write to DynamoDB (async — don't block response)
    try:
        table.put_item(Item={
            'tenant_id': tenant_id,
            'order_id': order_id,
            'risk_score': Decimal(str(round(risk_score, 4))),
            'risk_level': risk_level,
            'top_risk_factors': top_risk_factors,
            'causal_flags': {k: Decimal(str(v)) for k, v in causal_flags.items()},
            'recommended_action': recommended_action,
            'order_payload': json.dumps(order),
            'scored_at': datetime.now(timezone.utc).isoformat(),
            'ttl': int(time.time()) + (365 * 24 * 3600 * 2),  # 2-year retention
        })
    except Exception:
        pass  # Don't fail the checkout on a write error

    # Emit to Kinesis for async causal analysis (HIGH risk only)
    if risk_level == 'HIGH':
        try:
            kinesis.put_record(
                StreamName=ORDER_STREAM,
                Data=json.dumps({
                    'event_type': 'high_risk_order_scored',
                    'tenant_id': tenant_id,
                    'order_id': order_id,
                    'risk_score': risk_score,
                    'causal_flags': causal_flags,
                    'items': order.get('items', []),
                }),
                PartitionKey=tenant_id,
            )
        except Exception:
            pass  # Non-blocking

    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(result),
    }
