package io.github.zaxar1231.universal;

import android.content.Context;
import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.yausername.ffmpeg.FFmpeg;
import com.yausername.youtubedl_android.YoutubeDL;
import java.io.File;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Полная версия Universal прямо на телефоне: встроенный Python (Chaquopy) запускает тот же сервер,
 * что и на компьютере, на 127.0.0.1. ffmpeg и ffprobe — из youtubedl-android.
 */
@CapacitorPlugin(name = "PyServer")
public class PyServerPlugin extends Plugin {

    private final ExecutorService pool = Executors.newSingleThreadExecutor();
    private static volatile String url = null;

    @PluginMethod
    public void start(PluginCall call) {
        if (url != null) {
            JSObject ret = new JSObject();
            ret.put("url", url);
            call.resolve(ret);
            return;
        }
        pool.execute(() -> {
            try {
                Context ctx = getContext().getApplicationContext();
                // распаковать ffmpeg (и Python yt-dlp) — сервер зовёт ffmpeg отдельным процессом
                YoutubeDL.getInstance().init(ctx);
                FFmpeg.getInstance().init(ctx);
                if (!Python.isStarted()) Python.start(new AndroidPlatform(ctx));
                File pkgs = new File(ctx.getNoBackupFilesDir(), "youtubedl-android/packages");
                PyObject r = Python.getInstance().getModule("android_main").callAttr("start",
                        ctx.getApplicationInfo().nativeLibraryDir,
                        new File(pkgs, "ffmpeg/usr/lib").getAbsolutePath(),
                        new File(pkgs, "python/usr/lib").getAbsolutePath(),
                        ctx.getFilesDir().getAbsolutePath(),
                        ctx.getCacheDir().getAbsolutePath());
                url = r.toString();
                JSObject ret = new JSObject();
                ret.put("url", url);
                call.resolve(ret);
            } catch (Throwable e) {
                call.reject("Полная версия не запустилась: " + e);
            }
        });
    }
}
