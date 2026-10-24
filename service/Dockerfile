FROM python:3.12-slim

ARG INSTALL_MODEL=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace/service

COPY service/requirements.txt /tmp/requirements.txt
COPY service/requirements-model.txt /tmp/requirements-model.txt
RUN pip install -r /tmp/requirements.txt \
    && if [ "$INSTALL_MODEL" = "true" ]; then \
         pip install -r /tmp/requirements-model.txt; \
       fi

COPY service /workspace/service
COPY config /workspace/config
COPY scripts /workspace/scripts
COPY mt5 /workspace/mt5
COPY docs/izi_manual.pdf /workspace/docs/izi_manual.pdf

RUN mkdir -p /data/screenshots
