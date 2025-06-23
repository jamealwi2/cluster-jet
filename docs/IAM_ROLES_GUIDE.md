# IAM Roles Configuration Guide for Remote Job Launcher (IRSA)

This document outlines the AWS IAM roles and policies required to use the Remote Job Launcher with IRSA (IAM Roles for Service Accounts) for authenticating to target EKS clusters.

## Overview

Two main IAM roles are involved:

1.  **`ClusterALauncherRole` (in Cluster A's AWS Account):**
    *   This role is assumed by the Service Account (`remote-job-launcher-sa`) of the launcher application running in Cluster A.
    *   It needs permissions to:
        *   Describe the target EKS cluster(s) (in Cluster B's AWS Account).
        *   Assume the `ClusterBJobExecutionRole` (if Cluster B is in a different AWS account or specific role assumption is desired for EKS interaction).

2.  **`ClusterBJobExecutionRole` (in Cluster B's AWS Account):**
    *   This role is assumed by `ClusterALauncherRole`.
    *   It needs to be mapped in Cluster B's `aws-auth` ConfigMap to a Kubernetes user/group.
    *   This Kubernetes user/group, in turn, requires RBAC permissions within Cluster B to create Jobs.

## 1. `ClusterALauncherRole` (Cluster A's AWS Account)

This role is associated with the `remote-job-launcher-sa` ServiceAccount in Cluster A via IRSA.

**A. Trust Policy:**

The trust policy allows the Kubernetes Service Account to assume this IAM role.

*   Replace `CLUSTER_A_AWS_ACCOUNT_ID` with the AWS Account ID where Cluster A resides.
*   Replace `CLUSTER_A_OIDC_PROVIDER_ID` with the OIDC provider ID of Cluster A (e.g., `oidc.eks.us-west-2.amazonaws.com/id/EXAMPLED539D4633E53BF441C177A1`). You can find this in the EKS console for Cluster A or via `aws eks describe-cluster --name <cluster-a-name> --query "cluster.identity.oidc.issuer" --output text | sed 's|https://||'`.
*   Replace `NAMESPACE` with the Kubernetes namespace where `remote-job-launcher-sa` is deployed (e.g., `default`).
*   Replace `SERVICE_ACCOUNT_NAME` with `remote-job-launcher-sa`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::CLUSTER_A_AWS_ACCOUNT_ID:oidc-provider/CLUSTER_A_OIDC_PROVIDER_ID"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "CLUSTER_A_OIDC_PROVIDER_ID:sub": "system:serviceaccount:NAMESPACE:SERVICE_ACCOUNT_NAME"
        }
      }
    }
  ]
}
```

**B. Permissions Policy:**

This policy grants the necessary permissions to `ClusterALauncherRole`.

*   Replace `CLUSTER_B_AWS_ACCOUNT_ID` with the AWS Account ID where Cluster B resides.
*   Replace `TARGET_EKS_CLUSTER_NAME` with the name of the EKS cluster in Cluster B.
*   Replace `TARGET_EKS_CLUSTER_REGION` with the AWS region of Cluster B.
*   The `sts:AssumeRole` permission is only needed if you are using the `--target-eks-role-arn` feature to assume `ClusterBJobExecutionRole`. If Cluster A and B are in the same account AND you are not using `--target-eks-role-arn` (meaning `ClusterALauncherRole` itself will be mapped in Cluster B's `aws-auth`), then this specific `sts:AssumeRole` statement can be omitted or restricted.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "eks:DescribeCluster",
      "Resource": "arn:aws:eks:TARGET_EKS_CLUSTER_REGION:CLUSTER_B_AWS_ACCOUNT_ID:cluster/TARGET_EKS_CLUSTER_NAME"
    }
    // Add this statement if ClusterALauncherRole needs to assume ClusterBJobExecutionRole
    // (e.g., for cross-account access or more granular permissions in Cluster B)
    // {
    //   "Effect": "Allow",
    //   "Action": "sts:AssumeRole",
    //   "Resource": "arn:aws:iam::CLUSTER_B_AWS_ACCOUNT_ID:role/ClusterBJobExecutionRole"
    // }
  ]
}
```
*Note: If targeting multiple EKS clusters, you'll need to add `eks:DescribeCluster` permissions for each, or use wildcards if appropriate (e.g., `arn:aws:eks:*:*:cluster/*` - use with caution).*

## 2. `ClusterBJobExecutionRole` (Cluster B's AWS Account)

This role is assumed by `ClusterALauncherRole` (from Cluster A's account) to interact with Cluster B's EKS API and subsequently create Kubernetes jobs. This role is then mapped to a Kubernetes user/group within Cluster B via the `aws-auth` ConfigMap.

**A. Trust Policy:**

Allows `ClusterALauncherRole` from Cluster A's account to assume this role.

*   Replace `CLUSTER_A_AWS_ACCOUNT_ID` with the AWS Account ID where Cluster A (and `ClusterALauncherRole`) resides.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::CLUSTER_A_AWS_ACCOUNT_ID:role/ClusterALauncherRole"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```
*Note: If `ClusterALauncherRole` itself is being used to authenticate to Cluster B (i.e., it's directly mapped in `aws-auth` and no `--target-eks-role-arn` is used), then `ClusterBJobExecutionRole` might not be strictly necessary. However, for cross-account access or cleaner separation of concerns, using this second role is recommended.*

**B. Permissions Policy:**

This IAM role (`ClusterBJobExecutionRole`) does **not** directly need AWS permissions to interact with the Kubernetes API of Cluster B. Its ARN is used in the `aws-auth` ConfigMap of Cluster B to map it to a Kubernetes user. The Kubernetes permissions (e.g., to create jobs) are then granted to that Kubernetes user via Kubernetes RBAC (Roles/RoleBindings) within Cluster B.

Therefore, this role typically has **no IAM permission policies attached to it directly** for Kubernetes actions. Its sole purpose here is to be an identity that Cluster A can assume, which is then recognized by Cluster B's EKS authentication system.

## Summary of Setup Flow:

1.  **Create `ClusterBJobExecutionRole` in Cluster B's AWS Account:**
    *   Define its trust policy to allow assumption by `ClusterALauncherRole`.
    *   No specific AWS permission policy needed for K8s actions.
2.  **Create `ClusterALauncherRole` in Cluster A's AWS Account:**
    *   Define its trust policy for the Service Account in Cluster A.
    *   Attach permissions policy:
        *   `eks:DescribeCluster` for the target EKS cluster(s).
        *   `sts:AssumeRole` to assume `ClusterBJobExecutionRole` (if used).
3.  **Annotate Service Account in Cluster A:**
    *   In `k8s/cluster-a/serviceaccount.yaml`, set the `eks.amazonaws.com/role-arn` annotation to the ARN of `ClusterALauncherRole`.
4.  **Configure `aws-auth` ConfigMap in Cluster B:**
    *   Map the ARN of `ClusterBJobExecutionRole` (or `ClusterALauncherRole` if not using an intermediate role) to a Kubernetes username/group. (See `docs/DEPLOYMENT_GUIDE.md` or next steps in plan for details).
5.  **Configure Kubernetes RBAC in Cluster B:**
    *   Create Kubernetes `Role` and `RoleBinding` in Cluster B to grant the mapped Kubernetes username/group permissions to create jobs. (See `docs/DEPLOYMENT_GUIDE.md` or next steps in plan for details).

This setup enables secure, auditable, and token-refresh-safe authentication from the launcher in Cluster A to EKS clusters in Cluster B, including cross-account scenarios. Remember to replace all placeholder values with your actual resource names and IDs.
