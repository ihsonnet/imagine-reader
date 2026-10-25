# One image runs both the API and the worker; docker-compose.yml picks the command.

# Build the reader UI.
FROM node:22-slim AS reader
WORKDIR /src/reader
COPY reader/package.json reader/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY reader/ ./
RUN npm run build

# The engine, the server, and the built reader.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    IR_DATA_DIR=/data \
    IR_READER_DIST=/app/reader/dist
WORKDIR /app
COPY engine/ engine/
COPY server/ server/
RUN pip install ./engine ./server
COPY --from=reader /src/reader/dist reader/dist

# Run unprivileged; books, audio, and pictures live only in the /data volume.
RUN useradd --create-home --uid 10001 reader && mkdir /data && chown reader /data
USER reader
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "--factory", "imagine_reader_server.api:create_app", "--host", "0.0.0.0", "--port", "8000"]
