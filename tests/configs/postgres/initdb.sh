#!/bin/bash
set -e


# Ensure essential variables are set
if [[ -z "$POSTGRES_USER" || -z "$POSTGRES_PASSWORD" ]]; then
  echo "ERROR: POSTGRES_ROLE and POSTGRES_PASSWORD must be set." >&2
  exit 1
fi

# Run schema + role setup
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL

-- Create role if it doesn't exist
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$POSTGRES_USER') THEN
        CREATE ROLE $POSTGRES_USER WITH LOGIN PASSWORD '$POSTGRES_PASSWORD';
    END IF;
END
\$\$;

-- Create table if it doesn't exist
CREATE TABLE IF NOT EXISTS test_table (
    id SERIAL PRIMARY KEY,
    name TEXT
);


EOSQL
