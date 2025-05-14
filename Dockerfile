# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file first to leverage Docker cache
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir --compile -r requirements.txt

# Copy the application code into the container at /app
COPY Google_Suite.py .
COPY Google_Sheets_Agent.py .
COPY Google_Docs_Agent.py .

# Expose the port that Gunicorn will listen on (fixed to 8080)
EXPOSE 8080

# Define environment variable for the Google Client Secret.
# CRITICAL: This value MUST be provided by your serverless environment or
# container runtime configuration for production. The value here is a placeholder.
ENV GOOGLE_CLIENT_SECRET="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"

# The PORT environment variable is no longer strictly needed by Gunicorn CMD
# but can be kept if your app.run() in Google_Suite.py uses it for local dev.
# ENV PORT=8080

# Command to run the application using Gunicorn, binding to fixed port 8080.
# Gunicorn targets the 'app' Flask instance within 'Google_Suite.py'.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "Google_Suite:app"]
