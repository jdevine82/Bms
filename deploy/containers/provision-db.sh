#!/bin/bash
# bms-db: PostgreSQL 16 with TimescaleDB (milestone 12a). Run inside the container by
# deploy/proxmox/create-site.sh, with the database password on standard input.
# It listens on the private bridge only, and only bms-app may connect (scram-sha-256).
# Safe to run again. ALLOW_PLAIN=1 skips TimescaleDB (a bench only: no retention policy).
set -euo pipefail
: "${APP_IP:?}" "${DB_IP:?}"
ALLOW_PLAIN="${ALLOW_PLAIN:-0}"
PG=16
step() { echo "==> [bms-db] $*"; }
fail() { echo "ERROR: [bms-db] $*" >&2; exit 1; }
IFS= read -r PASSWORD || true
[ "${#PASSWORD}" -ge 20 ] || fail "the database password is missing (it comes on standard input)"
[[ "$PASSWORD" =~ ^[A-Za-z0-9]+$ ]] || fail "the database password must be letters and digits"

step "apt repositories (PostgreSQL, TimescaleDB)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends ca-certificates curl gnupg lsb-release
CODENAME="$(lsb_release -cs)"
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor --yes -o /etc/apt/keyrings/postgresql.gpg
echo "deb [signed-by=/etc/apt/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt $CODENAME-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list
if [ "$ALLOW_PLAIN" != 1 ]; then
    curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey | gpg --dearmor --yes -o /etc/apt/keyrings/timescaledb.gpg
    echo "deb [signed-by=/etc/apt/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/debian/ $CODENAME main" \
        > /etc/apt/sources.list.d/timescaledb.list
fi
apt-get update -q

step "PostgreSQL $PG"
PACKAGES=("postgresql-$PG")
[ "$ALLOW_PLAIN" = 1 ] || PACKAGES+=("timescaledb-2-postgresql-$PG")
apt-get install -y -q --no-install-recommends "${PACKAGES[@]}" \
    || fail "installing ${PACKAGES[*]} failed (is TimescaleDB published for Debian $CODENAME yet? ALLOW_PLAIN=1 on a bench only)"

CONF=/etc/postgresql/$PG/main
step "settings: listen on $DB_IP, only $APP_IP may connect"
cat > "$CONF/conf.d/devine-bms.conf" <<CONFEOF
# Devine BMS (deploy/containers/provision-db.sh)
listen_addresses = 'localhost,$DB_IP'
password_encryption = scram-sha-256
$([ "$ALLOW_PLAIN" = 1 ] || echo "shared_preload_libraries = 'timescaledb'")
timescaledb.telemetry_level = off
CONFEOF
[ "$ALLOW_PLAIN" = 1 ] && sed -i '/timescaledb/d' "$CONF/conf.d/devine-bms.conf"
sed -i '/# devine-bms$/d' "$CONF/pg_hba.conf"
echo "host bms bms $APP_IP/32 scram-sha-256 # devine-bms" >> "$CONF/pg_hba.conf"
systemctl restart "postgresql@$PG-main"

step "database bms"
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bms') THEN CREATE ROLE bms LOGIN; END IF;
END \$\$;
ALTER ROLE bms PASSWORD '$PASSWORD';
SQL
runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname = 'bms'" | grep -q 1 \
    || runuser -u postgres -- createdb -O bms bms
if [ "$ALLOW_PLAIN" != 1 ]; then
    runuser -u postgres -- psql -q -d bms -c "CREATE EXTENSION IF NOT EXISTS timescaledb"
fi
systemctl enable "postgresql@$PG-main" >/dev/null 2>&1 || true
step "done"
