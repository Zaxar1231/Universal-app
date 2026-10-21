package io.github.zaxar1231.universal;

import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;
import com.getcapacitor.JSObject;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import com.getcapacitor.Plugin;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * «Поделиться → Universal» из YouTube, TikTok, браузера: ссылка приходит в приложение
 * событием "shared" и попадает в поле ссылок на вкладке «Скачать».
 * Голосовое или видео (Telegram, WhatsApp) копируется в кэш приложения, и событие
 * несёт путь к копии: из него получится текст («Голос в текст»).
 */
@CapacitorPlugin(name = "ShareIn")
public class ShareInPlugin extends Plugin {

    @Override
    public void load() {
        take(getActivity().getIntent());
    }

    @Override
    protected void handleOnNewIntent(Intent intent) {
        super.handleOnNewIntent(intent);
        take(intent);
    }

    private void take(Intent intent) {
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) return;
        Uri stream = intent.getParcelableExtra(Intent.EXTRA_STREAM);
        if (stream != null) {
            intent.setAction(Intent.ACTION_MAIN);
            JSObject data = copyToCache(stream, intent.getType());
            if (data != null) notifyListeners("shared", data, true);
            return;
        }
        CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        if (text == null) return;
        intent.setAction(Intent.ACTION_MAIN);   // при пересоздании активити ссылка не добавится второй раз
        JSObject data = new JSObject();
        data.put("text", text.toString());
        // retain: если интерфейс ещё грузится, событие дождётся подписчика
        notifyListeners("shared", data, true);
    }

    private JSObject copyToCache(Uri uri, String mime) {
        String name = "voice.ogg";
        try (Cursor c = getContext().getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst() && c.getString(0) != null) name = c.getString(0);
        } catch (Exception ignored) {
            // имя не важно
        }
        name = name.replaceAll("[\\\\/:*?\"<>|]", "_");
        File dir = new File(getContext().getCacheDir(), "shared");
        dir.mkdirs();
        File out = new File(dir, name);
        try (InputStream in = getContext().getContentResolver().openInputStream(uri);
             OutputStream os = new FileOutputStream(out)) {
            if (in == null) return null;
            byte[] buf = new byte[65536];
            int n;
            long total = 0;
            while ((n = in.read(buf)) > 0) {
                total += n;
                if (total > 200L * 1024 * 1024) return null;     // больше 200 МБ не таскаем через WebView
                os.write(buf, 0, n);
            }
        } catch (Exception e) {
            return null;
        }
        JSObject data = new JSObject();
        data.put("file", Uri.fromFile(out).toString());
        data.put("name", name);
        data.put("mime", mime == null ? "" : mime);
        return data;
    }
}
