package com.familos.homepulse;

import android.Manifest;
import android.app.Application;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.bluetooth.BluetoothDevice;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/** Application-owned runtime: UI, foreground collector and network share one journal. */
public final class HomePulseApp extends Application {
    public Store store;
    public WellnessClient api;
    public Workout workout;
    public RingBle ring;
    public volatile JSONObject state = new JSONObject();
    public volatile String syncStatus = "Pronto anche offline";
    public volatile String statusMessage = "";
    public volatile double liveBpm = Double.NaN;
    public volatile long liveCapturedAt = 0, liveLastNotificationAt = 0;
    public volatile List<String> liveWarnings = Collections.emptyList();
    public volatile List<BluetoothDevice> devices = Collections.emptyList();
    public volatile String initializationError;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final CopyOnWriteArrayList<Runnable> observers = new CopyOnWriteArrayList<>();
    private long nextArchiveSync = 0;
    public static HomePulseApp app(Context context) { return (HomePulseApp) context.getApplicationContext(); }
    @Override public void onCreate() {
        super.onCreate();
        try {
            store = new Store(this);
            workout = new Workout(store);
            api = new WellnessClient(this, store);
            state = api.state();
            ring = new RingBle(this, store, new RingBle.Listener() {
                public void onStatus(String text) { main.post(() -> { statusMessage = text; notifyChanged(); }); }
                public void onLive(double bpm,long at,List<String> warnings) {
                    main.post(() -> {
                        liveLastNotificationAt = at;
                        liveWarnings = warnings == null ? Collections.emptyList() : new ArrayList<>(warnings);
                        liveBpm = Double.isFinite(bpm) && bpm >= 20 && bpm <= 250 ? bpm : Double.NaN;
                        if (Double.isFinite(liveBpm)) {
                            liveCapturedAt = at;
                            try { store.addReading(at, liveBpm, liveWarnings, null, 1); }
                            catch (Exception error) { statusMessage = "Proiezione lettura non salvata; pacchetto originale conservato"; }
                        }
                        notifyChanged();
                    });
                }
                public void onDevices(List<BluetoothDevice> values) { main.post(() -> { devices = new ArrayList<>(values); notifyChanged(); }); }
                public void onArchiveChanged() {
                    main.post(() -> {
                        notifyChanged();
                        long now = System.currentTimeMillis();
                        if (now >= nextArchiveSync) { nextArchiveSync = now + 1500; api.sync(); }
                    });
                }
            });
            api.setListener(new WellnessClient.Listener() {
                public void onState(JSONObject value) { main.post(() -> { state = value; configureRing(); notifyChanged(); }); }
                public void onStatus(String text) { main.post(() -> { syncStatus = text; notifyChanged(); }); }
                public void onSync() { main.post(() -> { state = api.state(); configureRing(); notifyChanged(); }); }
            });
            configureRing();
            if (api.hasCredentials()) api.refresh();
        } catch (Exception error) {
            initializationError = "Archivio locale non disponibile. I dati esistenti sono conservati; riapri l’app e verifica lo spazio libero.";
            statusMessage = initializationError;
        }
    }
    private void configureRing() {
        if (ring == null || api == null) return;
        try {
            String logical = api.logicalRing();
            byte[] key = api.ownerKey();
            if (logical != null && !logical.isEmpty() && key != null && key.length == 16) ring.configure(logical,key);
        } catch (Exception error) { statusMessage = "Associazione anello non ancora disponibile; sincronizza FamilOS"; }
    }
    public void addObserver(Runnable listener) { if (listener != null) observers.addIfAbsent(listener); }
    public void removeObserver(Runnable listener) { observers.remove(listener); }
    public void notifyChanged() {
        if (Looper.myLooper() != Looper.getMainLooper()) { main.post(this::notifyChanged); return; }
        for (Runnable listener : observers) { try { listener.run(); } catch (RuntimeException ignored) { } }
    }
    public void sync() { if (api == null) return; api.sync(); api.refresh(); notifyChanged(); }
    public void loadAnalysis(String sessionId) { if (api != null) api.loadAnalysis(sessionId); }
    public void saveSettings(JSONObject settings) {
        if (api == null) return;
        Workout.validateSettings(settings);
        api.saveSettings(settings);
        notifyChanged();
    }
    public void enroll(String server,String code) {
        if (api == null) throw new IllegalStateException("Archivio non pronto");
        if (api.hasCredentials()) throw new IllegalStateException("Questo telefono è già associato: usa Sincronizza. La coda esistente resta nel suo profilo.");
        api.enroll(server,code,"Home Pulse · Samsung Fold5");
    }
    public boolean bluetoothGranted() {
        return Build.VERSION.SDK_INT < 31 || (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED && checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) == PackageManager.PERMISSION_GRANTED);
    }
    public boolean collectorEnabled() { return store != null && store.getLong("collector.enabled",0) == 1; }
    public String selectedRingAddress() { return store == null ? "" : store.getJson("selectedRing",new JSONObject()).optString("address",""); }
    public void selectRing(String address) {
        if (!android.bluetooth.BluetoothAdapter.checkBluetoothAddress(address)) throw new IllegalArgumentException("Indirizzo Bluetooth non valido");
        store.setJson("selectedRing", Store.object("address",address));
        notifyChanged();
    }
    public void startCollector() {
        if (!bluetoothGranted()) throw new IllegalStateException("Autorizza i dispositivi nelle vicinanze prima di collegare l’anello");
        if (!api.hasCredentials()) throw new IllegalStateException("Associa prima il telefono a FamilOS");
        configureRing();
        store.setLong("collector.enabled",1);
        Intent intent = new Intent(this,HomePulseService.class).setAction(HomePulseService.ACTION_CONNECT);
        try { if (Build.VERSION.SDK_INT >= 26) startForegroundService(intent); else startService(intent); }
        catch (RuntimeException error) { store.setLong("collector.enabled",0); throw new IllegalStateException("Apri l’app e riprova ad avviare la raccolta",error); }
        notifyChanged();
    }
    public void scanRing() {
        if (!bluetoothGranted()) throw new IllegalStateException("Autorizza i dispositivi nelle vicinanze");
        if (!api.hasCredentials()) throw new IllegalStateException("Associa prima FamilOS");
        configureRing();
        store.setLong("collector.enabled",1);
        Intent intent = new Intent(this,HomePulseService.class).setAction(HomePulseService.ACTION_SCAN);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(intent); else startService(intent);
    }
    public void stopCollector() {
        if (store != null) store.setLong("collector.enabled",0);
        if (ring != null) ring.disconnect();
        liveBpm = Double.NaN; liveLastNotificationAt = System.currentTimeMillis();
        stopService(new Intent(this,HomePulseService.class));
        statusMessage = "Raccolta fermata; archivio locale conservato";
        notifyChanged();
    }
    public void startWorkout(String activity) { workout.start(activity); api.sync(); notifyChanged(); }
    public void pauseWorkout() { workout.pause(); api.sync(); notifyChanged(); }
    public void resumeWorkout() { workout.resume(); api.sync(); notifyChanged(); }
    public void endWorkout() { workout.end(); ring.stopSport(); api.sync(); notifyChanged(); }
    public void requestSport(int id,int age,int resting) {
        boolean known = id == 65535 || id == 14 || id == 12 || id == 5 || id == 8 || id == 10 || id == 15;
        if (!known || age < 18 || age > 100 || resting < 20 || resting > 200) throw new IllegalArgumentException("Scegli un’attività verificata, età e FC a riposo");
        ring.startSport(id,age,resting);
    }
    public void stopSport() { if (ring != null) ring.stopSport(); }
}
