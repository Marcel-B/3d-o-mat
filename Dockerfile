# 3d-o-mat als Container (Proxmox LXC mit Docker, wie paperless-gpt und Ohmventory).
# Gerechnet wird nicht hier, sondern auf oi-o-mat über die LLM-Schranke; die Dateien liegen auf der T7 von oi-o-mat
# (per SSH, store/launch.py). Hier laufen nur die Website und das Freistellen.

FROM node:22-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Blender für den FBX-Export (omat3d/fbx.py), das offizielle LTS-Archiv. Eigene Stufe, damit curl und xz nicht im Image
# bleiben; die Schicht ändert sich nur mit BLENDER. Blender ist GPL und läuft als eigener Prozess, 3d-o-mat bleibt MIT.
FROM debian:bookworm-slim AS blender
ARG BLENDER=5.2.4
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends ca-certificates curl xz-utils >/dev/null \
 && curl -fsSL "https://download.blender.org/release/Blender${BLENDER%.*}/blender-${BLENDER}-linux-x64.tar.xz" \
    | tar -xJ -C /opt \
 && mv /opt/blender-${BLENDER}-linux-x64 /opt/blender

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OMAT3D_CONFIG=/config/config.json U2NET_HOME=/cache/rembg
WORKDIR /app
# Die X- und GL-Bibliotheken braucht Blender auch ohne Oberfläche zum Starten.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends openssh-client \
    libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxext6 libxkbcommon0 libsm6 libice6 libgl1 libegl1 \
    >/dev/null && rm -rf /var/lib/apt/lists/*
COPY --from=blender /opt/blender /opt/blender
RUN ln -s /opt/blender/blender /usr/local/bin/blender
COPY requirements.txt requirements-rembg.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-rembg.txt
# Die Bewegungsbibliothek (CMU, Bandai Namco) einmal holen; die Schicht bleibt im Cache, bis motions.py sich ändert.
COPY omat3d/motions.py /tmp/motions.py
RUN python /tmp/motions.py fetch /app/motions-data && rm /tmp/motions.py
COPY omat3d/ omat3d/
COPY store/ store/
COPY --from=frontend /src/dist frontend/dist
EXPOSE 8090
# /ssh: der Schlüssel für oi-o-mat und dessen Host-Schlüssel (known_hosts).
# /cache: die Modelle für rembg (BiRefNet, etwa 1 GB), damit sie nicht bei jedem Neubau neu geladen werden.
VOLUME ["/ssh", "/cache", "/config"]
HEALTHCHECK --interval=60s --timeout=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/status', timeout=8)"
CMD ["python", "-m", "omat3d"]
