# Relay as a public demo on Hugging Face Spaces (Docker Space).
# Visitors never enter a key: set GROQ_API_KEY as a Space secret, ideally from a Groq account
# with no payment method, so the site can never cost money.
FROM python:3.12-slim

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RELAY_PUBLIC=1
WORKDIR /home/user/app

COPY --chown=user . .
RUN pip install --no-cache-dir --user -e ".[web]" pytest

EXPOSE 7860
CMD ["python", "-m", "orchestrator.web", "--host", "0.0.0.0", "--port", "7860", "--no-browser"]
