# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# Dockerfile

FROM python:3.12-alpine

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Set up Python environment and install dependencies
# zope.interface (a gevent dependency) has no musl wheel, so build its pure-python version
RUN python3 -m pip install --upgrade pip && PURE_PYTHON=1 python3 -m pip install --no-cache-dir --only-binary :all: --no-binary zope.interface -r requirements.txt --upgrade

# Cleanup the python requirements file (not needed at runtime)
RUN rm requirements.txt

# Copy only the necessary application files
COPY scrabble/ .

# Persistent data directory (mount a volume here for room/metrics persistence)
ENV SCRABBLE_DATA_DIR=/data
RUN mkdir -p /data

# Write app logs to docker logs immediately
ENV PYTHONUNBUFFERED=1

EXPOSE 1337

# Start gunicorn with a single gevent worker (room state lives in process memory)
CMD ["gunicorn", "--worker-class", "gevent", "--workers", "1", "--worker-connections", "1000", "--graceful-timeout", "5", "--bind", "0.0.0.0:1337", "app:app"]
