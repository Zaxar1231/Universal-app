#!/bin/sh
# Внутри контейнера archlinux:base-devel: собрать пакет из готового дерева.  build-arch.sh <версия> <папка с tar.gz>
set -e
V="$1"; D="$2"
useradd -m builder 2>/dev/null || true
W=$(mktemp -d); chown builder "$W"
sed "s/@VERSION@/$V/" "$(dirname "$0")/arch/PKGBUILD" > "$W/PKGBUILD"
cp "$D/universal-$V-linux.tar.gz" "$W/"
chown -R builder "$W"
su builder -c "cd $W && makepkg --nodeps --noconfirm >/dev/null"
cp "$W"/universal-*.pkg.tar.zst "$D/"
ls "$D"/*.pkg.tar.zst
