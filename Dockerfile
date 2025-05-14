# Use an official Python runtime as a parent image
FROM python:3.9-slim
# For a slightly newer Python version, you could use python:3.10-slim or python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file first to leverage Docker cache
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# --no-cache-dir: Disables the pip cache, reducing image size.
# --compile: Compiles Python source files to bytecode, can slightly improve startup.
RUN pip install --no-cache-dir --compile -r requirements.txt

# Copy the application code into the container at /app
# This includes your main application file and the agent/blueprint files.
COPY Google_Suite.py .
COPY Google_Sheets_Agent.py .
COPY Google_Docs_Agent.py .
# If you create any shared utility files (e.g., shared_utils.py), copy them too:
# COPY shared_utils.py .

# Expose the port that Gunicorn will listen on.
# Your serverless platform or container orchestrator will map this to an external port/URL.
# The actual port number used by Gunicorn inside the container is often set by the $PORT env var.
EXPOSE 8080

# Define environment variable for the Google Client Secret.
# CRITICAL: This value MUST be provided by your serverless environment or
# container runtime configuration for production. The value here is a placeholder.
ENV GOOGLE_CLIENT_SECRET="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"

# Define environment variable for the port Gunicorn should listen on.
# Many serverless platforms (like Cloud Run, Heroku, etc.) set this automatically.
# Gunicorn will bind to 0.0.0.0:$PORT.
ENV PORT=8080

# Command to run the application using Gunicorn.
# It targets the 'app' Flask instance within your 'Google_Suite.py' file.
# --bind 0.0.0.0:$PORT : Listen on all interfaces, on the port specified by the PORT env var.
# --workers 1 : Common for serverless environments where you scale by instance count. Adjust if needed.
# --threads 4 : Number of threads per worker.
# --timeout 120 : Worker timeout in seconds.
# --access-logfile '-' : Log access to stdout.
# --error-logfile '-' : Log errors to stderr.
# These logs will be picked up by your serverless platform's logging system.
CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "Google_Suite:app"]
