#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { SharedStack } from '../lib/shared-stack';
import { AtlasStack } from '../lib/atlas-stack';
import { MirrorStack } from '../lib/mirror-stack';

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? 'us-east-1',
};

// Shared infrastructure — deploy first
const shared = new SharedStack(app, 'RoosCloset-Shared', { env });

// ATLAS: Semantic Catalog Intelligence
const atlas = new AtlasStack(app, 'RoosCloset-ATLAS', { env, shared });
atlas.addDependency(shared);

// MIRROR: Causal Return Intelligence
const mirror = new MirrorStack(app, 'RoosCloset-MIRROR', { env, shared });
mirror.addDependency(shared);

// Tags applied to all resources — useful for AWS cost allocation by product
cdk.Tags.of(app).add('Project', 'RoosCloset');
cdk.Tags.of(atlas).add('Product', 'ATLAS');
cdk.Tags.of(mirror).add('Product', 'MIRROR');

app.synth();
