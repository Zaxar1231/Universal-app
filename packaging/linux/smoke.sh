#!/bin/sh
# Самопроверка установленного пакета внутри контейнера: universal --smoke + движки из зависимостей
set -e
export UNIVERSAL_HOME=/tmp/universal-smoke
universal --smoke && rc=0 || rc=$?
cat "$UNIVERSAL_HOME/smoke.json"; echo
for t in ffmpeg ffprobe pandoc pdftoppm; do command -v "$t" >/dev/null || { echo "нет $t"; exit 1; }; done
command -v magick >/dev/null || command -v convert >/dev/null || { echo "нет ImageMagick"; exit 1; }
test -f /usr/share/applications/universal.desktop
exit $rc
