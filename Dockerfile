FROM python:3.12-slim

WORKDIR /app

COPY server.py /app/server.py
COPY opds_to_html.xslt /app/opds_to_html.xslt

ENV LIBRARY_DIR=/books

EXPOSE 8080

CMD ["python", "server.py"]
