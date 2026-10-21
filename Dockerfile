# Web UI stylesheet (static/tailwind.css), built once on the build machine: the output does not
# depend on the target architecture
FROM --platform=$BUILDPLATFORM python:3.12-slim AS css
ENV TAILWIND_CACHE=/opt/tailwindcss
WORKDIR /src
COPY tools/tailwind.py tools/
COPY tailwind.config.js ./
COPY styles/ styles/
COPY static/ static/
RUN python tools/tailwind.py

# Development image (docker-compose.dev.yml): the source is mounted, the server restarts on every
# Python change (watchfiles) and the stylesheet is rebuilt on every change of static/ (Tailwind --watch)
FROM python:3.12-slim AS dev
ENV TAILWIND_CACHE=/opt/tailwindcss \
    PYTHONUNBUFFERED=1 \
    LIBRARY_DIR=/books
RUN pip install --no-cache-dir watchfiles==1.3.0
COPY --from=css /opt/tailwindcss /opt/tailwindcss
WORKDIR /app
EXPOSE 8080
CMD ["watchfiles", "python server.py", "controllers", "routes.py", "server.py", "genres.toml"]

# Production image: Python standard library only
FROM python:3.12-slim

WORKDIR /app

COPY routes.py /app/routes.py
COPY server.py /app/server.py
COPY controllers/ /app/controllers/
COPY static/ /app/static/
COPY --from=css /src/static/tailwind.css /app/static/tailwind.css
COPY genres.toml /app/genres.toml

ENV LIBRARY_DIR=/books

EXPOSE 8080

CMD ["python", "server.py"]
