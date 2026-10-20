#!/usr/bin/env bash
# Собирает дерево /usr для Linux-пакетов и .deb.
#   packaging/linux/build.sh 1.2.0 out/
# Результат: out/root (дерево), out/universal-<v>-linux.tar.gz (для Arch и RPM), out/universal_<v>_all.deb
set -euo pipefail
PY="${PYTHON:-python3}"
V="$1"; OUT="$(mkdir -p "$2" && cd "$2" && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"; SRC="$(cd "$HERE/../.." && pwd)"
ROOT="$OUT/root"; LIB="$ROOT/usr/lib/universal"
rm -rf "$ROOT"; mkdir -p "$LIB" "$ROOT/usr/bin" "$ROOT/usr/share/applications" "$ROOT/usr/share/doc/universal"

# приложение
cp "$SRC"/*.py "$LIB/"
printf 'VERSION = "%s"\nCOMMIT = "%s"\nBUILT = "%s"\n' "$V" "$(git -C "$SRC" rev-parse --short=7 HEAD 2>/dev/null)" "$(date -u +%Y-%m-%dT%H:%MZ)" > "$LIB/version.py"
cp -r "$SRC/templates" "$SRC/static" "$SRC/CHANGELOG.md" "$LIB/"
install -m 755 "$HERE/universal.sh" "$ROOT/usr/bin/universal"
install -m 644 "$HERE/universal.desktop" "$ROOT/usr/share/applications/universal.desktop"
cp "$SRC/LICENSE" "$SRC/README.md" "$ROOT/usr/share/doc/universal/"
for s in 64 128 256 512; do
  d="$ROOT/usr/share/icons/hicolor/${s}x${s}/apps"; mkdir -p "$d"
  "$PY" -c "from PIL import Image; Image.open('$SRC/packaging/icon.png').resize(($s,$s), Image.LANCZOS).save('$d/universal.png')"
done

# чистые зависимости внутрь пакета; бинарные модули (ускорители markupsafe) выкидываем,
# у markupsafe есть запасной путь на чистом Python
"$PY" -m pip install -q --no-deps --no-compile --target "$LIB/vendor" -r "$HERE/vendor.txt"
find "$LIB/vendor" -name "*.so" -path "*markupsafe*" -delete
if find "$LIB/vendor" -name "*.so" | grep -q .; then
  echo "В vendor попал бинарный модуль:"; find "$LIB/vendor" -name "*.so"; exit 1
fi
rm -rf "$LIB/vendor/bin" "$LIB/vendor/share"
find "$ROOT" -name "__pycache__" -prune -exec rm -rf {} +
find "$ROOT" -type d -exec chmod 755 {} +
find "$LIB" -type f -exec chmod 644 {} +

tar -C "$ROOT" -czf "$OUT/universal-$V-linux.tar.gz" usr

# .deb
DEB="$OUT/deb"; rm -rf "$DEB"; cp -a "$ROOT" "$DEB"; mkdir -p "$DEB/DEBIAN"
SIZE=$(du -sk "$DEB/usr" | cut -f1)
sed -e "s/@VERSION@/$V/" -e "s/@SIZE@/$SIZE/" "$HERE/debian-control" > "$DEB/DEBIAN/control"
dpkg-deb --root-owner-group --build "$DEB" "$OUT/universal_${V}_all.deb" >/dev/null
rm -rf "$DEB"
ls -la "$OUT"
