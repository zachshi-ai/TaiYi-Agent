FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .

# Default container config: bind all interfaces, persist under /data.
ENV TAIYI_HOST=0.0.0.0 \
    TAIYI_PORT=8080 \
    TAIYI_BASE_DIR=/data
VOLUME ["/data"]
EXPOSE 8080

# `taiyi serve` reads TAIYI_* env (and an optional --config). Override CMD to use
# a mounted config file, e.g.:  ["serve", "--config", "/etc/taiyi/taiyi.yaml"]
ENTRYPOINT ["taiyi"]
CMD ["serve"]
