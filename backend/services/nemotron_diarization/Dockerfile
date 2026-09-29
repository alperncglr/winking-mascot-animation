FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

ARG TRANSFORMERS_REF=f324707307757d9c0b8dac1c4462eceff911fa2f

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir \
        "git+https://github.com/huggingface/transformers.git@${TRANSFORMERS_REF}" \
        "websockets>=15,<16" \
        "librosa>=0.11,<0.12" \
        "safetensors>=0.5" \
        "huggingface-hub>=0.34"

WORKDIR /app
COPY nemotron_server.py /app/nemotron_server.py

EXPOSE 2710

CMD ["python", "/app/nemotron_server.py", "--host", "0.0.0.0", "--port", "2710"]
