# GitHub Push Checklist — RoosCloset to AWS Activate

## Contact Info

- **Founder:** Najmun Nahar Khan
- **Email:** Najmun@rooscloset.store
- **Domain:** RoosCloset.store
- **AWS Email:** naju.nagar.khan@gmail.com
- **Location:** Dhaka, Bangladesh

---

## Pre-Push Verification (Local)

- [x] All files are in root directory (no subdirectories)
- [x] All CDK TypeScript compiles: `npm run build`
- [x] All Python handlers are syntactically valid
- [x] `AWS_ACTIVATE_FOUNDERS.md` is complete
- [x] `README.md` is clear and links to narrative
- [x] `architecture.md` explains all 15 AWS services
- [x] `.gitignore` excludes node_modules, dist, cdk.out
- [x] No secrets in any files
- [x] Contact info is updated (Najmun Nahar Khan, RoosCloset.store)
- [x] `website/` directory exists for future website files

---

## Files in Root (Everything)

```
rooscloset/
├── shared-stack.ts                    ✓
├── atlas-stack.ts                     ✓
├── mirror-stack.ts                    ✓
├── app.ts                             ✓
│
├── ingest.py                          ✓
├── rekognition_detect.py              ✓
├── embed.py                           ✓
├── attribute.py                       ✓
├── index.py                           ✓
│
├── score.py                           ✓
├── explain.py                         ✓
├── prescribe.py                       ✓
│
├── product_attributes.json            ✓
│
├── package.json                       ✓
├── tsconfig.json                      ✓
├── cdk.json                           ✓
│
├── README.md                          ✓
├── architecture.md                    ✓
├── AWS_ACTIVATE_FOUNDERS.md           ✓
│
├── .gitignore                         ✓
├── LICENSE                            ✓
│
└── website/                           ✓ (placeholder)
    └── README.md
```

---

## Git Workflow

### 1. Initialize repo

```bash
cd /path/to/rooscloset  # Root directory with all files

# Create .gitignore (already done)
# Create LICENSE (already done)

# Initialize git
git init
git config user.name "Najmun Nahar Khan"
git config user.email "Najmun@rooscloset.store"
git add .
git commit -m "RoosCloset: AWS-native B2B fashion intelligence

Two products: ATLAS (semantic catalog) + MIRROR (causal return intelligence)

Architecture: 15 AWS services
- S3, Step Functions, Lambda, SageMaker, Bedrock
- Kinesis, DynamoDB, OpenSearch Serverless
- API Gateway, EventBridge, Cognito, Rekognition, CloudWatch, X-Ray

Infrastructure-as-Code (CDK TypeScript)
8 Lambda handlers (Python)
Multi-tenant B2B system, production-ready

See AWS_ACTIVATE_FOUNDERS.md for application narrative"
```

### 2. Create GitHub repo

Go to https://github.com/new

- **Repository name:** rooscloset
- **Description:** AWS-native B2B ML infrastructure for fashion e-commerce
- **Visibility:** Public
- **Initialize:** None (we already have commits)

### 3. Push to GitHub

```bash
git branch -M main
git remote add origin https://github.com/[YOUR_GITHUB_HANDLE]/rooscloset.git
git push -u origin main
```

### 4. Verify on GitHub

- [ ] All files visible at github.com/[YOUR_GITHUB_HANDLE]/rooscloset
- [ ] `AWS_ACTIVATE_FOUNDERS.md` is visible in root
- [ ] `README.md` shows as landing page
- [ ] Code is readable
- [ ] `website/` folder exists

---

## AWS Activate Application Form

**Go to:** https://activate.aws/

**Click:** Apply → Founders

**Fill out:**

| Field | Value |
|-------|-------|
| Company Name | RoosCloset |
| Website/Domain | RoosCloset.store |
| Email | Najmun@rooscloset.store |
| Location | Dhaka, Bangladesh |

**Narrative Fields:**

**Q: Describe your startup in one sentence**
> B2B ML infrastructure for fashion e-commerce that extracts semantic product attributes and scores return risk at checkout using AWS.

**Q: What problem are you solving?**
> (Copy from AWS_ACTIVATE_FOUNDERS.md "Problem" section)

**Q: What is your solution?**
> (Copy from AWS_ACTIVATE_FOUNDERS.md "Solution" section)

**Q: What AWS services do you use?**
> S3, Step Functions, SQS, Lambda, Rekognition, SageMaker (Endpoints + Pipelines), Bedrock (Claude 3), OpenSearch Serverless, Kinesis, DynamoDB, API Gateway, EventBridge, Cognito, CloudWatch, X-Ray. (15 services total)

**Q: What stage is your company at?**
> Pre-revenue. Infrastructure is deployable (CDK + Lambda). Ready for design partners.

**Q: Estimated monthly AWS spend at scale**
> Current (idle): ~$35/month
> At scale (1000 tenants, 10M orders/month): $1.5–2.5K/month

**Q: Link to your code/repo**
> https://github.com/[YOUR_GITHUB_HANDLE]/rooscloset

**Q: Tell us about the founding team**
> Najmun Nahar Khan. Self-taught ML engineer. 6 production AI systems shipped (flood detection, disease forecasting, industrial QC, etc.). AWS + SageMaker experience. Focused on building production ML infrastructure for fashion e-commerce.

**Q: Why do you need AWS Activate credits?**
> To deploy infrastructure at scale without immediate operational costs. Credits provide runway for design partner onboarding, user feedback, and product iteration before revenue generation.

---

## After Submission

**Timeline:** 1–2 weeks for approval

**If approved:**
- Credits land in your account
- Deploy infrastructure using `npm run deploy`
- Onboard design partners

**If rejected:**
- AWS is sometimes capricious with international founders
- Infrastructure is solid regardless
- Can use GCP (Vertex AI) or Azure instead

---

## What NOT to Do

- ❌ Mention "just trying AWS"
- ❌ Say "building an AI wrapper"
- ❌ Oversell (be honest about pre-revenue)
- ❌ Use buzzwords without substance
- ❌ Hide the GitHub link

---

## What TO Emphasize

- ✅ Real infrastructure (15 AWS services)
- ✅ Deployable code (CDK, not pseudocode)
- ✅ Real problem (fashion returns = $100B)
- ✅ Non-obvious solution (causal attribution)
- ✅ AWS dependency (can't be done cheaply without these services)
- ✅ Founder credibility (previous systems, self-taught)

---

## Final Checklist Before Submitting

- [ ] GitHub repo is public and complete
- [ ] All files are in root (flat structure)
- [ ] `AWS_ACTIVATE_FOUNDERS.md` is in root
- [ ] `README.md` links to `AWS_ACTIVATE_FOUNDERS.md`
- [ ] All code compiles (`npm run build` succeeds)
- [ ] All handlers are present (no missing files)
- [ ] No secrets in any files
- [ ] Contact info is correct (Najmun, RoosCloset.store)
- [ ] `website/` directory exists for future use
- [ ] Application form is filled out completely
- [ ] GitHub link matches your actual repo

---

## You're Ready

1. Initialize git in root directory
2. Push to GitHub
3. Fill out AWS Activate form
4. Submit

**Total time: 45 minutes**

That's it. The infrastructure is done. You're applying with real code, real AWS architecture, real problem.
