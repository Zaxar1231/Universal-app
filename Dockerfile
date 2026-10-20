FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg imagemagick pandoc poppler-utils libreoffice-writer-nogui fonts-dejavu \
        tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV HOST=0.0.0.0 PORT=5000 WORK_DIR=/data UNIVERSAL_DOCKER=1
VOLUME /data
EXPOSE 5000
CMD ["python", "app.py"]
