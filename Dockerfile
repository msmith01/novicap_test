FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY prototype ./prototype

ARG IMAGE_VERSION=dev
ENV IMAGE_VERSION=$IMAGE_VERSION

USER 10001
EXPOSE 8000
CMD ["uvicorn", "prototype.risk_scoring_prototype:app", "--host", "0.0.0.0", "--port", "8000"]
