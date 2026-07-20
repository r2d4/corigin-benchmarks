import * as cdk from "aws-cdk-lib";
import { RemovalPolicy, Stack, Tags } from "aws-cdk-lib";
import * as autoscaling from "aws-cdk-lib/aws-autoscaling";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import type { Construct } from "constructs";

const DEFAULT_CLIENT_COUNT = 20;
const DEFAULT_INSTANCE_TYPE = "c7gn.xlarge";

class ThroughputInfraStack extends Stack {
  constructor(scope: Construct, id: string) {
    super(scope, id);

    const clientCount = Number(this.node.tryGetContext("clientCount") ?? DEFAULT_CLIENT_COUNT);
    const instanceTypeName = String(this.node.tryGetContext("instanceType") ?? DEFAULT_INSTANCE_TYPE);

    const runBucket = new s3.Bucket(this, "RunBucket", {
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const vpc = new ec2.Vpc(this, "Vpc", {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          cidrMask: 20,
          mapPublicIpOnLaunch: true,
          name: "public",
          subnetType: ec2.SubnetType.PUBLIC,
        },
      ],
    });

    const clientRole = new iam.Role(this, "ClientRole", {
      assumedBy: new iam.ServicePrincipal("ec2.amazonaws.com"),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonSSMManagedInstanceCore")],
    });
    runBucket.grantReadWrite(clientRole);

    const clientSecurityGroup = new ec2.SecurityGroup(this, "ClientSecurityGroup", {
      allowAllOutbound: true,
      description: "Outbound-only benchmark clients",
      vpc,
    });

    const machineImage = ec2.MachineImage.latestAmazonLinux2023({
      cpuType: ec2.AmazonLinuxCpuType.ARM_64,
    });

    const launchTemplate = new ec2.LaunchTemplate(this, "ClientLaunchTemplate", {
      associatePublicIpAddress: true,
      detailedMonitoring: true,
      instanceType: new ec2.InstanceType(instanceTypeName),
      machineImage,
      requireImdsv2: true,
      role: clientRole,
      securityGroup: clientSecurityGroup,
      userData: clientUserData(runBucket.bucketName),
    });

    const clients = new autoscaling.AutoScalingGroup(this, "Clients", {
      desiredCapacity: clientCount,
      launchTemplate,
      maxCapacity: Math.max(clientCount, 1),
      minCapacity: 0,
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
    });
    Tags.of(clients).add("CoriginBenchmark", "throughput");
  }
}

function clientUserData(runBucketName: string): ec2.UserData {
  const userData = ec2.UserData.forLinux({ shebang: "#!/bin/bash" });
  userData.addCommands(
    "set -euxo pipefail",
    "dnf install -y amazon-ssm-agent",
    "systemctl enable --now amazon-ssm-agent",
    "mkdir -p /opt/corigin-throughput",
    `cat > /opt/corigin-throughput/env <<'ENV'
RUN_BUCKET=${runBucketName}
ENV`,
    "chmod 600 /opt/corigin-throughput/env",
  );
  return userData;
}

const app = new cdk.App();
new ThroughputInfraStack(app, "CoriginThroughputInfraStack");
