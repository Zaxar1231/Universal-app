package io.github.zaxar1231.universal;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(ShareInPlugin.class);
        registerPlugin(ApkInstallPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
