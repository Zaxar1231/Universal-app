#!/usr/bin/env bash
# Проверка полной версии на Android-эмуляторе (CI, .github/workflows/android-e2e.yml):
# ставит APK, запускает приложение, ждёт встроенный сервер, гоняет e2e.py и снимает экран.
set -uo pipefail
APK="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${E2E_OUT:-$PWD/e2e-out}"
PKG=io.github.zaxar1231.universal
mkdir -p "$OUT" "$OUT/host"
export E2E_OUT="$OUT"

adb install -r "$APK" || exit 1
adb logcat -c || true
adb shell am start -n "$PKG/.MainActivity"

echo "Жду встроенный сервер…"
up=0
for i in $(seq 1 100); do
  adb forward tcp:47700 tcp:47700 >/dev/null 2>&1
  if curl -sf -o /dev/null http://127.0.0.1:47700/api/ping; then up=1; echo "Сервер ответил через $((i * 3)) с"; break; fi
  sleep 3
done
adb logcat -d > "$OUT/logcat-start.txt" || true
if [ $up -ne 1 ]; then
  echo "Сервер не поднялся"; grep -iE "python|chaquo|universal|AndroidRuntime" "$OUT/logcat-start.txt" | tail -80
  adb exec-out screencap -p > "$OUT/screen-fail.png"
  exit 1
fi

# файл для проверки скачивания: эмулятор видит компьютер как 10.0.2.2
ffmpeg -y -loglevel error -f lavfi -i testsrc=size=320x240:rate=25:duration=2 -c:v libx264 -pix_fmt yuv420p "$OUT/host/clip.mp4"
python3 -m http.server 8765 --directory "$OUT/host" >/dev/null 2>&1 &
HTTP_PID=$!

python3 "$HERE/e2e.py"; rc=$?

# интерфейс: подключаемся к WebView приложения через DevTools и снимаем вкладки
pid="$(adb shell pidof "$PKG" | tr -d '\r')"
if [ -n "$pid" ]; then
  adb forward tcp:9222 "localabstract:webview_devtools_remote_$pid" >/dev/null 2>&1
  node "$HERE/ui.mjs" "$OUT" || echo "Снимки интерфейса не получились"
fi
adb exec-out screencap -p > "$OUT/screen-last.png" || true
adb logcat -d > "$OUT/logcat.txt" || true
kill $HTTP_PID 2>/dev/null
exit $rc
