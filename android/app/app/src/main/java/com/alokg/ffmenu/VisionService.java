package com.alokg.ffmenu;

import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.res.Configuration;
import android.media.projection.MediaProjectionManager;
import android.os.IBinder;

/**
 * Keeps the screen capture alive while the panel is in the background.
 *
 * <p>Required, not optional: since Android 14 a MediaProjection is torn down
 * when the app that owns it leaves the foreground unless a foreground service
 * of type `mediaProjection` is running. The panel is backgrounded the instant
 * Free Fire comes up, so without this the capture would end exactly when it
 * starts being useful.
 *
 * <p>The projection itself is not Parcelable, so what travels in the Intent is
 * the grant - result code plus data - and the service turns that into a
 * projection the way the Activity would have.
 */
public final class VisionService extends Service {

    public static final String EXTRA_RESULT_CODE = "result_code";
    public static final String EXTRA_DATA = "data";
    public static final String CHANNEL_ID = "vision";
    private static final int NOTIFICATION_ID = 0x76697331;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        startForeground(NOTIFICATION_ID, notification());

        if (intent == null) {
            stopSelf();
            return START_NOT_STICKY;
        }
        int result = intent.getIntExtra(EXTRA_RESULT_CODE, Activity.RESULT_CANCELED);
        Intent data = intent.getParcelableExtra(EXTRA_DATA);

        MediaProjectionManager mpm = (MediaProjectionManager)
                getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        if (mpm == null) {
            stopSelf();
            return START_NOT_STICKY;
        }
        Vision.start(this, Config.SHARED, result, data);
        return START_NOT_STICKY;
    }

    private Notification notification() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm != null && nm.getNotificationChannel(CHANNEL_ID) == null) {
            nm.createNotificationChannel(
                    new NotificationChannel(CHANNEL_ID, "vision",
                            NotificationManager.IMPORTANCE_LOW));
        }
        return new Notification.Builder(this, CHANNEL_ID)
                .setContentTitle("vision")
                .setContentText("screen capture")
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setOngoing(true)
                .build();
    }

    @Override
    public void onDestroy() {
        Vision.stop();
        super.onDestroy();
    }

    /**
     * The screen just changed shape - which is what opening the game does.
     *
     * <p>A service is told about configuration changes even when the Activity
     * that started it is portrait-locked, so this is where the capture gets
     * rebuilt at the size the display actually is now. Without it every box
     * after rotation lands in the wrong place.
     */
    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        Vision.resize(this);
    }
}