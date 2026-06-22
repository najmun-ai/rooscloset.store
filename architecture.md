# RoosCloset — Architecture Documentation

## Overview

RoosCloset is a B2B AWS-native intelligence layer for fashion e-commerce.
Two products at launch. Both are API-first, multi-tenant, and deeply integrated with AWS managed services.

---

## ATLAS — Semantic Catalog Intelligence

### Problem
Merchant product data is written for warehouses (`PLT-BLK-MD-001`), not for how customers
search ("something to wear to a rooftop bar"). 82% of search and recommendation failures in
fashion trace back to attribution failures — the system doesn't know enough about the product
to match it to intent.

### Solution
A five-stage AWS Step Functions pipeline that takes raw product images + sparse merchant 
descriptions and emits a rich semantic attribute graph per SKU.

### Pipeline

```
S3 Upload → SQS → Step Functions Orchestrator
                    │
                    ├── Stage 1: Lambda (Ingest & Validate)
                    │     Normalize formats, extract metadata, set initial DynamoDB state
                    │
                    ├── Stage 2: Lambda → Rekognition
                    │     DetectLabels: garment region, coarse category, clothing items
                    │     Output: [{Name: "Dress", Confidence: 98.2}, ...]
                    │
                    ├── Stage 3: Lambda → SageMaker Endpoint (ViT-L/14 CLIP)
                    │     Generate 512-dim style embedding from product image
                    │     Stored in S3: processed/{tenant_id}/embeddings/{sku_id}.json
                    │     Written to OpenSearch k-NN index for similarity search
                    │
                    ├── Stage 4: Lambda → Bedrock (Claude 3 Sonnet)
                    │     Multimodal: image + Rekognition labels + merchant description
                    │     Output: 180+ structured attributes (JSON schema in atlas/schema/)
                    │     Includes: return_risk_flags fed directly to MIRROR
                    │
                    └── Stage 5: Lambda → OpenSearch Serverless
                          Index: both embedding (k-NN) and structured attributes (keyword)
                          Enables: vibe search + attribute-filtered discovery
```

### Data Flow

```
Merchant Upload (S3)
        │
        ▼
┌─────────────────┐     ┌──────────────────────────────────────────────┐
│   SQS Queue     │────▶│          Step Functions Pipeline              │
│  (buffer S3     │     │  ingest → rekognition → embed → attr → index │
│   events)       │     └────────────────────┬─────────────────────────┘
└─────────────────┘                          │
                                             ▼
                          ┌──────────────────────────────────┐
                          │          DynamoDB                 │
                          │   sku-attributes table            │
                          │   - processing_status             │
                          │   - key attribute fields          │
                          │   - return_risk_flags (→ MIRROR)  │
                          │   - attributes_s3_key             │
                          └──────────────────────────────────┘
                                             │
                          ┌──────────────────▼──────────────────────────┐
                          │         OpenSearch Serverless                 │
                          │  Collection: rooscloset-catalog               │
                          │  Index 1: style-embeddings (k-NN, 512-dim)   │
                          │  Index 2: product-attributes (keyword/filter) │
                          └──────────────────────────────────────────────┘
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /v1/atlas/ingest | Submit product batch for processing |
| GET | /v1/atlas/products/{sku_id} | Get enriched product with all attributes |
| POST | /v1/atlas/search | Semantic + attribute-filtered search |
| GET | /v1/atlas/status/{job_id} | Check pipeline processing status |

### Search Query Examples

```json
// Vibe search: embedding similarity
POST /v1/atlas/search
{
  "query_type": "vibe",
  "query": "something to wear to a garden party",
  "filters": { "occasion_primary": "occasion", "season_affinity": ["spring", "summer"] },
  "limit": 20
}

// Aesthetic cluster search: style manifold position
POST /v1/atlas/search
{
  "query_type": "aesthetic",
  "aesthetic_vector": { "quiet_luxury": 0.8, "minimalist": 0.6 },
  "filters": { "formality_score": { "gte": 0.5 } },
  "limit": 20
}
```

---

## MIRROR — Causal Return Intelligence

### Problem
Fashion e-commerce return rates: 25–40%. Existing tools are either logistics 
(Loop Returns, Happy Returns) or predictive (predict returns but don't explain why 
or prescribe fixes). Prediction without causation is a dashboard, not a solution.

### Solution
Three-layer system that runs at checkout: Predict → Explain → Prescribe.

```
┌─────────────────────────────────────────────────┐
│  Layer 1: PREDICT                               │
│  Return probability per order BEFORE it ships   │
│  XGBoost on 200+ features across 4 domains     │
├─────────────────────────────────────────────────┤
│  Layer 2: EXPLAIN (async)                       │
│  Causal attribution via DoWhy graph             │
│  - Sizing uncertainty (47% of returns)          │
│  - Photography color mismatch (31%)             │
│  - Description mismatch (15%)                   │
│  - Shipping/damage signals (7%)                 │
├─────────────────────────────────────────────────┤
│  Layer 3: PRESCRIBE (async)                     │
│  Ranked interventions with ROI estimates        │
│  Generated by Bedrock (Claude) + intervention   │
│  templates, tracked for outcome measurement     │
└─────────────────────────────────────────────────┘
```

### Architecture

```
Merchant Checkout
        │
        ▼
API Gateway → Lambda (score.py)          < 150ms P99
                    │
                    ├── Build 200-feature vector
                    ├── SageMaker XGBoost endpoint
                    ├── Return risk score + risk level
                    ├── Write to DynamoDB (async)
                    └── Emit HIGH risk to Kinesis
                              │
                              ▼
                    Kinesis Data Streams
                              │
                    Lambda (explain.py) ←── EventBridge (high_risk_order_scored)
                              │
                              ├── Load DoWhy causal graph from S3
                              ├── Estimate causal effects per domain
                              ├── Bedrock (Claude Haiku): generate intervention brief
                              ├── Write attributions to DynamoDB
                              └── Emit CausalAttributionComplete → EventBridge
                                            │
                                Lambda (prescribe.py)
                                            │
                                            ├── Rank interventions by estimated ROI
                                            ├── Bedrock (Claude Sonnet): detailed brief
                                            └── Write prescriptions to DynamoDB
                                                (surfaced in QuickSight dashboard)
```

### Feature Vector: 200 Features Across 4 Causal Domains

| Domain | Features 0-49 | Features 50-99 | Features 100-149 | Features 150-199 |
|--------|---------------|----------------|------------------|------------------|
| Name | Sizing Signals | Content Quality | Customer History | Order Context |
| Key inputs | size_chart_present, size_ambiguity_risk (from ATLAS) | image_count, color_mismatch_risk (from ATLAS), description_length | lifetime_return_rate, orders_last_90d, first_order | discount_pct, time_on_pdp, used_search |

### Causal Graph: DoWhy (Microsoft)

```python
# Trained weekly by SageMaker Pipeline on accumulated return labels
causal_model = dowhy.CausalModel(
    data=return_history_df,
    treatment="product_has_size_chart",
    outcome="return_initiated",
    common_causes=["product_category", "price_band", "customer_segment", "season"]
)

# Result: "Adding size chart CAUSES 12.3% reduction in sizing returns"
# (not correlation — the backdoor adjustment controls for confounders)
estimate = causal_model.estimate_effect(
    method_name="backdoor.propensity_score_matching"
)
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /v1/mirror/score | Score an order at checkout |
| GET | /v1/mirror/interventions/{sku_id} | Get current prescriptions for a SKU |
| GET | /v1/mirror/dashboard/{tenant_id} | QuickSight embed URL for merchandising dashboard |
| POST | /v1/mirror/feedback | Record intervention outcome (was it applied? did it work?) |

### Score Response Example

```json
POST /v1/mirror/score

Response:
{
  "order_id": "ORD-48291",
  "return_risk_score": 0.73,
  "risk_level": "HIGH",
  "top_risk_factors": ["no_size_chart", "new_customer", "high_size_ambiguity"],
  "causal_flags": {
    "sizing_domain_score": 0.81,
    "content_domain_score": 0.52,
    "history_domain_score": 0.67,
    "context_domain_score": 0.31
  },
  "recommended_action": "show_size_chart_modal",
  "latency_ms": 87
}
```

---

## Cross-Product Integration

ATLAS and MIRROR are deeply coupled:

```
ATLAS return_risk_flags ──────────────────▶ MIRROR feature vector (Domain B)
  color_photography_mismatch_risk              features[54]: color_risk
  size_ambiguity_risk                          features[4]: size_ambiguity
  fabric_hand_unclear                          features[56]: fabric_unclear
  needs_size_chart                             features[0]: size_chart_absent

MIRROR causal attributions ───────────────▶ ATLAS re-processing queue
  "photography mismatch confirmed"             Flag SKU for re-attribute
  "description mismatch confirmed"             Trigger Bedrock re-extraction
```

This feedback loop improves both systems over time: MIRROR's causal findings make
ATLAS's attribute quality signals more accurate.

---

## Multi-Tenancy

| Concern | Implementation |
|---------|---------------|
| Auth | Cognito User Pool with tenant_id custom attribute |
| Data isolation | S3: tenant-prefixed paths; DynamoDB: tenant_id partition key |
| Compute isolation | Lambda: tenant_id in event context; IAM resource-scoped |
| Search isolation | OpenSearch: index-per-tenant (`{tenant_id}-catalog`) |
| Billing | Resource tags: Project=RoosCloset, TenantId={tenant_id} → Cost Allocation |

---

## AWS Services Summary

| Service | Stack | Usage |
|---------|-------|-------|
| S3 | Shared | Data lake: raw images, processed attributes, model artifacts |
| Step Functions | ATLAS | 5-stage pipeline orchestration with error handling |
| SQS | ATLAS | Decouple S3 events from pipeline execution |
| Rekognition | ATLAS | Garment detection (DetectLabels) |
| SageMaker Endpoint | ATLAS + MIRROR | CLIP embeddings (ATLAS); XGBoost scorer (MIRROR) |
| SageMaker Pipelines | MIRROR | Weekly automated retraining on return labels |
| Bedrock (Claude 3) | ATLAS + MIRROR | Attribute extraction (Sonnet); causal briefs (Haiku) |
| OpenSearch Serverless | ATLAS | k-NN vector search + keyword attribute search |
| Kinesis Data Streams | MIRROR | Real-time order event ingestion |
| DynamoDB | ATLAS + MIRROR | SKU attributes; return events; intervention tracking |
| EventBridge | MIRROR | Route high-risk orders; trigger weekly retraining |
| API Gateway | Shared | REST API for B2B merchant integration |
| Lambda | All | Compute glue across all pipeline stages |
| Cognito | Shared | Multi-tenant authentication |
| CloudWatch + X-Ray | All | Distributed tracing, metrics, alerting |
| QuickSight | MIRROR | Embedded causal dashboard for merchandising teams |
