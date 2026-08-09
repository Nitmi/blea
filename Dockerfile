# Registry sandbox image. Use a native host install for access to physical BLE adapters.
FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

ARG BLEA_VERSION=0.6.2
WORKDIR /opt/blea
COPY . .
RUN python -m pip install --no-cache-dir .

LABEL org.opencontainers.image.source="https://github.com/Nitmi/blea"
LABEL org.opencontainers.image.version="${BLEA_VERSION}"
LABEL io.modelcontextprotocol.server.name="io.github.nitmi/blea"
ENTRYPOINT ["ble", "mcp"]
