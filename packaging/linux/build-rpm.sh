#!/bin/sh
# Внутри контейнера Fedora: собрать .rpm из готового дерева.  build-rpm.sh <версия> <папка с tar.gz>
set -e
V="$1"; D="$2"
dnf install -y -q rpm-build >/dev/null
rpmbuild -bb --define "_sourcedir $D" --define "_rpmdir $D" --define "ver $V" "$(dirname "$0")/rpm/universal.spec" >/dev/null
mv "$D"/noarch/*.rpm "$D"/ && rmdir "$D/noarch"
ls "$D"/*.rpm
