FROM python:3.14-alpine

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

RUN mkdir -p /config

# Atrium n installe aucun paquet a l execution : pip ne sert qu a la
# construction de l image, et ses failles connues n ont rien a faire dans ce
# qui tourne chez l utilisateur.
RUN python -m pip uninstall -y pip setuptools 2>/dev/null || true;     rm -rf /usr/local/lib/python*/ensurepip /usr/local/bin/pip*

# Le conteneur demarre en root, le temps de donner /config au compte demande
# et de descendre : les volumes des NAS (Unraid, Synology…) sont montes avec des
# proprietaires varies (99:100, 1000:1000, root…) et un utilisateur fixe grave
# ici rendrait /config non inscriptible selon l hote. Poser PUID et PGID fait
# tourner le serveur sous ce compte ; sans elles, rien ne change.
ENV PUID=""     PGID=""

VOLUME ["/config"]
EXPOSE 8420

# Le commit et le jour de la construction, poses par la chaine de publication.
# Places en fin de fichier : ils changent a chaque commit, et invalideraient
# toutes les couches precedentes s ils etaient plus haut.
ARG ATRIUM_BUILD=""
ARG ATRIUM_BUILD_DATE=""
ENV ATRIUM_BUILD=$ATRIUM_BUILD \
    ATRIUM_BUILD_DATE=$ATRIUM_BUILD_DATE

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8420/api/health',timeout=3).status==200 else 1)"

CMD ["python", "entree.py"]
