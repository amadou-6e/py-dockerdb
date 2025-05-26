#!/bin/bash
set -e


# Ensure essential variables are set
if [[ -z "$POSTGRES_USER" || -z "$POSTGRES_PASSWORD" ]]; then
  echo "ERROR: POSTGRES_ROLE and POSTGRES_PASSWORD must be set." >&2
  exit 1
fi

# Run schema + role setup
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL

-- Create table if it doesn't exist
CREATE TABLE IF NOT EXISTS test_table (
    id SERIAL PRIMARY KEY,
    name TEXT
);

INSERT INTO test_table (name) 
VALUES ('$YourEnvVar');


EOSQL
