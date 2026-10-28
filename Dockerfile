# 3d-o-mat als Container (Proxmox LXC mit Docker, wie paperless-gpt und Ohmventory).
# Gerechnet wird nicht hier, sondern auf oi-o-mat über die LLM-Schranke; hier laufen nur die Website und das Freistellen.

FROM node:22-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OMAT3D_CONFIG=/config/config.json U2NET_HOME=/cache/rembg
WORKDIR /app
COPY requirements.txt requirements-rembg.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-rembg.txt
COPY omat3d/ omat3d/
COPY --from=frontend /src/dist frontend/dist
EXPOSE 8090
# /data: der Ordner auf der T7 von oi-o-mat, auf dem Proxmox-Host per SMB eingebunden und durchgereicht.
# /cache: die Modelle für rembg (BiRefNet, etwa 1 GB), damit sie nicht bei jedem Neubau neu geladen werden.
VOLUME ["/data", "/cache", "/config"]
HEALTHCHECK --interval=60s --timeout=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/status', timeout=8)"
CMD ["python", "-m", "omat3d"]
