# The image used when SANDBOX=docker. Build it once with:
#     docker build -t orchestrator-sandbox sandbox
# Containers started from it get no network, a read-only filesystem (except the mounted
# project folder and /tmp), an unprivileged user, and CPU, memory, and process limits;
# those flags are set in src/orchestrator/tools.py, not here.
FROM python:3.13-slim
RUN pip install --no-cache-dir pytest
ENV PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
WORKDIR /work
