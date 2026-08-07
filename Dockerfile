FROM python:3.12-alpine

LABEL org.opencontainers.image.title="Atrium" \
      org.opencontainers.image.description="Tableau de bord auto-hébergé : services, serveurs et lectures en cours" \
      org.opencontainers.image.source="https://github.com/Alardware/atrium" \
      org.opencontainers.image.licenses="MIT"

# Unraid : icône et bouton « WebUI » dans le menu du conteneur
LABEL net.unraid.docker.icon="https://raw.githubusercontent.com/Alardware/atrium/main/app/static/icon.png" \
      net.unraid.docker.webui="http://[IP]:[PORT:8420]/" \
      net.unraid.docker.managed="dockerman" \
      net.unraid.docker.shell="sh"

ENV ATRIUM_PORT=8420 \
    ATRIUM_CONFIG_DIR=/config \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY app/ /app/

RUN adduser -D -u 1000 atrium && mkdir -p /config && chown -R atrium:atrium /app /config
USER atrium

VOLUME ["/config"]
EXPOSE 8420

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8420/api/health',timeout=3).status==200 else 1)"

CMD ["python", "server.py"]
