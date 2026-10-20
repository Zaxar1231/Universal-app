package io.github.zaxar1231.universal;

import android.content.Intent;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * «Поделиться → Universal» из YouTube, TikTok, браузера: ссылка приходит в приложение
 * событием "shared" и попадает в поле ссылок на вкладке «Скачать».
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
        CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        if (text == null) return;
        intent.setAction(Intent.ACTION_MAIN);   // при пересоздании активити ссылка не добавится второй раз
        JSObject data = new JSObject();
        data.put("text", text.toString());
        // retain: если интерфейс ещё грузится, событие дождётся подписчика
        notifyListeners("shared", data, true);
    }
}
