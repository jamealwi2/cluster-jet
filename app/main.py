import os
import argparse
import logging
import base64
import datetime
import urllib.parse
import boto3
from botocore.signers import Presigner
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# EKS token constants
EKS_TOKEN_PREFIX = "k8s-aws-v1."
EKS_EXPIRATION_MINUTES = 14 # Tokens are valid for 15 minutes, generate shortly before that

def get_eks_token(cluster_name: str, region: str, role_arn: str = None) -> str:
    """
    Generates a bearer token for EKS authentication using STS presigned URL.
    If role_arn is provided, it attempts to assume that role first.
    """
    sts_client = boto3.client("sts", region_name=region)

    if role_arn:
        logging.info(f"Assuming role: {role_arn} for EKS token generation.")
        try:
            assumed_role_object = sts_client.assume_role(
                RoleArn=role_arn,
                RoleSessionName="EKSJobLauncherSession"
            )
            credentials = assumed_role_object['Credentials']
            sts_client = boto3.client(
                "sts",
                aws_access_key_id=credentials['AccessKeyId'],
                aws_secret_access_key=credentials['SecretAccessKey'],
                aws_session_token=credentials['SessionToken'],
                region_name=region,
            )
            logging.info(f"Successfully assumed role {role_arn}")
        except Exception as e:
            logging.error(f"Error assuming role {role_arn}: {e}")
            raise

    # Create a presigned URL for GetCallerIdentity
    # The Kubernetes authenticator on the EKS control plane will call this URL.
    # The URL itself acts as a bearer token.
    presigner = Presigner(
        sts_client,
        {}, # empty client_config
        os.environ, # Read region from environment if not already set in sts_client
        # We could also pass a custom BOTO_CONFIG_SYSTEM_PATH and BOTO_CONFIG_USER_PATH
        # but default credential chain (IRSA env vars) should work.
    )

    # The token expiration is handled by EKS by setting the X-Amz-Expires query parameter.
    # We request it for slightly less than the max 15 minutes.
    signed_url = presigner.generate_presigned_url(
        "get_caller_identity",
        params={"X-Amz-Algorithm": "AWS4-HMAC-SHA256", # Must be included
                # No need to specify X-Amz-Date, X-Amz-SignedHeaders, X-Amz-Expires, X-Amz-Credential, X-Amz-Signature
                # as generate_presigned_url handles these.
               },
        region_name=region, # Ensure region is passed for presigning
        expires_in=EKS_EXPIRATION_MINUTES * 60,
        operation_name="GetCallerIdentity", # Though not strictly required by EKS for token, it's good practice
    )

    # The token must be base64 encoded, with URL-safe characters, and have the prefix.
    # The presigned URL itself is the token body.
    token = EKS_TOKEN_PREFIX + base64.urlsafe_b64encode(signed_url.encode('utf-8')).decode('utf-8').rstrip("=")
    return token


def get_k8s_api_client_for_eks(cluster_name: str, region: str, target_role_arn: str = None):
    """
    Configures and returns a Kubernetes API client for a target EKS cluster using IRSA.
    Args:
        cluster_name: Name of the target EKS cluster.
        region: AWS region of the target EKS cluster.
        target_role_arn: (Optional) IAM role in the target account to assume for EKS access.
                         If None, uses the pod's current IAM role.
    """
    logging.info(f"Attempting to configure K8s client for EKS cluster: {cluster_name} in region: {region}")

    # If a target_role_arn is specified, the pod's IAM role (from IRSA in Cluster A)
    # must have permission to assume this target_role_arn.
    # The get_eks_token function will handle assuming this role if provided.
    current_pod_role_arn = os.environ.get("AWS_ROLE_ARN") # Injected by IRSA
    if target_role_arn and not current_pod_role_arn:
        logging.warning("target_role_arn specified, but pod is not running with an IAM role (AWS_ROLE_ARN not set). Token generation might fail or use default credentials.")

    # 1. Get EKS cluster endpoint and CA data
    try:
        eks_client = boto3.client("eks", region_name=region)
        if target_role_arn: # If we need to assume a role to talk to EKS API of target cluster
            logging.info(f"Assuming role {target_role_arn} to describe EKS cluster {cluster_name}")
            sts_client_for_eks_describe = boto3.client("sts", region_name=region)
            assumed_role_object = sts_client_for_eks_describe.assume_role(
                RoleArn=target_role_arn,
                RoleSessionName="DescribeEKSSession"
            )
            creds = assumed_role_object['Credentials']
            eks_client = boto3.client(
                "eks",
                aws_access_key_id=creds['AccessKeyId'],
                aws_secret_access_key=creds['SecretAccessKey'],
                aws_session_token=creds['SessionToken'],
                region_name=region
            )
            logging.info(f"Successfully assumed role {target_role_arn} for EKS describe.")

        cluster_info = eks_client.describe_cluster(name=cluster_name)
        endpoint = cluster_info["cluster"]["endpoint"]
        certificate_authority_data = cluster_info["cluster"]["certificateAuthority"]["data"]
        logging.info(f"Successfully described EKS cluster: {cluster_name}. Endpoint: {endpoint}")
    except Exception as e:
        logging.error(f"Error describing EKS cluster {cluster_name} in region {region}: {e}")
        raise

    # 2. Generate EKS bearer token
    # The token should be generated using the credentials of the role that will interact with the K8s API.
    # If target_role_arn is specified, it means ClusterBJobExecutionRole.
    # If not, it means ClusterALauncherRole (the pod's own role).
    # The get_eks_token function handles assuming target_role_arn if it's passed.
    token_generation_role_arn = target_role_arn if target_role_arn else current_pod_role_arn
    if not token_generation_role_arn and target_role_arn: # if target_role_arn was meant but pod has no role
         logging.warning(f"Attempting token generation for EKS cluster {cluster_name} without a specific role, as pod has no role but target_role_arn was {target_role_arn}.")
    elif not token_generation_role_arn and not target_role_arn:
         logging.warning(f"Attempting token generation for EKS cluster {cluster_name} using pod's default credentials/role if any, as no target_role_arn specified and pod may not have an explicit role.")


    token = get_eks_token(cluster_name, region, role_arn=target_role_arn) # Pass target_role_arn to be assumed by STS for token
    logging.info(f"Successfully generated EKS token for cluster: {cluster_name}")

    # 3. Configure Kubernetes client
    k8s_config = client.Configuration()
    k8s_config.host = endpoint
    k8s_config.api_key_prefix['authorization'] = 'Bearer'
    k8s_config.api_key['authorization'] = token

    # SSL CA Cert: boto3 returns it base64 encoded, need to decode
    # and write to a temp file or pass directly if library supports
    k8s_config.ssl_ca_cert = None # Will use default system CAs if not set
                                 # For EKS, we need to use the cluster's CA

    # Create a temporary file for the CA certificate
    # This is safer than disabling SSL verification.
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='utf-8') as ca_cert_file:
        ca_cert_file.write(base64.b64decode(certificate_authority_data).decode('utf-8'))
        k8s_config.ssl_ca_cert = ca_cert_file.name

    logging.info(f"Kubernetes client configured for EKS cluster {cluster_name} using token and CA cert at {k8s_config.ssl_ca_cert}")

    api_client = client.ApiClient(configuration=k8s_config)
    return api_client


def launch_job_in_cluster(
    auth_method: str,
    target_cluster_name: str,
    job_image: str,
    job_namespace: str = "default",
    kubeconfig_base_path: str = "/etc/kubeconfigs", # Used for 'kubeconfig' auth_method
    target_cluster_region: str = None, # Used for 'irsa' auth_method
    target_eks_role_arn: str = None # Optional role in target account to assume for EKS access
):
    """
    Launches a Kubernetes Job in the specified target cluster.
    Uses different auth methods based on arguments.
    """
    api_client = None
    if auth_method == "irsa":
        if not target_cluster_region:
            raise ValueError("target_cluster_region is required for IRSA authentication.")
        api_client = get_k8s_api_client_for_eks(target_cluster_name, target_cluster_region, target_eks_role_arn)
    elif auth_method == "kubeconfig":
        kubeconfig_path = os.path.join(kubeconfig_base_path, target_cluster_name)
        if not os.path.exists(kubeconfig_path):
            logging.error(f"Kubeconfig file not found for cluster '{target_cluster_name}' at {kubeconfig_path}")
            raise FileNotFoundError(f"Kubeconfig file not found for cluster '{target_cluster_name}' at {kubeconfig_path}")
        logging.info(f"Loading kubeconfig from: {kubeconfig_path}")
        try:
            # This creates an ApiClient instance
            api_client = config.new_client_from_config(config_file=kubeconfig_path)
        except Exception as e:
            logging.error(f"Error loading kubeconfig or creating Kubernetes client: {e}")
            raise
    else:
        raise ValueError(f"Unsupported authentication method: {auth_method}")

    if not api_client:
        raise ConnectionError("Failed to initialize Kubernetes API client.")

    # Get BatchV1Api from the configured ApiClient
    batch_v1 = client.BatchV1Api(api_client)

    # Define the Job
    # Using a more robust unique ID for job name
    job_suffix = base64.b32encode(os.urandom(5)).decode('utf-8').lower() # 8 char random string
    job_name = f"remote-job-{target_cluster_name[:20]}-{job_suffix}"
    logging.info(f"Defining Job: {job_name} with image: {job_image} in namespace: {job_namespace}")

    container = client.V1Container(
        name=f"{job_name[:55]}-container", # K8s names have length limits
        image=job_image,
        command=["/bin/sh", "-c"],
        args=["echo 'Hello from remote IRSA-launched job!'; sleep 10; echo 'Remote job finished.'"]
    )

    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels={"app": "remote-job-runner", "cluster": target_cluster_name}),
        spec=client.V1PodSpec(restart_policy="Never", containers=[container])
    )

    job_spec = client.V1JobSpec(
        template=template,
        backoff_limit=2 # Reduced backoff limit for quicker feedback in tests
    )

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(name=job_name),
        spec=job_spec
    )

    ca_cert_to_clean = api_client.configuration.ssl_ca_cert if auth_method == "irsa" else None
    try:
        logging.info(f"Creating Job '{job_name}' in namespace '{job_namespace}' on cluster '{target_cluster_name}' using {auth_method}...")
        api_response = batch_v1.create_namespaced_job(
            body=job,
            namespace=job_namespace
        )
        logging.info(f"Job '{api_response.metadata.name}' created successfully.")
    except ApiException as e:
        logging.error(f"Exception when calling BatchV1Api->create_namespaced_job: {e.status} {e.reason}")
        logging.error(f"Body: {e.body}")
        raise
    except Exception as e:
        logging.error(f"An unexpected error occurred while creating the job: {e}")
        raise
    finally:
        if ca_cert_to_clean and os.path.exists(ca_cert_to_clean):
            try:
                os.remove(ca_cert_to_clean)
                logging.info(f"Cleaned up temporary CA cert: {ca_cert_to_clean}")
            except OSError as e:
                logging.warning(f"Could not remove temporary CA cert {ca_cert_to_clean}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Launch a Kubernetes Job in a target cluster.")
    parser.add_argument("--auth-method", required=True, choices=["irsa", "kubeconfig"], help="Authentication method to use.")
    parser.add_argument("--target-cluster-name", required=True, help="Name of the target cluster.")
    parser.add_argument("--job-image", required=True, help="Docker image for the Job to be run in the target cluster.")
    parser.add_argument("--job-namespace", default="default", help="Namespace in the target cluster to launch the Job.")

    # Args for 'kubeconfig' auth_method
    parser.add_argument("--kubeconfig-base-path", default="/etc/kubeconfigs", help="Base directory for kubeconfig files (if auth-method is kubeconfig).")

    # Args for 'irsa' auth_method
    parser.add_argument("--target-cluster-region", help="AWS region of the target EKS cluster (required for IRSA).")
    parser.add_argument("--target-eks-role-arn", help="(Optional) IAM Role ARN in the target EKS cluster's account to assume for job creation. Requires sts:AssumeRole permission on the launcher's IAM role.")


    args = parser.parse_args()

    if args.auth_method == "irsa" and not args.target_cluster_region:
        parser.error("--target-cluster-region is required when --auth-method is 'irsa'")

    try:
        launch_job_in_cluster(
            auth_method=args.auth_method,
            target_cluster_name=args.target_cluster_name,
            job_image=args.job_image,
            job_namespace=args.job_namespace,
            kubeconfig_base_path=args.kubeconfig_base_path,
            target_cluster_region=args.target_cluster_region,
            target_eks_role_arn=args.target_eks_role_arn
        )
        logging.info("Job launch process completed.")
    except FileNotFoundError as e: # Specifically for kubeconfig not found
        logging.error(f"Configuration error: {e}")
        exit(1)
    except ValueError as e: # For invalid args like missing region for IRSA
        logging.error(f"Configuration error: {e}")
        exit(1)
    except ApiException as e:
        logging.error(f"Kubernetes API error: Status: {e.status} - Reason: {e.reason}")
        if e.body:
             # Limit body length in logs to avoid excessive output
            max_body_len = 500
            body_to_log = e.body[:max_body_len] + ('...' if len(e.body) > max_body_len else '')
            logging.error(f"Body: {body_to_log}")
        exit(1)
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}", exc_info=True) # Add stack trace for unexpected
        exit(1)

if __name__ == "__main__":
    main()
