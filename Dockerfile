FROM node:20-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/vite.config.ts ./frontend/
COPY frontend/index.html ./frontend/
COPY frontend/src ./frontend/src

WORKDIR /frontend/frontend
RUN npm ci
RUN npm run build

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV LOG_LEVEL=WARNING

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend-build /frontend/frontend/dist ./frontend/dist

EXPOSE 4041

CMD ["gunicorn", "-c", "gunicorn_conf.py", "main:app"]
