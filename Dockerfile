ARG NODE_IMAGE=node:24-alpine
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${NODE_IMAGE} AS frontend
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STROY_DATA_DIR=/data STATIC_DIR=/web/dist APP_HOST=0.0.0.0 MODEL_MANIFEST=/models/yolo26m/manifest.json
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY scripts/ ./scripts/
COPY models/yolo26m/best.onnx models/yolo26m/manifest.json /models/yolo26m/
COPY --from=frontend /web/dist /web/dist
RUN useradd --uid 10001 --create-home stroy && mkdir -p /data && chown stroy:stroy /data
USER stroy
RUN python -m app.check_model --cpu
EXPOSE 8000
CMD ["python", "-m", "app.serve"]
