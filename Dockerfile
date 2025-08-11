# Dockerfile
FROM python:3.11-slim

# Avoid buffering logs
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir snscrape requests

WORKDIR /app
COPY ew_link_bot.py .

# Defaults (can be overridden with -e)
ENV POLL_SECONDS=900 \
    X_USER=eWhispers

CMD ["python", "ew_link_bot.py"]
