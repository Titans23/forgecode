#!/bin/sh
# Cache only the exact public uv 0.9.5 requests made by the official installer.
# All other calls keep the verifier's curl and arguments.
set -eu
curl=${FORGECODE_ORIGINAL_CURL:?}
if [ "$#" = 2 ] && [ "$1" = -LsSf ] && [ "$2" = https://astral.sh/uv/0.9.5/install.sh ]; then
    digest=8402ab80d2ef54d7044a71ea4e4e1e8db3b20c87c7bffbc30bff59f1e80ebbd5
    destination=-
elif [ "$#" = 4 ] && [ "$1" = -sSfL ] && [ "$3" = -o ]; then
    case "$2" in
        https://github.com/astral-sh/uv/releases/download/0.9.5/uv-*-unknown-linux-*.tar.gz) ;;
        *) exec "$curl" "$@" ;;
    esac
    case "${2##*/}" in
        uv-x86_64-unknown-linux-gnu.tar.gz) digest=2cf10babba653310606f8b49876cfb679928669e7ddaa1fb41fb00ce73e64f66 ;;
        uv-x86_64-unknown-linux-musl.tar.gz) digest=3665ffb6c429c31ad6c778ac0489b7746e691acf025cf530b3510b2f9b1660ff ;;
        uv-aarch64-unknown-linux-gnu.tar.gz) digest=9db0c2f6683099f86bfeea47f4134e915f382512278de95b2a0e625957594ff3 ;;
        uv-aarch64-unknown-linux-musl.tar.gz) digest=42b9b83933a289fe9c0e48f4973dee49ce0dfb95e19ea0b525ca0dbca3bce71f ;;
        *) exec "$curl" "$@" ;;
    esac
    destination=$4
else
    exec "$curl" "$@"
fi
cache=${UV_CACHE_DIR:-/opt/forgecode-cache}
cached="$cache/official-uv-$digest"
work=$(mktemp -d)
pending=''
trap 'rm -rf -- "$work"; if [ -n "$pending" ]; then rm -f -- "$pending"; fi' EXIT
valid() { (cd "$work" && printf '%s  asset\n' "$digest" | sha256sum -c --status); }
hit() { [ -f "$cached" ] && [ ! -L "$cached" ] && cp -- "$cached" "$work/asset" && valid; }
download() {
    rm -f -- "$work/asset"
    for attempt in 1 2 3; do
        if http=$("$curl" -fLsS --connect-timeout 15 --max-time 120 -w '%{http_code}' -o "$work/asset" "$1"); then break; else code=$?; fi
        case "$code:$http" in 6:*|7:*|18:*|28:*|35:*|52:*|55:*|56:*|22:408|22:429|22:5??) ;; *) exit "$code";; esac
        [ "$attempt" -lt 3 ] || exit "$code"
        sleep "$attempt"
    done
    valid || { echo 'Official verifier dependency SHA-256 mismatch' >&2; exit 1; }
    if pending=$(mktemp "$cache/.uv-verifier.XXXXXXXX") && install -m 644 "$work/asset" "$pending" && mv -fT "$pending" "$cached"; then
        pending=''
    fi
}
if ! hit; then
    if [ -r "$cache" ] && command -v flock >/dev/null; then
        exec 9<"$cache"
        flock -x 9 || echo 'Verifier cache lock unavailable' >&2
    fi
    hit || download "$2"
fi
if [ "$destination" = - ]; then cat "$work/asset"; else cat "$work/asset" > "$destination"; fi
