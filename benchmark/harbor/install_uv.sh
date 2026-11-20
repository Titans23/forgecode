# Invoked by the frozen Harbor driver, once per task. The binary digests are
# supplied by the driver, outside the writable shared cache.
set -euo pipefail
version=$1 cache=$2 bin=$3
case "$(uname -m)" in
    x86_64) arch=x86_64; digest=$4 ;;
    aarch64|arm64) arch=aarch64; digest=$5 ;;
    *)
        # Keep upstream platform selection on the other Linux architectures.
        curl --proto '=https' --tlsv1.2 --retry 2 --retry-connrefused --connect-timeout 15 --max-time 60 \
            -fLsS "https://astral.sh/uv/$version/install.sh" | UV_UNMANAGED_INSTALL="$(dirname "$bin")" sh
        exit
        ;;
esac

target="uv-$arch-unknown-linux-musl"
cached="$cache/$target-$version-$digest"
work=$(mktemp -d "$(dirname "$bin")/.uv-bootstrap.XXXXXXXX")
pending=''
trap 'rm -rf -- "$work"; if [[ -n "$pending" ]]; then rm -f -- "$pending"; fi' EXIT
valid_binary() { (cd "$work" && printf '%s  uv\n' "$digest" | sha256sum -c --status); }
cache_hit() { [[ -f "$cached" && ! -L "$cached" ]] && cp -- "$cached" "$work/uv" && valid_binary; }

if ! cache_hit; then
    # Serialize cold downloads where flock is available. Publication remains
    # atomic even on filesystems without advisory locks.
    if command -v flock >/dev/null; then
        exec 9<"$cache"
        flock -x 9 || echo 'uv cache lock unavailable; using atomic publication' >&2
    fi
    if ! cache_hit; then
        url="https://github.com/astral-sh/uv/releases/download/$version/$target.tar.gz"
        for attempt in 1 2 3; do
            if http=$(curl --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 120 \
                -fLsS -w '%{http_code}' -o "$work/archive" "$url"); then break; else code=$?; fi
            case "$code:$http" in
                6:*|7:*|18:*|28:*|35:*|52:*|55:*|56:*|22:408|22:429|22:5??) ;;
                *) exit "$code" ;;
            esac
            if [[ "$attempt" == 3 ]]; then exit "$code"; fi
            echo "Retrying uv download ($attempt/2, curl $code, HTTP $http)" >&2
            sleep "$((2 * attempt))"
        done
        rm -f -- "$work/uv"
        tar -xzOf "$work/archive" "$target/uv" > "$work/uv"
        valid_binary || { echo 'Downloaded uv binary SHA-256 mismatch' >&2; exit 1; }
        if pending=$(mktemp "$cache/.uv-publish.XXXXXXXX") && install -m 644 "$work/uv" "$pending" && mv -fT "$pending" "$cached"; then
            pending=''
        else
            echo 'Verified uv is available for this task; shared cache could not be updated' >&2
        fi
    fi
fi
chmod 755 "$work/uv"
mv -fT "$work/uv" "$bin"
