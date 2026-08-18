ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3

ENV ATRIUM_PORT=8420 \
    ATRIUM_CONFIG_DIR=/config \
    PYTHONUNBUFFERED=1

COPY app/ /opt/atrium/
WORKDIR /opt/atrium

ARG ATRIUM_BUILD=""
ARG ATRIUM_BUILD_DATE=""
ENV ATRIUM_BUILD=$ATRIUM_BUILD \
    ATRIUM_BUILD_DATE=$ATRIUM_BUILD_DATE

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s   CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8420/api/health',timeout=3).status==200 else 1)"

CMD ["python3", "server.py"]
