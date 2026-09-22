FROM pgvector/pgvector:0.8.6-pg14-bookworm

COPY entrypoint.sh /usr/local/bin/aikey-postgres-entrypoint.sh
COPY 10-extensions.sql /docker-entrypoint-initdb.d/10-extensions.sql
RUN chmod 0755 /usr/local/bin/aikey-postgres-entrypoint.sh

ENTRYPOINT ["aikey-postgres-entrypoint.sh"]
