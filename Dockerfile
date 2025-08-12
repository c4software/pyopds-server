FROM python:3.12-slim

WORKDIR /app

COPY server.py /app/server.py

RUN pip install --no-cache-dir ebooklib

ENV LIBRARY_DIR=/books

EXPOSE 8080

CMD ["python", "server.py"]
