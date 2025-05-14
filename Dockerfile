# Use an official Python runtime as a parent image
FROM python:3.9-slim
LABEL maintainer="your-name-or-email@example.com"
LABEL description="Google Suite Agent for Sheets and Docs."

# Set environment variables to make Python print out everything immediately
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file first to leverage Docker layer caching
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# Using a virtual environment within the Docker image is good practice,
# but for simplicity here, we'll install globally within this stage.
# If you had build-time dependencies, you might use a multi-stage build.
RUN pip install --no-cache-dir --compile -r requirements.txt

# Copy the application code into the container at /app
COPY Google_Suite.py .
COPY Google_Sheets_Agent.py .
COPY Google_Docs_Agent.py .
COPY shared_utils.py .
COPY Google_Drive_Agent.py .
COPY Google_Slides_Agent.py .
#COPY Google_Calendar_Agent.py .
#COPY Gmail_Agent.py .
# If you have any other shared utility files or directories, copy them here:
# Example: COPY shared_utils.py .

# Expose the port that Gunicorn will listen on inside the container.
# This informs Docker that the containerized application uses this port.
# Your deployment platform (Airev) will need to map external traffic to this port.
EXPOSE 8080

# Define environment variable for the Google Client Secret.
# CRITICAL: This value MUST be provided by your deployment environment
# (e.g., Airev service configuration) for production.
# The value here is a placeholder and should ideally be overridden.
ENV GOOGLE_CLIENT_SECRET="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"

# The PORT environment variable for Gunicorn in CMD is NOT used here;
# Gunicorn binds to a fixed port (8080).
# This ENV PORT could still be used by your app.run() in Google_Suite.py
# if __name__ == "__main__" block for local development, but it's optional.
# ENV PORT=8080

# Command to run the application using Gunicorn.
# Gunicorn binds to a fixed port (0.0.0.0:8080).
# It targets the 'app' Flask instance within your 'Google_Suite.py' file.
# --workers 1: Suitable for many serverless/containerized environments where scaling is by instance.
# --threads 4: Adjust based on your application's nature (I/O bound vs CPU bound).
# --timeout 120: Worker timeout in seconds.
# --access-logfile '-': Log Gunicorn access logs to stdout.
# --error-logfile '-': Log Gunicorn error logs to stderr.
# These logs will typically be collected by your deployment platform.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "Google_Suite:app"]
