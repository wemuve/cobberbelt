# PostGIS 16 + pgvector (the stock postgis image lacks pgvector, which migration 0001 requires).
FROM postgis/postgis:16-3.5
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
