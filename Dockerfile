FROM python:3.12-slim

WORKDIR /app

COPY routes.py /app/routes.py
COPY server.py /app/server.py
COPY controllers/ /app/controllers/
COPY static/ /app/static/

ENV LIBRARY_DIR=/books

EXPOSE 8080

CMD ["python", "server.py"]
