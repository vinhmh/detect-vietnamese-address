FROM python:3.12-slim

# postgresql-client provides psql for running migrations
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cached unless requirements change)
COPY etl/requirements.txt etl/requirements.txt
RUN pip install --no-cache-dir -r etl/requirements.txt

# Copy all project source
COPY . .

# Default: start the API server
# Override with the etl command to run the data pipeline
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
