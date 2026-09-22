#!/usr/bin/env bash
set -Eeuo pipefail

# This container is a dedicated emulator search host. No vendor table schema is
# installed here; an authenticated Protect controller applies its migrations.
fail() { printf '%s\n' "$*" >&2; exit 1; }

: "${CONSOLE_IP:?Set CONSOLE_IP to the exact controller IPv4 address}"
: "${POSTGRES_PASSWORD_FILE:?Mount a nonempty PostgreSQL password secret file}"
: "${POSTGRES_TLS_CERT_FILE:?Mount a PostgreSQL TLS certificate file}"
: "${POSTGRES_TLS_KEY_FILE:?Mount its TLS private key file}"

validate_ipv4() {
    local address=$1
    local octet
    local -a octets
    case "$address" in
        *[!0-9.]* | .* | *. | *..*) fail "Peer address must be one literal IPv4 address" ;;
    esac
    IFS=. read -r -a octets <<< "$address"
    [[ ${#octets[@]} -eq 4 ]] || fail "Peer address must have four IPv4 octets"
    for octet in "${octets[@]}"; do
        [[ ${#octet} -le 3 ]] || fail "Invalid IPv4 octet"
        [[ ${#octet} -eq 1 || ${octet:0:1} != 0 ]] || fail "IPv4 octets must not have leading zeros"
        (( 10#$octet <= 255 )) || fail "Invalid IPv4 octet"
    done
}
validate_ipv4 "$CONSOLE_IP"
if [[ -n ${EMULATOR_IP:-} ]]; then
    validate_ipv4 "$EMULATOR_IP"
fi

for required_file in "$POSTGRES_PASSWORD_FILE" "$POSTGRES_TLS_CERT_FILE" "$POSTGRES_TLS_KEY_FILE"; do
    [[ -f "$required_file" && -r "$required_file" && -s "$required_file" ]] || fail "A required secret file is missing, empty, or unreadable"
done
[[ $(id -u) -eq 0 ]] || fail "Entrypoint needs root to prepare files; PostgreSQL runs as postgres"
[[ $# -eq 0 || ( $# -eq 1 && ${1:-} == postgres ) ]] || fail "This profile does not accept PostgreSQL command-line overrides"

export POSTGRES_USER=unifi-protect
export POSTGRES_DB=unifi-protect
export POSTGRES_INITDB_ARGS="--auth-host=scram-sha-256 --auth-local=trust"
unset POSTGRES_PASSWORD POSTGRES_HOST_AUTH_METHOD

runtime_dir=/run/aikey-postgres
install -d -m 0750 -o postgres -g postgres "$runtime_dir"
install -m 0644 -o postgres -g postgres "$POSTGRES_TLS_CERT_FILE" "$runtime_dir/server.crt"
install -m 0600 -o postgres -g postgres "$POSTGRES_TLS_KEY_FILE" "$runtime_dir/server.key"

cat > "$runtime_dir/pg_hba.conf" <<EOF
# Unix socket access is restricted by container access. TCP peers are explicit.
local all all trust
hostssl "unifi-protect" "unifi-protect" ${CONSOLE_IP}/32 scram-sha-256
EOF
if [[ -n ${EMULATOR_IP:-} && $EMULATOR_IP != "$CONSOLE_IP" ]]; then
    printf 'hostssl "unifi-protect" "unifi-protect" %s/32 scram-sha-256\n' "$EMULATOR_IP" >> "$runtime_dir/pg_hba.conf"
fi
cat >> "$runtime_dir/pg_hba.conf" <<'EOF'
host all all 0.0.0.0/0 reject
host all all ::/0 reject
EOF
chown postgres:postgres "$runtime_dir/pg_hba.conf"
chmod 0640 "$runtime_dir/pg_hba.conf"

exec /usr/local/bin/docker-entrypoint.sh postgres \
    -c "listen_addresses=*" \
    -c "password_encryption=scram-sha-256" \
    -c "ssl=on" \
    -c "ssl_min_protocol_version=TLSv1.2" \
    -c "ssl_cert_file=$runtime_dir/server.crt" \
    -c "ssl_key_file=$runtime_dir/server.key" \
    -c "hba_file=$runtime_dir/pg_hba.conf"
