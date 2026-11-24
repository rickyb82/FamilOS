package com.familos.homepulse;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.ConnectivityManager;
import android.net.Network;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;

/** User-started connected-device foreground service; no hidden permanent worker. */
public final class HomePulseService extends Service {
    public static final String ACTION_CONNECT="com.familos.homepulse.CONNECT";
    public static final String ACTION_SCAN="com.familos.homepulse.SCAN";
    private static final String ACTION_STOP="com.familos.homepulse.STOP";
    private static final String CHANNEL="homepulse.collector";
    private static final int NOTIFICATION=20;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private HomePulseApp app;
    private ConnectivityManager connectivity;
    private PowerManager.WakeLock trainingLock;
    private long nextSync=0,nextRefresh=0,nextNotification=0;
    private boolean networkRegistered=false;
    private final ConnectivityManager.NetworkCallback networkCallback=new ConnectivityManager.NetworkCallback(){
        @Override public void onAvailable(Network network){handler.post(()->{if(app!=null&&app.api!=null)app.api.sync();});}
    };
    @Override public void onCreate(){
        super.onCreate();app=HomePulseApp.app(this);
        NotificationManager manager=getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL,"Anello e allenamento",NotificationManager.IMPORTANCE_LOW));
        connectivity=getSystemService(ConnectivityManager.class);
        try{connectivity.registerDefaultNetworkCallback(networkCallback);networkRegistered=true;}catch(RuntimeException ignored){}
        PowerManager power=getSystemService(PowerManager.class);
        trainingLock=power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"HomePulse:training");trainingLock.setReferenceCounted(false);
    }
    private Notification notification(){
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop=PendingIntent.getService(this,1,new Intent(this,HomePulseService.class).setAction(ACTION_STOP),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        String message=app.ring!=null&&app.ring.ready?"Anello collegato · dati salvati sul telefono":"Collegamento anello attivo";
        return new Notification.Builder(this,CHANNEL).setSmallIcon(R.drawable.ic_pulse).setContentTitle("Home Pulse")
            .setContentText(message).setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true)
            .setVisibility(Notification.VISIBILITY_PRIVATE).addAction(new Notification.Action.Builder(null,"Ferma",stop).build()).build();
    }
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        if(intent!=null&&ACTION_STOP.equals(intent.getAction())){app.stopCollector();stopSelf();return START_NOT_STICKY;}
        if(app.initializationError!=null||!app.collectorEnabled()||!app.bluetoothGranted()){stopSelf();return START_NOT_STICKY;}
        try{if(Build.VERSION.SDK_INT>=29)startForeground(NOTIFICATION,notification(),ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);else startForeground(NOTIFICATION,notification());}
        catch(RuntimeException error){app.statusMessage="Autorizza Bluetooth e avvia la raccolta dall’app aperta";stopSelf();return START_NOT_STICKY;}
        handler.removeCallbacks(tick);handler.post(tick);
        if(intent!=null&&ACTION_SCAN.equals(intent.getAction()))app.ring.scan();
        else{String address=app.selectedRingAddress();if(address.isEmpty())app.statusMessage="Scegli l’anello dalla ricerca Bluetooth";else if(!app.ring.connected&&!app.ring.busy)app.ring.connect(address);}
        app.notifyChanged();return START_STICKY;
    }
    private final Runnable tick=new Runnable(){public void run(){
        if(app==null||!app.collectorEnabled()){stopSelf();return;}
        long now=System.currentTimeMillis();
        try{
            app.ring.maintenance();
            if(now>=nextSync){nextSync=now+15000;app.api.sync();}
            if(now>=nextRefresh){nextRefresh=now+30000;app.api.refresh();}
            if(now>=nextNotification){nextNotification=now+15000;getSystemService(NotificationManager.class).notify(NOTIFICATION,notification());}
            if(app.workout.current()!=null){if(!trainingLock.isHeld())trainingLock.acquire(90000);}
            else if(trainingLock.isHeld())trainingLock.release();
            app.notifyChanged();
        }catch(RuntimeException error){app.statusMessage="Raccolta da verificare nell’app; archivio conservato";app.notifyChanged();}
        handler.postDelayed(this,1000);
    }};
    @Override public void onDestroy(){handler.removeCallbacks(tick);if(networkRegistered)try{connectivity.unregisterNetworkCallback(networkCallback);}catch(RuntimeException ignored){}if(trainingLock!=null&&trainingLock.isHeld())trainingLock.release();stopForeground(STOP_FOREGROUND_REMOVE);super.onDestroy();}
    @Override public IBinder onBind(Intent intent){return null;}
}
