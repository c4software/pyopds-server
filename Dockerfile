FROM python:3.12-slim

WORKDIR /app

COPY routes.py /app/routes.py
COPY server.py /app/server.py
COPY opds.py /app/opds.py
COPY koreader_sync.py /app/koreader_sync.py
COPY opds_to_html.xslt /app/opds_to_html.xslt

ENV LIBRARY_DIR=/books

EXPOSE 8080

CMD ["python", "server.py"]
