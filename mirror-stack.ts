import * as cdk from 'aws-cdk-lib';
import * as kinesis from 'aws-cdk-lib/aws-kinesis';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-lambda-event-sources';
import * as eventbridge from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as quicksight from 'aws-cdk-lib/aws-quicksight';
import { Construct } from 'constructs';
import { SharedStack } from './shared-stack';

interface MirrorStackProps extends cdk.StackProps {
  shared: SharedStack;
}

export class MirrorStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: MirrorStackProps) {
    super(scope, id, props);

    const { dataLake, api, bedrockRole } = props.shared;

    // ── Kinesis: Order Event Stream ────────────────────────────────────────
    // Every order event flows here: placed, confirmed, shipped, delivered, return_initiated, refunded
    const orderStream = new kinesis.Stream(this, 'OrderEventStream', {
      streamName: 'rooscloset-order-events',
      shardCount: 2,          // ~2K events/sec per shard — scale up per tenant
      retentionPeriod: cdk.Duration.days(7),
      encryption: kinesis.StreamEncryption.MANAGED,
    });

    // ── DynamoDB: Return Event + Intervention Store ────────────────────────
    const returnTable = new dynamodb.Table(this, 'ReturnEventTable', {
      tableName: 'rooscloset-return-events',
      partitionKey: { name: 'tenant_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'order_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      timeToLiveAttribute: 'ttl',   // expire old events after 2 years
    });

    // GSI: query high-risk orders by tenant + date for dashboard
    returnTable.addGlobalSecondaryIndex({
      indexName: 'by-risk-score',
      partitionKey: { name: 'tenant_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'risk_score', type: dynamodb.AttributeType.NUMBER },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // GSI: track intervention outcomes (was the prescription followed? did it work?)
    returnTable.addGlobalSecondaryIndex({
      indexName: 'by-intervention-status',
      partitionKey: { name: 'sku_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'intervention_applied_at', type: dynamodb.AttributeType.STRING },
    });

    // ── Lambda: Real-Time Return Scorer ───────────────────────────────────
    // Invoked at checkout (< 150ms P99 target)
    // Calls SageMaker XGBoost endpoint → return probability
    const scoreFn = new lambda.Function(this, 'ScoreFn', {
      functionName: 'mirror-score',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'score.handler',
      code: lambda.Code.fromAsset('../mirror/handlers'),
      timeout: cdk.Duration.seconds(10),    // tight timeout — this is on the checkout path
      memorySize: 512,
      reservedConcurrentExecutions: 50,     // avoid cold-start latency on burst
      environment: {
        RETURN_TABLE: returnTable.tableName,
        ORDER_STREAM: orderStream.streamName,
        SAGEMAKER_ENDPOINT: 'mirror-return-xgboost',
        RISK_THRESHOLD_HIGH: '0.65',
        RISK_THRESHOLD_MEDIUM: '0.40',
      },
    });

    scoreFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['sagemaker:InvokeEndpoint'],
      resources: [`arn:aws:sagemaker:${this.region}:${this.account}:endpoint/mirror-return-xgboost`],
    }));
    scoreFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['kinesis:PutRecord'],
      resources: [orderStream.streamArn],
    }));
    returnTable.grantReadWriteData(scoreFn);

    // ── Lambda: Causal Explanation Generator ──────────────────────────────
    // Runs async after score — reads DoWhy causal graph output from S3,
    // calls Bedrock to generate merchandiser-readable intervention brief
    const explainFn = new lambda.Function(this, 'ExplainFn', {
      functionName: 'mirror-explain',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'explain.handler',
      code: lambda.Code.fromAsset('../mirror/handlers'),
      timeout: cdk.Duration.minutes(2),
      memorySize: 1024,
      role: bedrockRole,
      environment: {
        RETURN_TABLE: returnTable.tableName,
        DATA_LAKE_BUCKET: dataLake.bucketName,
        BEDROCK_MODEL_ID: 'anthropic.claude-3-haiku-20240307-v1:0',  // Haiku for speed/cost
        CAUSAL_GRAPH_S3_KEY: 'models/mirror/causal_graph_latest.pkl',
      },
    });
    returnTable.grantReadWriteData(explainFn);
    dataLake.grantRead(explainFn);

    // Kinesis → explainFn (process return events in micro-batches)
    explainFn.addEventSource(new events.KinesisEventSource(orderStream, {
      startingPosition: lambda.StartingPosition.LATEST,
      batchSize: 25,
      bisectBatchOnError: true,
      retryAttempts: 3,
      filters: [
        // Only process return-related events — ignore placed/shipped
        lambda.FilterCriteria.filter({
          data: { event_type: lambda.FilterRule.isEqual('return_initiated') },
        }),
      ],
    }));

    // ── Lambda: Intervention Prescriber ───────────────────────────────────
    // Triggered by EventBridge when high-confidence causal attribution found.
    // Generates ranked interventions with ROI estimates per SKU.
    const prescribeFn = new lambda.Function(this, 'PrescribeFn', {
      functionName: 'mirror-prescribe',
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'prescribe.handler',
      code: lambda.Code.fromAsset('../mirror/handlers'),
      timeout: cdk.Duration.minutes(3),
      memorySize: 512,
      role: bedrockRole,
      environment: {
        RETURN_TABLE: returnTable.tableName,
        BEDROCK_MODEL_ID: 'anthropic.claude-3-sonnet-20240229-v1:0',
        // Intervention templates loaded from S3
        INTERVENTION_TEMPLATES_KEY: 'models/mirror/intervention_templates.json',
        DATA_LAKE_BUCKET: dataLake.bucketName,
      },
    });
    returnTable.grantReadWriteData(prescribeFn);
    dataLake.grantRead(prescribeFn);

    // ── EventBridge: Route High-Risk + Causal Findings ────────────────────
    const interventionBus = new eventbridge.EventBus(this, 'InterventionBus', {
      eventBusName: 'rooscloset-interventions',
    });

    // Rule: high return risk at order time → trigger immediate explanation
    new eventbridge.Rule(this, 'HighRiskRule', {
      eventBus: interventionBus,
      ruleName: 'high-risk-order',
      description: 'Order scored >0.65 return probability → queue for causal analysis',
      eventPattern: {
        source: ['rooscloset.mirror'],
        detailType: ['ReturnRiskScored'],
        detail: {
          risk_level: ['HIGH'],
        },
      },
      targets: [new targets.LambdaFunction(explainFn)],
    });

    // Rule: causal attribution complete → generate prescriptions
    new eventbridge.Rule(this, 'CausalCompleteRule', {
      eventBus: interventionBus,
      ruleName: 'causal-attribution-complete',
      description: 'Causal graph identified root cause → generate intervention recommendations',
      eventPattern: {
        source: ['rooscloset.mirror'],
        detailType: ['CausalAttributionComplete'],
        detail: {
          confidence: [{ numeric: ['>', 0.70] }],
        },
      },
      targets: [new targets.LambdaFunction(prescribeFn)],
    });

    // ── SageMaker Retraining: Scheduled Weekly ─────────────────────────────
    // EventBridge Scheduler triggers SageMaker Pipeline weekly
    // Pipeline: pull labeled return data → feature engineering → XGBoost retrain → evaluate → deploy
    new eventbridge.Rule(this, 'WeeklyRetrainRule', {
      ruleName: 'mirror-weekly-retrain',
      description: 'Weekly model retraining on accumulated return labels',
      schedule: eventbridge.Schedule.cron({ weekDay: 'MON', hour: '2', minute: '0' }),
      targets: [
        // Targets SageMaker Pipeline — ARN set post-pipeline creation
        new targets.LambdaFunction(prescribeFn),  // placeholder — replace with SageMaker pipeline target
      ],
    });

    // ── API: /mirror endpoints ─────────────────────────────────────────────
    const mirrorResource = api.root.addResource('mirror');

    // POST /mirror/score — checkout integration point
    // Merchant sends order payload, receives risk score + causal flags in < 150ms
    const scoreResource = mirrorResource.addResource('score');
    scoreResource.addMethod('POST', new cdk.aws_apigateway.LambdaIntegration(scoreFn, {
      timeout: cdk.Duration.seconds(8),     // API Gateway timeout < Lambda timeout
    }));

    // GET /mirror/interventions/{sku_id} — fetch current prescriptions for a SKU
    const interventionsResource = mirrorResource.addResource('interventions');
    const skuInterventionResource = interventionsResource.addResource('{sku_id}');
    skuInterventionResource.addMethod('GET', new cdk.aws_apigateway.LambdaIntegration(prescribeFn));

    // GET /mirror/dashboard/{tenant_id} — return causality summary (QuickSight embed URL)
    const dashboardResource = mirrorResource.addResource('dashboard');
    const tenantDashboardResource = dashboardResource.addResource('{tenant_id}');
    tenantDashboardResource.addMethod('GET', new cdk.aws_apigateway.LambdaIntegration(explainFn));

    // ── Outputs ────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'OrderStreamArn', { value: orderStream.streamArn });
    new cdk.CfnOutput(this, 'ReturnTableName', { value: returnTable.tableName });
    new cdk.CfnOutput(this, 'ScoreEndpoint', {
      value: `${api.url}mirror/score`,
      description: 'Checkout integration endpoint',
    });
    new cdk.CfnOutput(this, 'InterventionBusArn', { value: interventionBus.eventBusArn });
  }
}
