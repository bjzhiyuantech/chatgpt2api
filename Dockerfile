FROM node:22-alpine AS web-build

WORKDIR /app/web

COPY web/package.json web/bun.lock ./
RUN npm install

COPY VERSION /app/VERSION
COPY web ./
RUN NEXT_PUBLIC_APP_VERSION="$(cat /app/VERSION)" npm run build


FROM python:3.13-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install nginx
RUN apt-get update && apt-get install -y --no-install-recommends nginx && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY main.py ./
COPY VERSION ./
COPY services ./services
COPY --from=web-build /app/web/out ./web_dist
COPY nginx.conf /etc/nginx/sites-enabled/default
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# Remove default nginx config that conflicts
RUN rm -f /etc/nginx/sites-enabled/default.bak

EXPOSE 80

CMD ["./entrypoint.sh"]
