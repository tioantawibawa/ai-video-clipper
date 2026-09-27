FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt pyproject.toml ./
COPY src ./src
RUN pip install . && useradd --uid 10001 --create-home clipper \
    && mkdir /app/data && chown clipper:clipper /app/data
USER clipper
CMD ["python", "-m", "clipper", "daemon"]
