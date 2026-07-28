FROM python:3.11-slim

# poppler-utils gives us pdftotext — the fast, reliable text extractor.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends poppler-utils ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker.py substance.py secbind.py .

CMD ["python", "-u", "worker.py"]
