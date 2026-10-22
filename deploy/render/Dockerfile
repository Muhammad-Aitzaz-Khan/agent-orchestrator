# Relay on Render (free web service). Created and configured by deploy/deploy.py.
FROM python:3.12-slim

RUN useradd -m -u 1000 user
WORKDIR /home/user/app
COPY --chown=user pyproject.toml README.md ./
COPY --chown=user src ./src
COPY --chown=user evals/tasks ./evals/tasks
USER user
ENV PATH=/home/user/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RELAY_PUBLIC=1
RUN pip install --no-cache-dir --user -e ".[web]" pytest

# Render tells the app which port to use in $PORT.
CMD python -m orchestrator.web --host 0.0.0.0 --port ${PORT:-10000} --no-browser
