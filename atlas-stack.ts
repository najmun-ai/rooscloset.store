import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as opensearch from 'aws-cdk-lib/aws-opensearchserverless';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3n from 'aws-cdk-lib/aws-s3-notifications';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as events from 'aws-cdk-lib/aws-lambda-event-sources';
import { Construct } from 'constructs';
import { SharedStack } from './shared-stack';

interface AtlasStackProps extends cdk.StackProps {
  shared: SharedStack;
}

export class AtlasStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AtlasStackProps) {
    super(scope, id, props);

    const { dataLake, api, bedrockRole } = props.shared;

    // ── DynamoDB: SKU Attribute Store ──────────────────────────────────────
    // Tracks attribute version history per SKU — MIRROR reads this for return features
    const skuTable = new dynamodb.Table(this, 'SkuAttributeTable', {
      tableName: 'rooscloset-sku-attributes',
      partitionKey: { name: 'tenant_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sku_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      stream: dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,  // MIRROR subscribes to attribute changes
    });

    // GSI: query by processing_status (pending | processing | complete | failed)
    skuTable.addGlobalSecondaryIndex({
      indexName: 'by-status',
      partitionKey: { name: 'tenant_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'processing_status', type: dynamodb.AttributeType.STRING },
    });

    // ── SQS: Ingestion Queue (decouples S3 events from Step Functions) ─────
    const dlq = new sqs.Queue(this, 'IngestionDLQ', {
      retentionPeriod: cdk.Duration.days(14),
    });

    const ingestionQueue = new sqs.Queue(this, 'IngestionQueue', {
      visibilityTimeout: cdk.Duration.minutes(15),
      deadLetterQueue: { queue: dlq, maxReceiveCount: 3 },
    });

    // S3 PUT to raw/{tenant_id}/images/ → SQS
    dataLake.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.SqsDestination(ingestionQueue),
      { prefix: 'raw/', suffix: '.jpg' }
    );
    dataLake.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.SqsDestination(ingestionQueue),
      { prefix: 'raw/', suffix: '.png' }
    );

    // ── Lambda: Stage 1 — Ingest & Validate ───────────────────────────────
    const ingestFn = new lambda.Function(this, 'IngestFn', {
      functionName: 'atlas-ingest',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'ingest.handler',
      code: lambda.Code.fromAsset('../atlas/handlers'),
      timeout: cdk.Duration.minutes(2),
      memorySize: 512,
      role: bedrockRole,
      environment: {
        SKU_TABLE: skuTable.tableName,
        DATA_LAKE_BUCKET: dataLake.bucketName,
      },
    });
    skuTable.grantReadWriteData(ingestFn);
    ingestionQueue.grantConsumeMessages(ingestFn);
    ingestFn.addEventSource(new events.SqsEventSource(ingestionQueue, { batchSize: 10 }));

    // ── Lambda: Stage 2 — Rekognition Garment Detection ───────────────────
    const rekognitionFn = new lambda.Function(this, 'RekognitionFn', {
      functionName: 'atlas-rekognition',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'rekognition_detect.handler',
      code: lambda.Code.fromAsset('../atlas/handlers'),
      timeout: cdk.Duration.minutes(3),
      memorySize: 512,
      environment: {
        SKU_TABLE: skuTable.tableName,
        DATA_LAKE_BUCKET: dataLake.bucketName,
      },
    });
    rekognitionFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['rekognition:DetectLabels', 'rekognition:DetectModerationLabels'],
      resources: ['*'],
    }));
    skuTable.grantReadWriteData(rekognitionFn);
    dataLake.grantRead(rekognitionFn);

    // ── Lambda: Stage 3 — SageMaker CLIP Embedding ────────────────────────
    const embedFn = new lambda.Function(this, 'EmbedFn', {
      functionName: 'atlas-embed',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'embed.handler',
      code: lambda.Code.fromAsset('../atlas/handlers'),
      timeout: cdk.Duration.minutes(5),
      memorySize: 1024,
      environment: {
        SKU_TABLE: skuTable.tableName,
        DATA_LAKE_BUCKET: dataLake.bucketName,
        // SAGEMAKER_ENDPOINT_NAME injected at deploy time
        SAGEMAKER_ENDPOINT_NAME: 'atlas-clip-vit-l14',
      },
    });
    embedFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['sagemaker:InvokeEndpoint'],
      resources: [`arn:aws:sagemaker:${this.region}:${this.account}:endpoint/atlas-clip-vit-l14`],
    }));
    skuTable.grantReadWriteData(embedFn);
    dataLake.grantReadWrite(embedFn);

    // ── Lambda: Stage 4 — Bedrock Attribute Extraction ────────────────────
    const attributeFn = new lambda.Function(this, 'AttributeFn', {
      functionName: 'atlas-attribute',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'attribute.handler',
      code: lambda.Code.fromAsset('../atlas/handlers'),
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      role: bedrockRole,
      environment: {
        SKU_TABLE: skuTable.tableName,
        DATA_LAKE_BUCKET: dataLake.bucketName,
        BEDROCK_MODEL_ID: 'anthropic.claude-3-sonnet-20240229-v1:0',
      },
    });
    skuTable.grantReadWriteData(attributeFn);
    dataLake.grantRead(attributeFn);

    // ── Lambda: Stage 5 — OpenSearch Index ────────────────────────────────
    const indexFn = new lambda.Function(this, 'IndexFn', {
      functionName: 'atlas-index',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('../atlas/handlers'),
      timeout: cdk.Duration.minutes(3),
      memorySize: 512,
      environment: {
        SKU_TABLE: skuTable.tableName,
        OPENSEARCH_ENDPOINT: `https://rooscloset-catalog.${this.region}.aoss.amazonaws.com`,
      },
    });
    skuTable.grantReadWriteData(indexFn);

    // ── Step Functions: ATLAS Pipeline Orchestrator ────────────────────────
    // ingest → rekognition → embed → attribute → index (→ QA on failure)

    const ingestStep = new tasks.LambdaInvoke(this, 'IngestValidate', {
      lambdaFunction: ingestFn,
      outputPath: '$.Payload',
    });

    const rekognitionStep = new tasks.LambdaInvoke(this, 'GarmentDetection', {
      lambdaFunction: rekognitionFn,
      outputPath: '$.Payload',
    });

    const embedStep = new tasks.LambdaInvoke(this, 'GenerateEmbedding', {
      lambdaFunction: embedFn,
      outputPath: '$.Payload',
    });

    const attributeStep = new tasks.LambdaInvoke(this, 'ExtractAttributes', {
      lambdaFunction: attributeFn,
      outputPath: '$.Payload',
    });

    const indexStep = new tasks.LambdaInvoke(this, 'IndexToOpenSearch', {
      lambdaFunction: indexFn,
      outputPath: '$.Payload',
    });

    const failState = new sfn.Fail(this, 'PipelineFailed', {
      error: 'AtlasPipelineError',
      cause: 'See CloudWatch logs for details',
    });

    const succeedState = new sfn.Succeed(this, 'PipelineComplete');

    // Chain with error handling at each stage
    const definition = ingestStep
      .addCatch(failState, { errors: ['States.ALL'], resultPath: '$.error' })
      .next(rekognitionStep.addCatch(failState, { errors: ['States.ALL'], resultPath: '$.error' }))
      .next(embedStep.addCatch(failState, { errors: ['States.ALL'], resultPath: '$.error' }))
      .next(attributeStep.addCatch(failState, { errors: ['States.ALL'], resultPath: '$.error' }))
      .next(indexStep.addCatch(failState, { errors: ['States.ALL'], resultPath: '$.error' }))
      .next(succeedState);

    const atlasPipeline = new sfn.StateMachine(this, 'AtlasPipeline', {
      stateMachineName: 'atlas-catalog-pipeline',
      definition,
      timeout: cdk.Duration.minutes(30),
      tracingEnabled: true,
    });

    // ── OpenSearch Serverless Collection ───────────────────────────────────
    // Vector index (k-NN) for style embeddings + structured index for attributes
    const opensearchCollection = new opensearch.CfnCollection(this, 'CatalogCollection', {
      name: 'rooscloset-catalog',
      type: 'VECTORSEARCH',
      description: 'Style embedding index + structured attribute index for ATLAS',
    });

    // Encryption policy (required)
    new opensearch.CfnSecurityPolicy(this, 'CatalogEncryption', {
      name: 'rooscloset-catalog-encryption',
      type: 'encryption',
      policy: JSON.stringify({
        Rules: [{ Resource: ['collection/rooscloset-catalog'], ResourceType: 'collection' }],
        AWSOwnedKey: true,
      }),
    });

    // Network policy (VPC or public)
    new opensearch.CfnSecurityPolicy(this, 'CatalogNetwork', {
      name: 'rooscloset-catalog-network',
      type: 'network',
      policy: JSON.stringify([
        {
          Rules: [
            { Resource: ['collection/rooscloset-catalog'], ResourceType: 'collection' },
            { Resource: ['collection/rooscloset-catalog'], ResourceType: 'dashboard' },
          ],
          AllowFromPublic: true,
        },
      ]),
    });

    // ── API: /atlas endpoints ──────────────────────────────────────────────
    const atlasResource = api.root.addResource('atlas');

    // POST /atlas/ingest — merchant submits product batch
    const ingestResource = atlasResource.addResource('ingest');
    ingestResource.addMethod('POST', new cdk.aws_apigateway.LambdaIntegration(ingestFn));

    // GET /atlas/products/{sku_id} — retrieve enriched product data
    const productsResource = atlasResource.addResource('products');
    const skuResource = productsResource.addResource('{sku_id}');
    skuResource.addMethod('GET', new cdk.aws_apigateway.LambdaIntegration(indexFn));

    // POST /atlas/search — semantic + attribute-filtered search
    const searchResource = atlasResource.addResource('search');
    searchResource.addMethod('POST', new cdk.aws_apigateway.LambdaIntegration(indexFn));

    // ── Outputs ────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'SkuTableName', { value: skuTable.tableName });
    new cdk.CfnOutput(this, 'PipelineArn', { value: atlasPipeline.stateMachineArn });
    new cdk.CfnOutput(this, 'OpenSearchEndpoint', {
      value: opensearchCollection.attrCollectionEndpoint,
    });
  }
}
