# Universal для Android и iPhone

Capacitor 8: интерфейс на HTML, как и у настольной версии, внутри нативного приложения.

## Что внутри
| Вкладка | Как работает |
|---|---|
| Скачать | ссылки → свой Universal (`/api/dl`, качает yt-dlp), потом файлы скачиваются на телефон |
| Инструменты | QR, картинки, PDF из `site/index.html`, всё на телефоне |
| Файлы | `Документы/Universal` (Android) или «Файлы → На iPhone → Universal» |
| Сервер | адрес и пароль своего Universal, вход в полную версию во встроенном браузере |

yt-dlp и ffmpeg на телефоне не запускаются (на iPhone это запрещено). Поэтому тяжёлые вещи делает свой сервер: Docker-версия или `HOST=0.0.0.0 APP_PASSWORD=… ./run.sh` на компьютере.

## Сборка
```bash
cd mobile
npm ci
node build-www.mjs        # www/ из src/ и site/
npx cap sync              # копирует www/ в android/ и ios/
npx cap open android      # Android Studio (JDK 21)
npx cap open ios          # Xcode (только macOS)
```
Иконки и заставки: `python icons.py` (из `packaging/icon.png`).

Без Android Studio: `cd android && ./gradlew assembleRelease`, APK появится в `app/build/outputs/apk/release/`.

## Подпись
- **Android.** APK подписан ключом `android/app/universal.jks` из репозитория. С одним и тем же ключом новые версии ставятся поверх старых. Ключ публичный, поэтому подписать APK им может кто угодно; ставь APK только из релизов этого репозитория. Свой секретный ключ:
  - добавь секреты репозитория `ANDROID_KEYSTORE_BASE64` (`base64 -w0 my.jks`), `ANDROID_KEYSTORE_PASSWORD` и, если нужно, `ANDROID_KEY_ALIAS`;
  - после смены ключа приложение придётся один раз удалить и поставить заново.
- **iPhone.** Без аккаунта разработчика Apple ($99 в год) подписать приложение в CI нельзя, поэтому в релизе лежит `*-ios-unsigned.ipa`. Ставится через AltStore или Sideloadly: они подписывают его твоим Apple ID на 7 дней и продлевают.
