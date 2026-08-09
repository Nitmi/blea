# Registry sandbox image. Use a native host install for access to physical BLE adapters.
FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

ARG BLEA_VERSION=0.6.1
RUN python -m pip install --no-cache-dir "blea==${BLEA_VERSION}"

LABEL org.opencontainers.image.source="https://github.com/Nitmi/blea"
ENTRYPOINT ["ble", "mcp"]
