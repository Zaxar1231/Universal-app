package io.github.zaxar1231.universal;

import android.content.Context;
import android.os.Environment;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.yausername.ffmpeg.FFmpeg;
import com.yausername.youtubedl_android.YoutubeDL;
import com.yausername.youtubedl_android.YoutubeDLRequest;
import java.io.File;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import kotlin.Unit;

/**
 * Скачивание видео прямо на телефоне: yt-dlp с Python, ffmpeg и QuickJS внутри приложения
 * (библиотека youtubedl-android, на ней же работает YTDLnis). Компьютер не нужен.
 * Файлы ложатся в «Документы/Universal» — ту же папку, что и всё остальное в приложении.
 * Прогресс приходит событием "progress" {id, progress, eta, line}.
 */
@CapacitorPlugin(name = "Ytdl")
public class YtdlPlugin extends Plugin {

    private final ExecutorService pool = Executors.newCachedThreadPool();
    private volatile boolean ready = false;
    private String initError = null;

    private synchronized void ensureInit() throws Exception {
        if (ready) return;
        Context ctx = getContext().getApplicationContext();
        YoutubeDL.getInstance().init(ctx);   // распаковывает Python и yt-dlp при первом запуске (несколько секунд)
        FFmpeg.getInstance().init(ctx);
        ready = true;
    }

    @PluginMethod
    public void init(PluginCall call) {
        pool.execute(() -> {
            try {
                ensureInit();
                JSObject ret = new JSObject();
                ret.put("version", YoutubeDL.getInstance().versionName(getContext()));
                call.resolve(ret);
            } catch (Throwable e) {
                initError = String.valueOf(e.getMessage());
                call.reject("yt-dlp не запустился: " + initError);
            }
        });
    }

    /** Свежий yt-dlp: сайты меняются чаще, чем выходят версии приложения. */
    @PluginMethod
    public void update(PluginCall call) {
        pool.execute(() -> {
            try {
                ensureInit();
                YoutubeDL.UpdateStatus st = YoutubeDL.getInstance().updateYoutubeDL(
                        getContext().getApplicationContext(), YoutubeDL.UpdateChannel._STABLE);
                JSObject ret = new JSObject();
                ret.put("status", st == null ? "" : st.name());
                ret.put("version", YoutubeDL.getInstance().versionName(getContext()));
                call.resolve(ret);
            } catch (Throwable e) {
                call.reject("не обновился: " + e.getMessage());
            }
        });
    }

    private static File outDir() {
        File d = new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS), "Universal");
        d.mkdirs();
        return d;
    }

    private static Map<String, Long> snapshot(File dir) {
        Map<String, Long> m = new HashMap<>();
        File[] list = dir.listFiles();
        if (list != null) for (File f : list) m.put(f.getName(), f.lastModified());
        return m;
    }

    @PluginMethod
    public void download(PluginCall call) {
        String id = call.getString("id", "dl");
        String url = call.getString("url");
        String mode = call.getString("mode", "video");
        String quality = call.getString("quality", "1080");
        String audio = call.getString("audio", "mp3");
        boolean playlist = Boolean.TRUE.equals(call.getBoolean("playlist", false));
        if (url == null || url.isEmpty()) {
            call.reject("нет ссылки");
            return;
        }
        pool.execute(() -> {
            try {
                ensureInit();
                File dir = outDir();
                Map<String, Long> before = snapshot(dir);
                long started = System.currentTimeMillis();
                YoutubeDLRequest req = new YoutubeDLRequest(url);
                req.addOption("-P", dir.getAbsolutePath());
                req.addOption("-o", "%(title).150B [%(id)s].%(ext)s");
                req.addOption("--windows-filenames");      // общая память Android не любит : ? * " < > |
                req.addOption("--no-mtime");               // дата файла — момент скачивания
                req.addOption("--newline");
                if (!playlist) req.addOption("--no-playlist");
                if ("audio".equals(mode)) {
                    req.addOption("-f", "ba/b");
                    req.addOption("-x");
                    req.addOption("--audio-format", audio);
                    if (!"wav".equals(audio)) req.addOption("--audio-quality", "0");
                    req.addOption("--embed-metadata");
                    if ("mp3".equals(audio) || "m4a".equals(audio)) req.addOption("--embed-thumbnail");
                } else {
                    // MP4 с H.264/AAC: открывается в любой галерее и плеере
                    String res = "best".equals(quality) ? "" : "res:" + quality + ",";
                    req.addOption("-S", res + "vcodec:h264,acodec:aac,ext:mp4:m4a");
                    req.addOption("-f", "bv*+ba/b");
                    req.addOption("--merge-output-format", "mp4");
                    req.addOption("--embed-metadata");
                }
                YoutubeDL.getInstance().execute(req, id, (progress, eta, line) -> {
                    JSObject ev = new JSObject();
                    ev.put("id", id);
                    ev.put("progress", progress);
                    ev.put("eta", eta);
                    ev.put("line", line);
                    notifyListeners("progress", ev);
                    return Unit.INSTANCE;
                });
                JSArray files = new JSArray();
                File[] list = dir.listFiles();
                if (list != null) for (File f : list) {
                    String n = f.getName();
                    if (!f.isFile() || f.length() == 0 || n.endsWith(".part") || n.endsWith(".ytdl") || n.contains(".temp.")) continue;
                    Long old = before.get(n);
                    if (old == null || f.lastModified() >= started - 2000 && !old.equals(f.lastModified())) {
                        JSObject o = new JSObject();
                        o.put("name", n);
                        o.put("path", "file://" + f.getAbsolutePath());
                        o.put("size", f.length());
                        files.put(o);
                    }
                }
                JSObject ret = new JSObject();
                ret.put("files", files);
                call.resolve(ret);
            } catch (YoutubeDL.CanceledException e) {
                call.reject("Отменено", "CANCELLED");
            } catch (Throwable e) {
                String msg = String.valueOf(e.getMessage());
                // из stderr yt-dlp показываем последнюю строку с ERROR
                String[] lines = msg.split("\n");
                for (int i = lines.length - 1; i >= 0; i--) {
                    if (lines[i].contains("ERROR")) { msg = lines[i].replace("ERROR: ", "").trim(); break; }
                }
                call.reject(msg.length() > 400 ? msg.substring(0, 400) : msg);
            }
        });
    }

    @PluginMethod
    public void cancel(PluginCall call) {
        String id = call.getString("id", "dl");
        JSObject ret = new JSObject();
        ret.put("cancelled", YoutubeDL.getInstance().destroyProcessById(id));
        call.resolve(ret);
    }
}
