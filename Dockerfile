FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && pip check
COPY . /app
USER 1000:1000
WORKDIR /data
CMD ["python", "-u", "/app/launch.py"]
