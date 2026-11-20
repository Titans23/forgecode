# Configure APT inside a disposable task container. Custom repositories and
# their signing settings stay with the image.
set -eu
apt_dir=${1:-/etc/apt}
system_ca=${2:-/etc/ssl/certs/ca-certificates.crt}
bootstrap_ca=$3
[[ -d "$apt_dir/apt.conf.d" ]] || exit 0
config="$apt_dir/apt.conf.d/05forge-acquire"
printf '%s\n' 'Acquire::http::Timeout "30";' 'Acquire::https::Timeout "30";' > "$config"
retries=2
if [[ ! -s "$system_ca" ]]; then
    install -m 644 "$bootstrap_ca" "$apt_dir/forge-bootstrap-ca.pem"
    printf 'Acquire::https::CaInfo "%s";\n' "$apt_dir/forge-bootstrap-ca.pem" >> "$config"
fi

# Only introduce new list URIs when setup will refresh the indexes anyway.
# Otherwise switching transport on the same hostname preserves preloaded lists.
fallback=false
if [[ -x /usr/lib/apt/methods/mirror+file ]] && { ! command -v git >/dev/null || ! command -v curl >/dev/null; }; then
    fallback=true
fi
for file in "$apt_dir/sources.list" "$apt_dir"/sources.list.d/*.{list,sources}; do
    [[ -f "$file" && ! -L "$file" ]] || continue
    for host in archive.ubuntu.com security.ubuntu.com; do
        escaped=${host//./\\.}
        pattern="(https?://)$escaped/ubuntu/?([[:space:]]|$)"
        grep -Eq "^[[:space:]]*(deb(-src)?[[:space:]]|URIs:).*$pattern" "$file" || continue
        uri="https://$host/ubuntu"
        if "$fallback"; then
            list="$apt_dir/forge-$host.mirrors"
            printf '%s\tpriority:1\n%s\tpriority:2\n' "$uri/" 'https://mirrors.kernel.org/ubuntu/' > "$list"
            uri="mirror+file:$list"
            # APT tries the origin again for each file before trying its next
            # mirror. During an outage those nested retries can consume minutes.
            retries=0
        fi
        sed -Ei "/^[[:space:]]*(deb(-src)?[[:space:]]|URIs:)/s#$pattern#$uri\\2#g" "$file"
    done
done
printf 'Acquire::Retries "%s";\n' "$retries" >> "$config"
