import os
import argparse
import logging
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def launch_job_in_cluster(target_cluster_name: str, job_image: str, job_namespace: str = "default", kubeconfig_base_path: str = "/etc/kubeconfigs"):
    """
    Launches a Kubernetes Job in the specified target cluster.

    Args:
        target_cluster_name: The name of the target EKS cluster (used to find the kubeconfig).
        job_image: The Docker image for the Job.
        job_namespace: The namespace in the target cluster to launch the Job.
        kubeconfig_base_path: The base directory where kubeconfig files are mounted.
    """
    kubeconfig_path = os.path.join(kubeconfig_base_path, target_cluster_name)

    if not os.path.exists(kubeconfig_path):
        logging.error(f"Kubeconfig file not found for cluster '{target_cluster_name}' at {kubeconfig_path}")
        raise FileNotFoundError(f"Kubeconfig file not found for cluster '{target_cluster_name}' at {kubeconfig_path}")

    logging.info(f"Loading kubeconfig from: {kubeconfig_path}")
    try:
        # Load Kubernetes configuration from the specified kubeconfig file
        api_client = config.new_client_from_config(config_file=kubeconfig_path)
        batch_v1 = client.BatchV1Api(api_client)
    except Exception as e:
        logging.error(f"Error loading kubeconfig or creating Kubernetes client: {e}")
        raise

    # Define the Job
    job_name = f"remote-job-{target_cluster_name}-{os.getpid()}-{hash(job_image)[-6:]}"
    logging.info(f"Defining Job: {job_name} with image: {job_image} in namespace: {job_namespace}")

    container = client.V1Container(
        name=f"{job_name}-container",
        image=job_image,
        command=["/bin/sh", "-c"], # Example command
        args=["echo 'Hello from remote job!'; sleep 10; echo 'Remote job finished.'"] # Example args
    )

    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels={"app": "remote-job-runner", "cluster": target_cluster_name}),
        spec=client.V1PodSpec(restart_policy="Never", containers=[container])
    )

    job_spec = client.V1JobSpec(
        template=template,
        backoff_limit=4 # Number of retries before considering a Job as failed
    )

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(name=job_name),
        spec=job_spec
    )

    try:
        logging.info(f"Creating Job '{job_name}' in namespace '{job_namespace}' on cluster '{target_cluster_name}'...")
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

def main():
    parser = argparse.ArgumentParser(description="Launch a Kubernetes Job in a target EKS cluster.")
    parser.add_argument("--target-cluster-name", required=True, help="Name of the target EKS cluster (must match kubeconfig filename).")
    parser.add_argument("--job-image", required=True, help="Docker image for the Job to be run in the target cluster.")
    parser.add_argument("--job-namespace", default="default", help="Namespace in the target cluster to launch the Job (default: default).")
    parser.add_argument("--kubeconfig-base-path", default="/etc/kubeconfigs", help="Base directory where kubeconfig files are mounted (default: /etc/kubeconfigs).")

    args = parser.parse_args()

    try:
        launch_job_in_cluster(
            target_cluster_name=args.target_cluster_name,
            job_image=args.job_image,
            job_namespace=args.job_namespace,
            kubeconfig_base_path=args.kubeconfig_base_path
        )
        logging.info("Job launch process completed.")
    except FileNotFoundError as e:
        logging.error(f"Configuration error: {e}")
        # Specific exit code for config file not found could be useful
        exit(1)
    except ApiException as e:
        logging.error(f"Kubernetes API error: {e.status} - {e.reason}")
        exit(1)
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        exit(1)

if __name__ == "__main__":
    main()
