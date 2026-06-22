# RoosCloset — B2B Fashion Intelligence Infrastructure

> Fashion e-commerce loses 25–40% of GMV to returns. Catalog attribution failures are the root cause. RoosCloset is AWS-native ML infrastructure that fixes it.

---

## Two Products. One Intelligence Layer.

### ATLAS — Semantic Catalog Intelligence
Upload a product image → get 180+ structured attributes, style embeddings, consumer copy, and return risk flags in <60 seconds.

**Replaces:** Manual tagging teams + $50K/year attribution tools (Lily AI)

### MIRROR — Causal Return Intelligence
Score every order at checkout in <150ms. Explain why it's high-risk. Prescribe ranked interventions with ROI estimates.

**Replaces:** Predictive tools that don't explain + manual root cause analysis

---

## AWS Native Infrastructure

**15 AWS Services:** S3 · Step Functions · Lambda · SageMaker · Bedrock · Kinesis · DynamoDB · OpenSearch Serverless · API Gateway · EventBridge · Cognito · Rekognition · CloudWatch · X-Ray

**Not a SaaS wrapper.** Not a Lambda calling an API. Production ML infrastructure from day one.

---

## Repository Structure

```
rooscloset/                                    # Root directory
├── shared-stack.ts                           # Multi-tenancy, auth, API Gateway
├── atlas-stack.ts                            # ATLAS pipeline (Step Functions)
├── mirror-stack.ts                           # MIRROR scoring (Kinesis + Lambda)
├── app.ts                                    # CDK app entry point
│
├── ingest.py                                 # ATLAS Stage 1: Validate
├── rekognition_detect.py                     # ATLAS Stage 2: Garment detection
├── embed.py                                  # ATLAS Stage 3: SageMaker CLIP
├── attribute.py                              # ATLAS Stage 4: Bedrock attributes
├── index.py                                  # ATLAS Stage 5: OpenSearch indexing
│
├── score.py                                  # MIRROR: Real-time scoring
├── explain.py                                # MIRROR: Causal attribution
├── prescribe.py                              # MIRROR: Intervention prescription
│
├── product_attributes.json                   # 180-field attribute schema
│
├── package.json                              # Node dependencies
├── tsconfig.json                             # TypeScript config
├── cdk.json                                  # CDK config
├── .gitignore                                # Git ignore
│
├── README.md                                 # This file
├── architecture.md                           # Technical architecture
├── AWS_ACTIVATE_FOUNDERS.md                  # AWS Activate application
│
└── /website/                                 # Website files (Next.js, HTML, etc.)
    ├── [Your website files go here]
    └── [Landing page, pricing, blog, etc.]
```

---

## Quickstart

```bash
# Install dependencies
npm install

# Deploy all infrastructure
npm run deploy
```

Full infrastructure (API, databases, pipelines, multi-tenancy) deployed in 5 minutes.

---

## For AWS Activate Reviewers

See **`AWS_ACTIVATE_FOUNDERS.md`** for the complete application narrative.

Quick version:
- **Problem:** Fashion returns are $100B+ loss. Root cause is catalog intelligence + return causality gaps.
- **Solution:** Two AWS-native products (ATLAS for attributes, MIRROR for causal return scoring)
- **Why AWS:** Real multi-service architecture, not a wrapper. 15 services, genuine dependency.
- **Stage:** Pre-revenue. Code is deployable. Ready for design partners.
- **Founder:** Self-taught ML engineer, 6 production systems, AWS + SageMaker experience.

---

## Full Documentation

- **`architecture.md`** — Technical deep dive (all 15 services explained)
- **`AWS_ACTIVATE_FOUNDERS.md`** — AWS Activate application (problem, solution, business model)

---

## Cost

- **Idle:** $35/month
- **At scale (1K tenants):** $1.6K/month

---

## Contact

**Founder:** Najmun Nahar Khan  
**Email:** Najmun@rooscloset.store  
**Domain:** RoosCloset.store  
**Location:** Dhaka, Bangladesh

---

## AWS Activate

For AWS Activate Founders application details, see `AWS_ACTIVATE_FOUNDERS.md`.
