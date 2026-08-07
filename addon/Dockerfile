ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3

ENV ATRIUM_PORT=8420 \
    ATRIUM_CONFIG_DIR=/config \
    PYTHONUNBUFFERED=1

COPY app/ /opt/atrium/
WORKDIR /opt/atrium

CMD ["python3", "server.py"]
