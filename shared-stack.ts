import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export class SharedStack extends cdk.Stack {
  public readonly dataLake: s3.Bucket;
  public readonly userPool: cognito.UserPool;
  public readonly api: apigateway.RestApi;
  public readonly bedrockRole: iam.Role;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ── Data Lake ──────────────────────────────────────────────────────────
    this.dataLake = new s3.Bucket(this, 'RoosClosetDataLake', {
      bucketName: `rooscloset-data-lake-${this.account}`,
      versioned: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      lifecycleRules: [
        {
          // Raw ingestion → Intelligent-Tiering after 30d
          prefix: 'raw/',
          transitions: [
            {
              storageClass: s3.StorageClass.INTELLIGENT_TIERING,
              transitionAfter: cdk.Duration.days(30),
            },
          ],
        },
        {
          // Model artifacts kept 90d
          prefix: 'models/',
          expiration: cdk.Duration.days(90),
        },
      ],
    });

    // Folder structure enforced via key prefixes:
    // s3://rooscloset-data-lake-{account}/
    //   raw/{tenant_id}/images/
    //   raw/{tenant_id}/metadata/
    //   processed/{tenant_id}/embeddings/
    //   processed/{tenant_id}/attributes/
    //   models/atlas/
    //   models/mirror/
    //   returns/{tenant_id}/events/

    // ── Multi-Tenant Auth ──────────────────────────────────────────────────
    this.userPool = new cognito.UserPool(this, 'RoosClosetUserPool', {
      userPoolName: 'rooscloset-tenants',
      selfSignUpEnabled: false,         // B2B: admin-provisioned tenants only
      signInAliases: { email: true },
      standardAttributes: {
        email: { required: true, mutable: false },
      },
      customAttributes: {
        tenant_id: new cognito.StringAttribute({ mutable: false }),
        plan: new cognito.StringAttribute({ mutable: true }),   // starter|growth|pro
      },
      passwordPolicy: {
        minLength: 16,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const userPoolClient = new cognito.UserPoolClient(this, 'ApiClient', {
      userPool: this.userPool,
      generateSecret: true,
      authFlows: {
        adminUserPassword: true,
        userSrp: true,
      },
      oAuth: {
        flows: { clientCredentials: true },
        scopes: [cognito.OAuthScope.custom('rooscloset/atlas'), cognito.OAuthScope.custom('rooscloset/mirror')],
      },
    });

    // ── API Gateway ────────────────────────────────────────────────────────
    const logGroup = new logs.LogGroup(this, 'ApiLogs', {
      retention: logs.RetentionDays.ONE_MONTH,
    });

    const cognitoAuthorizer = new apigateway.CognitoUserPoolsAuthorizer(
      this,
      'TenantAuthorizer',
      { cognitoUserPools: [this.userPool] }
    );

    this.api = new apigateway.RestApi(this, 'RoosClosetApi', {
      restApiName: 'rooscloset-api',
      description: 'RoosCloset B2B Fashion Intelligence API',
      deployOptions: {
        stageName: 'v1',
        accessLogDestination: new apigateway.LogGroupLogDestination(logGroup),
        accessLogFormat: apigateway.AccessLogFormat.jsonWithStandardFields(),
        tracingEnabled: true,   // X-Ray
        metricsEnabled: true,
        throttlingRateLimit: 100,
        throttlingBurstLimit: 200,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: ['GET', 'POST', 'OPTIONS'],
      },
    });

    // ── Bedrock IAM Role (shared across products) ──────────────────────────
    this.bedrockRole = new iam.Role(this, 'BedrockInvokeRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
      inlinePolicies: {
        BedrockAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                'bedrock:InvokeModel',
                'bedrock:InvokeModelWithResponseStream',
              ],
              resources: [
                // Claude 3 Sonnet — attribute extraction + explanation generation
                `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-3-sonnet-20240229-v1:0`,
                `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-3-haiku-20240307-v1:0`,
              ],
            }),
          ],
        }),
        S3DataLakeAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject', 's3:ListBucket'],
              resources: [
                this.dataLake.bucketArn,
                `${this.dataLake.bucketArn}/*`,
              ],
            }),
          ],
        }),
      },
    });

    // ── Outputs ────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'DataLakeBucket', { value: this.dataLake.bucketName });
    new cdk.CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new cdk.CfnOutput(this, 'ApiEndpoint', { value: this.api.url });
  }
}
