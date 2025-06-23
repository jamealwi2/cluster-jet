# Deployment Guide: Remote Job Launcher

This guide explains how to build and deploy the Remote Job Launcher application.
This application runs as a Deployment in one Kubernetes cluster (Cluster A) and is designed to launch Kubernetes Jobs in another Kubernetes cluster (Cluster B).

## Prerequisites

1.  **Two Kubernetes Clusters:**
    *   **Cluster A:** Where the Remote Job Launcher will be deployed.
    *   **Cluster B:** The target cluster where Jobs will be launched. You need kubeconfig access to this cluster.
2.  **Docker:** Installed locally to build the Docker image.
3.  **kubectl:** Installed and configured for Cluster A.
4.  **Docker Registry:** A container registry (like Docker Hub, ECR, GCR, ACR) to push the built image.

## Project Structure

```
.
├── app/
│   ├── Dockerfile
│   ├── main.py         # Python application logic
│   └── requirements.txt  # Python dependencies
├── docs/
│   └── DEPLOYMENT_GUIDE.md # This file
├── k8s/
│   └── cluster-a/
│       ├── deployment.yaml
│       ├── rbac.yaml
│       └── serviceaccount.yaml
├── LICENSE
└── README.md
```

## Build Steps

1.  **Navigate to the Application Directory:**
    ```bash
    cd /path/to/your/project/app
    ```

2.  **Build the Docker Image:**
    Replace `YOUR_DOCKER_REGISTRY/remote-job-launcher:latest` with your actual image repository and tag.
    ```bash
    docker build -t YOUR_DOCKER_REGISTRY/remote-job-launcher:latest .
    ```

3.  **Push the Docker Image:**
    ```bash
    docker push YOUR_DOCKER_REGISTRY/remote-job-launcher:latest
    ```

## Deployment Steps (Cluster A)

These steps assume you are deploying to the `default` namespace in Cluster A. Adjust the namespace in the YAML files and `kubectl` commands if using a different one.

1.  **Prepare Kubeconfig for Cluster B:**
    *   You need the kubeconfig file for Cluster B. Let's assume this file is located at `~/.kube/cluster-b.yaml`.
    *   The application expects the kubeconfig to be available inside a mounted secret volume. The filename inside this volume should match the `--target-cluster-name` argument provided to the application.

2.  **Create a Kubernetes Secret in Cluster A for Cluster B's Kubeconfig:**
    Let's say you want to target a cluster you'll refer to as `my-target-cluster`. The `--target-cluster-name` argument to your application will be `my-target-cluster`.
    Create a secret named `target-cluster-kubeconfig` (this name must match `secretName` in `k8s/cluster-a/deployment.yaml` or you'll need to update the deployment manifest).
    The *key* within the secret data must match the `my-target-cluster` name.

    ```bash
    kubectl create secret generic target-cluster-kubeconfig \
      --from-file=my-target-cluster=/path/to/your/cluster-b.yaml \
      -n default # Or your target namespace in Cluster A
    ```
    *   Replace `/path/to/your/cluster-b.yaml` with the actual path to Cluster B's kubeconfig file.
    *   The key in the secret (`my-target-cluster`) will become the filename inside the `/etc/kubeconfigs/` directory in the pod. The application will look for `/etc/kubeconfigs/my-target-cluster`.

3.  **Update Deployment Manifest:**
    Open `k8s/cluster-a/deployment.yaml`:
    *   **Image:** Change `image: YOUR_DOCKER_REGISTRY/remote-job-launcher:latest` to the image you pushed.
    *   **Args:**
        *   Modify `--target-cluster-name` to match the key you used in the secret (e.g., `my-target-cluster`).
        *   Modify `--job-image` to the image you want to run in Cluster B (e.g., `busybox`).
        *   Modify `--job-namespace` to the namespace in Cluster B where the job should run (e.g., `default` or `target-jobs`).
    *   **Secret Name:** Ensure `secretName` under `volumes` matches the name of the secret you created (e.g., `target-cluster-kubeconfig`).

    Example snippet from `deployment.yaml` after changes:
    ```yaml
    # ...
    spec:
      serviceAccountName: remote-job-launcher-sa
      containers:
      - name: launcher
        image: myregistry/myuser/remote-job-launcher:v1.0.0 # YOUR ACTUAL IMAGE
        args:
          - "--target-cluster-name"
          - "my-target-cluster"       # Must match the key in the secret
          - "--job-image"
          - "alpine/git"              # Example job image for Cluster B
          - "--job-namespace"
          - "batch-processing"        # Namespace in Cluster B
        volumeMounts:
        - name: kubeconfigs
          mountPath: "/etc/kubeconfigs"
          readOnly: true
      volumes:
      - name: kubeconfigs
        secret:
          secretName: target-cluster-kubeconfig # Matches the secret created above
    # ...
    ```

4.  **Apply Kubernetes Manifests to Cluster A:**
    Navigate to the root of the project.
    ```bash
    kubectl apply -f k8s/cluster-a/serviceaccount.yaml
    kubectl apply -f k8s/cluster-a/rbac.yaml
    kubectl apply -f k8s/cluster-a/deployment.yaml
    ```

5.  **Verify Deployment:**
    Check the status of the deployment and pods in Cluster A:
    ```bash
    kubectl get deployments -n default
    kubectl get pods -n default -l app=remote-job-launcher
    ```
    Check the logs of the launcher pod:
    ```bash
    kubectl logs -f <pod-name-of-launcher> -n default
    ```
    If everything is configured correctly, the logs should indicate that it's trying to connect to Cluster B and launch the job.

6.  **Verify Job in Cluster B:**
    Switch your `kubectl` context to Cluster B and check if the job was created:
    ```bash
    kubectl get jobs -n <job-namespace-in-cluster-b> # e.g., batch-processing
    kubectl get pods -n <job-namespace-in-cluster-b> # To see the job's pod
    kubectl logs -f <job-pod-name-in-cluster-b> -n <job-namespace-in-cluster-b>
    ```

## Troubleshooting

*   **ImagePullBackOff (Cluster A):** Ensure the Docker image name in `deployment.yaml` is correct and the image is accessible from Cluster A (publicly or with `imagePullSecrets`).
*   **Error loading kubeconfig (Launcher Pod Logs):**
    *   Verify the `secretName` in `deployment.yaml` matches the created secret.
    *   Verify the `--target-cluster-name` arg matches a key within the secret data.
    *   Check RBAC permissions: `kubectl describe role remote-job-launcher-role -n default` and `kubectl describe rolebinding remote-job-launcher-rb -n default`.
    *   Ensure the `fsGroup` is set in `deployment.yaml` under `spec.template.spec.securityContext.fsGroup` if the pod user (`appuser`) cannot read the mounted secret files. The `appuser` has UID `1000` in the provided Dockerfile. `fsGroup: 1000` might be needed.
*   **Connection refused/timeout to Cluster B (Launcher Pod Logs):**
    *   Ensure the kubeconfig for Cluster B is correct and its API server is accessible from Cluster A's pods. Network policies or firewalls might be an issue.
*   **Job creation failed (Launcher Pod Logs):**
    *   The logs should provide details from the Kubernetes API in Cluster B. This could be due to RBAC issues *within Cluster B* (i.e., the identity defined in Cluster B's kubeconfig doesn't have permission to create jobs).
    *   Ensure the specified `--job-namespace` exists in Cluster B or the identity has permission to create it.

## Multiple Target Clusters

If you need to target multiple clusters (e.g., `cluster-b-dev`, `cluster-b-staging`, `cluster-b-prod`):

1.  **Create a separate kubeconfig secret for each target cluster in Cluster A.**
    *   `kubectl create secret generic cluster-b-dev-kubeconfig --from-file=cluster-b-dev=/path/to/dev.config`
    *   `kubectl create secret generic cluster-b-staging-kubeconfig --from-file=cluster-b-staging=/path/to/staging.config`
2.  **Modify the `deployment.yaml` in Cluster A:**
    *   You'll need to decide how the launcher pod will access these.
        *   **Option A (Multiple Volume Mounts):** Mount each secret to a distinct subpath under `/etc/kubeconfigs`. This would require changes to the application logic to select the correct path.
        *   **Option B (One Secret, Multiple Keys):** Create a single secret with multiple keys, each key being a target cluster name.
            ```bash
            kubectl create secret generic all-target-kubeconfigs \
              --from-file=cluster-b-dev=/path/to/dev.config \
              --from-file=cluster-b-staging=/path/to/staging.config
            ```
            Then mount this `all-target-kubeconfigs` secret. The application `main.py` already expects the filename in `/etc/kubeconfigs/` to be the target cluster name, so this approach works well with the current code. The `deployment.yaml` would mount `all-target-kubeconfigs` to `/etc/kubeconfigs`.
    *   The RBAC `Role` in Cluster A would need to be updated to grant access to all these secrets or the single combined secret.
3.  **Run multiple instances of the launcher deployment**, each configured with different arguments to target a specific cluster, or modify the application to loop through targets or accept a list of targets. For simplicity, running separate, uniquely named deployments (or using something like Argo Workflows/Airflow to orchestrate multiple runs with different parameters) is often easier.

This guide provides a comprehensive overview. Adjust paths, names, and configurations to fit your specific environment.
