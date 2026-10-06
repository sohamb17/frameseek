# FrameSeek ML image: used by both the private retrieval/conversation service
# and the ingestion worker (different commands, same code and pinned deps).
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-eng curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# CPU-only PyTorch keeps the image small enough for an 8 GB laptop.
# For a CUDA machine, build with --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --index-url ${TORCH_INDEX} torch==2.14.1 torchvision==0.29.1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml .
COPY frameseek ./frameseek
COPY tests ./tests
RUN pip install --no-cache-dir --no-deps -e .

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models/hf \
    FRAMESEEK_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "frameseek.service.app:app", "--host", "0.0.0.0", "--port", "8000"]
