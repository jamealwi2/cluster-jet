# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /usr/src/app

# Copy the requirements file into the container at /usr/src/app
COPY requirements.txt ./

# Install any needed packages specified in requirements.txt
# Using --no-cache-dir to reduce image size
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container at /usr/src/app
COPY main.py .

# Specify the user to run the application (optional, but good practice)
# Create a non-root user for security purposes
RUN useradd --create-home appuser
USER appuser
WORKDIR /home/appuser/app # Change workdir for the new user
COPY --chown=appuser:appuser main.py .
# Note: requirements.txt was already installed system-wide, which is fine.
# If main.py was also copied to /usr/src/app before USER appuser,
# we would need to ensure appuser has access or copy it again to the new WORKDIR.
# For simplicity here, we copy main.py again to the new WORKDIR owned by appuser.

# Define environment variables if necessary (e.g., default values for args)
# ENV NAME="World"

# Run main.py when the container launches
# The arguments will be passed to the entrypoint/cmd during Kubernetes deployment
ENTRYPOINT ["python", "main.py"]

# Example default command (can be overridden in Kubernetes manifest)
# CMD ["--target-cluster-name", "my-eks-b", "--job-image", "busybox"]
