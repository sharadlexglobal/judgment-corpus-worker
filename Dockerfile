FROM python:3.11-slim

# poppler-utils gives us pdftotext — the fast, reliable text extractor.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends poppler-utils ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker.py substance.py secbind.py orderrule.py .
# The register loaders run from this same image, selected per service by
# dockerCommand. hc_register.py imports canonical/parse_name from loader.py,
# and loader.py needs schema.sql beside it.
COPY loader.py hc_register.py schema.sql .

CMD ["python", "-u", "worker.py"]
