#!/bin/sh
# Generate datasources.yaml from template so the DB password is not committed.
set -e
DSSRC="/etc/grafana/provisioning/datasources/datasources.yaml.template"
DSOUT="/etc/grafana/provisioning/datasources/datasources.yaml"
if [ -f "$DSSRC" ]; then
  envsubst '${POSTGRES_CEA_USER_PASSWORD}' < "$DSSRC" > "$DSOUT"
fi
exec /run.sh
