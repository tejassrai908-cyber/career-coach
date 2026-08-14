FROM python:3.11-slim

# Tesseract = the program that reads text out of screenshots.
# This is why we use Docker on Render: plain Python runtime cannot install it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng libheif-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 10000
CMD gunicorn -b 0.0.0.0:$PORT --timeout 120 app:app
