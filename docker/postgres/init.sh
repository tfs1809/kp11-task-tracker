#!/bin/sh
set -eu

# Запускается только на пустом named volume. Не выводить SQL/секреты через set -x.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 \
  --set app_user="$DB_APP_USER" --set app_password="$DB_APP_PASSWORD" \
  --set migrator_user="$DB_MIGRATOR_USER" --set migrator_password="$DB_MIGRATOR_PASSWORD" \
  --set database="$POSTGRES_DB" <<'SQL'
CREATE ROLE :"app_user" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
CREATE ROLE :"migrator_user" LOGIN NOSUPERUSER CREATEDB NOCREATEROLE PASSWORD :'migrator_password';
ALTER DATABASE :"database" OWNER TO :"migrator_user";
REVOKE ALL ON DATABASE :"database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database" TO :"app_user";
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO :"migrator_user";
GRANT USAGE ON SCHEMA public TO :"app_user";
GRANT :"app_user" TO :"migrator_user";
SQL
