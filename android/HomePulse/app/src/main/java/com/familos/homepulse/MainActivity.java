package com.familos.homepulse;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Typeface;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.GridLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import org.json.JSONArray;
import org.json.JSONObject;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;

/** Framework-only native UI. Forms are never rebuilt by the one-second live refresh. */
public final class MainActivity extends Activity {
    private static final int BG = 0xff101a21, PANEL = 0xff19282f, TEXT = 0xffedf4f1;
    private static final int MUTED = 0xffa2b8b8, ACCENT = 0xff82dcd2, BORDER = 0xff33484d;
    private static final String[] TABS = {"Dashboard", "Allenamento", "Diario", "Calendario", "Impostazioni"};
    private static final String[] DAYS = {"Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"};
    private HomePulseApp app;
    private LinearLayout shell, content, navigation;
    private GridLayout metrics;
    private TextView connection, heartValue, heartNote, timer, phaseLabel, energyLabel, currentActivity, queueLabel, ringLabel;
    private TextView spo2Value, tempValue, breathValue, motionValue, stepValue;
    private TextView spo2Time, tempTime, breathTime, motionTime, stepTime;
    private Button startButton, pauseButton, finishButton;
    private EditText activityInput, serverInput, codeInput;
    private ChartsView dashboardChart, diaryChart;
    private LinearLayout diaryEntries;
    private TextView diarySummary;
    private String selectedSession = "", diaryFingerprint = "", pendingServer = "https://familos.example", pendingCode = "";
    private JSONObject selectedAnalysis;
    private int tab = 0;
    private boolean resumed;
    private final HashMap<String, String> drafts = new HashMap<>();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Runnable observer = this::refresh;
    private final Runnable ticker = new Runnable() { public void run() { if (!resumed) return; refresh(); main.postDelayed(this, 1000); } };
    private Runnable permissionAction;
    private AlertDialog devicesDialog;
    private ArrayAdapter<String> devicesAdapter;
    private final List<BluetoothDevice> displayedDevices = new ArrayList<>();
    private final SimpleDateFormat dateTime = new SimpleDateFormat("d MMM · HH:mm", Locale.ITALY);

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        app = HomePulseApp.app(this);
        if (app.initializationError != null) {
            TextView error = new TextView(this);
            error.setText(app.initializationError);
            error.setTextColor(Color.WHITE); error.setTextSize(19);
            error.setPadding(30,60,30,30); setContentView(error); return;
        }
        dateTime.setTimeZone(TimeZone.getTimeZone("Europe/Rome"));
        getWindow().setStatusBarColor(BG); getWindow().setNavigationBarColor(BG);
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        if (state != null) {
            tab = state.getInt("tab", 0);
            Bundle saved = state.getBundle("form_drafts");
            if (saved != null) for (String key : saved.keySet()) drafts.put(key, saved.getString(key, ""));
        }
        buildShell();
        handleLink(getIntent());
        showTab(tab);
    }

    @Override protected void onResume() {
        super.onResume(); if (app.initializationError != null) return;
        resumed = true; app.addObserver(observer); refresh(); main.post(ticker);
    }
    @Override protected void onPause() {
        resumed = false; main.removeCallbacks(ticker); app.removeObserver(observer); super.onPause();
    }
    @Override protected void onDestroy() {
        if (devicesDialog != null) devicesDialog.dismiss();
        super.onDestroy();
    }
    @Override protected void onSaveInstanceState(Bundle state) {
        state.putInt("tab", tab);
        Bundle saved = new Bundle();
        for (Map.Entry<String, String> item : drafts.entrySet()) saved.putString(item.getKey(), item.getValue());
        state.putBundle("form_drafts", saved); super.onSaveInstanceState(state);
    }
    @Override public void onConfigurationChanged(Configuration configuration) {
        super.onConfigurationChanged(configuration);
        if (metrics != null) metrics.setColumnCount(wide() ? 3 : 2);
        if (shell != null) shell.requestLayout();
    }
    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent); setIntent(intent); if (app.initializationError != null) return; handleLink(intent); showTab(tab);
    }

    private void buildShell() {
        shell = column(); shell.setBackgroundColor(BG);
        shell.setPadding(dp(16), dp(12), dp(16), dp(8));
        shell.setOnApplyWindowInsetsListener((view, insets) -> {
            int top, bottom;
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                top = bars.top; bottom = bars.bottom;
            } else { top = insets.getSystemWindowInsetTop(); bottom = insets.getSystemWindowInsetBottom(); }
            view.setPadding(dp(16), dp(12) + top, dp(16), dp(8) + bottom);
            return insets;
        });
        LinearLayout header = row(); header.setGravity(Gravity.CENTER_VERTICAL);
        TextView brand = text("FAMILOS  /  HOME PULSE", 12, ACCENT); brand.setLetterSpacing(.12f);
        header.addView(brand, new LinearLayout.LayoutParams(0, dp(32), 1));
        header.addView(button("Sincronizza", () -> runAction(() -> app.sync()), false));
        shell.addView(header);
        connection = text("In attesa del collegamento", 12, MUTED); connection.setPadding(0, dp(3), 0, dp(13)); shell.addView(connection);
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true); scroll.setClipToPadding(false);
        content = column(); content.setPadding(0, dp(12), 0, dp(22)); scroll.addView(content);
        shell.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        HorizontalScrollView navScroll = new HorizontalScrollView(this); navScroll.setHorizontalScrollBarEnabled(false);
        navigation = row(); navigation.setPadding(0, dp(8), 0, dp(3));
        navScroll.addView(navigation); shell.addView(navScroll, new LinearLayout.LayoutParams(-1, -2));
        setContentView(shell);
    }

    private void showTab(int selected) {
        tab = Math.max(0, Math.min(TABS.length - 1, selected));
        content.removeAllViews(); navigation.removeAllViews();
        heartValue = heartNote = timer = phaseLabel = energyLabel = currentActivity = queueLabel = ringLabel = null;
        spo2Value = tempValue = breathValue = motionValue = stepValue = null;
        spo2Time = tempTime = breathTime = motionTime = stepTime = null;
        startButton = pauseButton = finishButton = null;
        activityInput = serverInput = codeInput = null; dashboardChart = diaryChart = null; metrics = null; diaryEntries = null; diarySummary = null;
        for (int index = 0; index < TABS.length; index++) {
            final int destination = index;
            Button item = button(TABS[index], () -> showTab(destination), index == tab);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-2, dp(47)); params.setMargins(0, 0, dp(6), 0);
            navigation.addView(item, params);
        }
        if (tab == 0) buildDashboard();
        else if (tab == 1) buildWorkout();
        else if (tab == 2) buildDiary();
        else if (tab == 3) buildCalendar();
        else buildSettings();
        refresh();
    }

    private void heading(String eyebrow, String title, String subtitle) {
        TextView overline = text(eyebrow.toUpperCase(Locale.ITALY), 10, ACCENT); overline.setLetterSpacing(.13f); content.addView(overline);
        TextView name = text(title, 29, TEXT); name.setPadding(0, dp(8), 0, dp(7)); content.addView(name);
        TextView description = text(subtitle, 13, MUTED); description.setPadding(0, 0, 0, dp(22)); content.addView(description);
    }

    private void buildDashboard() {
        heading("Il corpo, nel tuo spazio", "Il tuo ritmo.", "Segnali reali dal Gen3. I campioni assenti restano vuoti.");
        LinearLayout heart = card();
        heart.addView(text("FREQUENZA CARDIACA", 11, MUTED));
        heartValue = text("—", 78, ACCENT); heartValue.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL)); heart.addView(heartValue);
        heartNote = text("Collega il tuo anello per ricevere il battito.", 12, MUTED); heart.addView(heartNote);
        LinearLayout actions = row(); actions.setPadding(0, dp(15), 0, 0);
        addWeighted(actions, button("Collega anello", this::chooseRing, true));
        addWeighted(actions, button("Ferma lettura", () -> runAction(() -> app.stopCollector()), false)); heart.addView(actions);
        content.addView(heart);
        metrics = new GridLayout(this); metrics.setColumnCount(wide() ? 3 : 2);
        TextView[] oxygen = metric(metrics, "SpO₂", "Storico elaborato", ACCENT); spo2Value = oxygen[0]; spo2Time = oxygen[1];
        TextView[] temperature = metric(metrics, "Temperatura anello", "Non temperatura interna", 0xfff0ae96); tempValue = temperature[0]; tempTime = temperature[1];
        TextView[] breathing = metric(metrics, "Respiro", "Media nel record storico", 0xff9acfe8); breathValue = breathing[0]; breathTime = breathing[1];
        TextView[] motion = metric(metrics, "Movimento", "Secondi nel record storico", 0xffb8abe7); motionValue = motion[0]; motionTime = motion[1];
        TextView[] steps = metric(metrics, "Passi", "Incremento ultimo record", 0xffc7e99b); stepValue = steps[0]; stepTime = steps[1];
        content.addView(metrics);
        LinearLayout chartCard = card(); chartCard.addView(text("Una sola linea del tempo", 18, TEXT));
        chartCard.addView(text("Acquisizione reale · ora di Roma · ultime 24 ore", 11, MUTED));
        dashboardChart = new ChartsView(this); chartCard.addView(dashboardChart, new LinearLayout.LayoutParams(-1, dp(484))); content.addView(chartCard);
        queueLabel = text("Archivio e coda locale", 12, MUTED); queueLabel.setPadding(dp(3), dp(3), 0, dp(10)); content.addView(queueLabel);
    }

    private TextView[] metric(GridLayout grid, String title, String subtitle, int color) {
        LinearLayout tile = card(); tile.setPadding(dp(14), dp(17), dp(14), dp(17));
        tile.addView(text(title, 12, MUTED)); TextView number = text("—", 27, color); number.setPadding(0, dp(6), 0, dp(7)); tile.addView(number);
        tile.addView(text(subtitle, 10, MUTED)); TextView stamp = text("Nessun record disponibile", 10, MUTED); stamp.setPadding(0, dp(5), 0, 0); tile.addView(stamp);
        GridLayout.LayoutParams params = new GridLayout.LayoutParams(); params.width = 0; params.height = -2;
        params.columnSpec = GridLayout.spec(GridLayout.UNDEFINED, 1f); params.rowSpec = GridLayout.spec(GridLayout.UNDEFINED);
        params.setMargins(dp(3), dp(4), dp(3), dp(4)); tile.setLayoutParams(params); grid.addView(tile);
        return new TextView[]{number, stamp};
    }

    private void buildWorkout() {
        heading("Una sessione alla volta", "Allenamento", "Fasi e intervalli personali. Nessuna zona ricavata automaticamente dall’età.");
        LinearLayout session = card(); phaseLabel = text("PRONTO QUANDO LO SEI TU", 11, ACCENT); session.addView(phaseLabel);
        timer = text("00:00", 66, TEXT); timer.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL)); session.addView(timer);
        currentActivity = text("Sessione libera", 13, MUTED); session.addView(currentActivity);
        activityInput = input(session, "Attività", "workout_activity", config().optString("activity_name", "Allenamento"), false);
        LinearLayout controls = row(); startButton = button("Inizia", this::startWorkout, true);
        pauseButton = button("Pausa", () -> runAction(() -> {
            if ("paused".equals(current().optString("status"))) app.resumeWorkout(); else app.pauseWorkout();
        }), false);
        finishButton = button("Termina", () -> new AlertDialog.Builder(this).setTitle("Terminare la sessione?")
                .setMessage("Il riepilogo e i campioni acquisiti resteranno nel diario.")
                .setNegativeButton("Continua", null).setPositiveButton("Termina", (dialog, which) -> runAction(() -> app.endWorkout())).show(), false);
        addWeighted(controls, startButton); addWeighted(controls, pauseButton); addWeighted(controls, finishButton); session.addView(controls);
        energyLabel = text("Calorie non stimate: servono peso e MET delle fasi.", 12, MUTED); energyLabel.setPadding(0, dp(14), 0, 0); session.addView(energyLabel);
        content.addView(session);
        LinearLayout plan = card(); plan.addView(text("La tua scheda", 18, TEXT));
        JSONArray phases = config().optJSONArray("plan");
        if (phases == null || phases.length() == 0) plan.addView(note("Nessuna fase configurata. Il timer registra il tempo attivo senza cambiare automaticamente esercizio."));
        else {
            plan.addView(note(config().optInt("rounds", 1) + " giri · " + phases.length() + " fasi"));
            for (int i = 0; i < phases.length(); i++) {
                JSONObject phase = phases.optJSONObject(i); if (phase == null) continue;
                plan.addView(note((i + 1) + "  " + phase.optString("name") + " · " + duration(phase.optDouble("duration")) + target(phase)));
            }
        }
        plan.addView(button("Modifica la scheda", this::editPlan, false)); content.addView(plan);
        LinearLayout sport = card(); sport.addView(text("Lettura durante l’attività", 17, TEXT));
        sport.addView(note("Il comando sport Gen3 è separato dal timer della sessione. Scegli il tipo di attività supportato dall’anello."));
        sport.addView(button("Configura lettura sport", this::sportDialog, false));
        sport.addView(button("Ferma lettura sport", () -> runAction(() -> app.stopSport()), false)); content.addView(sport);
    }

    private void startWorkout() {
        String name = activityInput == null ? "Allenamento" : activityInput.getText().toString().trim();
        if (name.isEmpty()) { toast("Scegli il nome dell’attività."); return; }
        runAction(() -> { if (current().optBoolean("provisional")) { app.workout.confirm(name); app.sync(); } else app.startWorkout(name); });
    }

    private void buildDiary() {
        heading("Ritrova i tuoi progressi", "Il diario", "Campioni locali e storico sincronizzato, al loro orario di acquisizione.");
        content.addView(button("Ultime 24 ore", () -> { selectedSession = ""; selectedAnalysis = null; updateDiaryChart(); }, false));
        diarySummary = text("Seleziona una sessione per il riepilogo.", 12, MUTED); diarySummary.setPadding(0, dp(12), 0, dp(12)); content.addView(diarySummary);
        LinearLayout chart = card(); diaryChart = new ChartsView(this); chart.addView(diaryChart, new LinearLayout.LayoutParams(-1, dp(484))); content.addView(chart);
        diaryEntries = column(); content.addView(diaryEntries); diaryFingerprint = "";
    }

    private void updateDiaryEntries() {
        if (diaryEntries == null) return;
        JSONArray local = app.workout.sessions(); JSONArray remote = state().optJSONArray("sessions");
        String fingerprint = String.valueOf(local) + String.valueOf(remote);
        if (fingerprint.equals(diaryFingerprint)) return;
        diaryFingerprint = fingerprint; diaryEntries.removeAllViews();
        List<JSONObject> all = new ArrayList<>(); java.util.HashSet<String> identities = new java.util.HashSet<>();
        for (JSONArray source : new JSONArray[]{local, remote}) if (source != null) for (int i = 0; i < source.length(); i++) {
            JSONObject item = source.optJSONObject(i); if (item == null) continue;
            String id = item.optString("remote_id", item.optString("id", ""));
            if (!id.isEmpty() && identities.add(id)) all.add(item);
        }
        all.sort((a, b) -> Double.compare(epoch(b.opt("started_at")), epoch(a.opt("started_at"))));
        if (all.isEmpty()) diaryEntries.addView(note("La prima sessione terminata apparirà qui. I dati disponibili non vengono simulati."));
        for (JSONObject item : all) {
            LinearLayout entry = card(); String id = item.optString("remote_id", item.optString("id", ""));
            entry.addView(text(item.optString("activity_name", "Allenamento"), 17, TEXT));
            entry.addView(note(stamp(epoch(item.opt("started_at"))) + " · " + duration(item.optDouble("active_seconds")) + " · " + statusLabel(item.optString("status"))));
            entry.addView(button("Apri grafici", () -> selectSession(id, item), false)); diaryEntries.addView(entry);
        }
    }

    private void selectSession(String id, JSONObject session) {
        selectedSession = id;
        JSONObject cached = analysis(); double start = epoch(session.opt("started_at")), end = epoch(session.opt("ended_at"));
        double finish = end > 0 ? Math.min(now(), end + 600) : now();
        selectedAnalysis = clipAnalysis(cached, start, finish, end);
        if (diarySummary != null) diarySummary.setText(session.optString("activity_name", "Allenamento") + " · " + stamp(start) + "\nCampioni già disponibili; sincronizza per recuperare eventuali dati mancanti.");
        updateDiaryChart();
        app.loadAnalysis(id);
    }

    private void updateDiaryChart() {
        if (!selectedSession.isEmpty()) {
            JSONObject cached = app.store.getJson("analysis." + selectedSession, new JSONObject());
            if (cached.has("window_start")) selectedAnalysis = cached;
        }
        if (diaryChart != null) diaryChart.setData(selectedSession.isEmpty() ? analysis() : selectedAnalysis);
        if (diarySummary != null && selectedSession.isEmpty()) diarySummary.setText("Ultime 24 ore disponibili · ora di Roma");
    }

    private void buildCalendar() {
        heading("Spazio al movimento", "La settimana", "Gli appuntamenti del tuo profilo FamilOS, nel fuso di Roma.");
        JSONArray slots = settings().optJSONArray("weekly_schedule");
        if (slots == null || slots.length() == 0) content.addView(note("Nessun appuntamento configurato. Aggiungi gli orari dalla tua dashboard FamilOS."));
        else for (int day = 1; day <= 7; day++) for (int i = 0; i < slots.length(); i++) {
            JSONObject slot = slots.optJSONObject(i); if (slot == null || slot.optInt("weekday") != day) continue;
            LinearLayout appointment = card(); appointment.addView(text(DAYS[day - 1], 12, ACCENT));
            appointment.addView(text(slot.optString("start") + " — " + slot.optString("end"), 28, TEXT));
            appointment.addView(note(slot.optString("label", "Palestra") + " · " + slot.optString("timezone", "Europe/Rome"))); content.addView(appointment);
        }
        JSONObject detection = settings().optJSONObject("auto_detection"); JSONObject gym = detection == null ? null : detection.optJSONObject("gym");
        LinearLayout place = card(); place.addView(text("Il tuo luogo", 18, TEXT));
        place.addView(note(gym == null ? "Nessuna palestra configurata." : gym.optString("name") + "\n" + gym.optString("address")));
        place.addView(note("Gli orari provengono dal tuo profilo. Il rilevamento automatico dipende dalle funzioni e dai permessi disponibili sul dispositivo; non sostituisce la scelta della tua attività.")); content.addView(place);
    }

    private void buildSettings() {
        heading("Il collegamento è tuo", "Impostazioni", "Il tuo server, il tuo anello, il tuo profilo.");
        LinearLayout pairing = card(); pairing.addView(text("Collega a FamilOS", 18, TEXT));
        pairing.addView(note("Nella dashboard apri iPhone e anello → Collega dispositivo. Incolla il link oppure inserisci server e codice monouso."));
        serverInput = plainInput(pairing, "Server HTTPS", pendingServer, false); serverInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        codeInput = plainInput(pairing, "Codice monouso", pendingCode, false); codeInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD); codeInput.setSaveEnabled(false);
        LinearLayout enrollActions = row(); addWeighted(enrollActions, button("Incolla link", this::pastePairing, false));
        addWeighted(enrollActions, button("Associa", this::enroll, true)); pairing.addView(enrollActions); content.addView(pairing);
        LinearLayout ring = card(); ring.addView(text("Oura Gen3", 18, TEXT)); ringLabel = note("Nessun anello selezionato"); ring.addView(ringLabel);
        ring.addView(button("Cerca e scegli anello", this::chooseRing, true));
        ring.addView(button("Collega anello selezionato", () -> ensurePermissions(() -> runAction(() -> app.startCollector())), false));
        ring.addView(note("La lettura può continuare con schermo spento tramite la notifica persistente. Per interromperla usa Ferma lettura, qui o nella notifica."));
        ring.addView(button("Ferma lettura", () -> runAction(() -> app.stopCollector()), false)); content.addView(ring);
        LinearLayout profile = card(); profile.addView(text("Profilo personale", 18, TEXT));
        JSONObject value = settings().optJSONObject("profile"); profile.addView(note(value == null ? "Misure non inserite." : "Peso " + number(value.optDouble("weight_kg", Double.NaN), 1) + " kg · altezza " + number(value.optDouble("height_cm", Double.NaN), 0) + " cm"));
        profile.addView(button("Modifica profilo", this::editProfile, false)); content.addView(profile);
        LinearLayout archive = card(); archive.addView(text("Archivio personale", 18, TEXT)); queueLabel = note(""); archive.addView(queueLabel);
        archive.addView(note("Gli eventi vengono conservati sul dispositivo prima dell’invio. Un dato scaricato in ritardo mantiene l’orario originale. Nessun valore corporeo viene inventato.")); content.addView(archive);
    }

    private void refresh() {
        if (isFinishing() || app == null) return;
        connection.setText(nonempty(app.syncStatus, "In attesa del server") + (nonempty(app.statusMessage, "").isEmpty() ? "" : " · " + app.statusMessage));
        if (queueLabel != null) queueLabel.setText(app.store.pendingCount() + " eventi da inviare · " + app.store.journalCount() + " eventi conservati sul dispositivo");
        if (ringLabel != null) ringLabel.setText(app.selectedRingAddress().isEmpty() ? "Nessun anello selezionato" : "Selezionato: " + app.selectedRingAddress());
        if (heartValue != null) refreshHeart();
        JSONObject latest = state().optJSONObject("latest");
        updateMetric(latest, "spo2", spo2Value, spo2Time, "%", 0);
        updateMetric(latest, "temperature", tempValue, tempTime, "°C", 1);
        updateMetric(latest, "respiratory_rate", breathValue, breathTime, "resp/min", 1);
        updateMetric(latest, "motion", motionValue, motionTime, "s", 0);
        updateMetric(latest, "steps", stepValue, stepTime, "", 0);
        if (dashboardChart != null) dashboardChart.setData(analysis());
        if (timer != null) refreshWorkout();
        if (tab == 2) { updateDiaryEntries(); updateDiaryChart(); }
        if (devicesDialog != null && devicesDialog.isShowing()) refreshDevices();
    }

    private void refreshHeart() {
        JSONObject latest = state().optJSONObject("latest"); JSONObject heart = latest == null ? null : latest.optJSONObject("heart_rate");
        // Local readings are useful while offline, but keep their original capture time.
        JSONArray local = app.store.recentReadings();
        if (local != null && local.length() > 0) {
            JSONObject candidate = local.optJSONObject(local.length() - 1);
            if (candidate != null && (heart == null || epoch(candidate.opt("t")) > epoch(heart.opt("captured_at")))) {
                heart = copy(candidate); put(heart, "captured_at", candidate.opt("t")); put(heart, "bpm", candidate.opt("v"));
            }
        }
        if (Double.isFinite(app.liveBpm) && app.liveCapturedAt > 0 && (heart == null || app.liveCapturedAt / 1000.0 >= epoch(heart.opt("captured_at")))) {
            heart = new JSONObject(); put(heart,"captured_at",app.liveCapturedAt / 1000.0); put(heart,"bpm",app.liveBpm);
            put(heart,"warnings",new JSONArray(app.liveWarnings == null ? java.util.Collections.emptyList() : app.liveWarnings));
        }
        double captured = heart == null ? 0 : epoch(heart.opt("captured_at"));
        double bpm = heart == null ? Double.NaN : heart.optDouble("bpm", heart.optDouble("value", Double.NaN));
        JSONObject outside = latest == null ? null : latest.optJSONObject("heart_rate_outside_display");
        boolean outsideNewer = outside != null && epoch(outside.opt("captured_at")) > captured;
        JSONArray warnings = heart == null ? null : heart.optJSONArray("warnings");
        boolean fresh = captured > 0 && now() - captured >= -2 && now() - captured <= 8;
        boolean newerInvalidNotification = app.liveLastNotificationAt > app.liveCapturedAt && app.liveLastNotificationAt / 1000.0 > captured;
        boolean quality = fresh && !outsideNewer && !newerInvalidNotification && Double.isFinite(bpm) && bpm >= 20 && bpm <= 250;
        if (heart != null && heart.has("ibi_validity_raw") && heart.optInt("ibi_validity_raw") != 1) quality = false;
        heartValue.setText(quality ? number(bpm, 0) + " bpm" : "—");
        if (newerInvalidNotification) heartNote.setText("L’ultima notifica non contiene un intervallo cardiaco valido. In attesa del prossimo campione.");
        else if (outsideNewer || fresh && Double.isFinite(bpm) && (bpm < 20 || bpm > 250)) heartNote.setText("Ultima lettura fuori dall’intervallo della vista principale. Il dato originale è conservato.");
        else if (!fresh) heartNote.setText(captured > 0 ? "Ultimo campione " + stamp(captured) + ". In attesa di una lettura recente." : "Nessun campione cardiaco disponibile.");
        else if (!quality) heartNote.setText(warnings != null && warnings.length() > 0 ? warningText(warnings) : "Qualità del segnale da verificare.");
        else heartNote.setText("Ricevuto " + Math.max(0, (int)(now() - captured)) + " s fa · acquisizione reale" + (warnings != null && warnings.length() > 0 ? "\n" + warningText(warnings) : ""));
    }

    private void updateMetric(JSONObject latest, String key, TextView value, TextView time, String unit, int digits) {
        if (value == null) return;
        JSONObject point = latest == null ? null : latest.optJSONObject(key);
        double number = point == null ? Double.NaN : point.optDouble("value", Double.NaN);
        value.setText(number(number, digits) + (Double.isFinite(number) && !unit.isEmpty() ? " " + unit : ""));
        double timestamp = point == null ? 0 : point.optDouble("utc_ms", 0) / 1000;
        time.setText(timestamp > 0 ? stamp(timestamp) : "respiratory_rate".equals(key) ? "Nessun record del respiro" : "Nessun record disponibile");
    }

    private void refreshWorkout() {
        JSONObject current = current(), position = app.workout.position(), energy = app.workout.energy();
        String status = current.optString("status", ""); boolean active = "active".equals(status) || "paused".equals(status);
        boolean paused = "paused".equals(status), provisional = current.optBoolean("provisional");
        double elapsed = active ? app.workout.elapsedSeconds() : 0;
        JSONObject sessionConfig = current.optJSONObject("config"); if (sessionConfig == null) sessionConfig = config();
        JSONArray plan = sessionConfig.optJSONArray("plan"); boolean planned = plan != null && plan.length() > 0;
        phaseLabel.setText(provisional ? "POSSIBILE ALLENAMENTO" : paused ? "IN PAUSA" : active ? planned ? position.optString("name", "SESSIONE IN CORSO") : "SESSIONE LIBERA" : "PRONTO QUANDO LO SEI TU");
        timer.setText(duration(active ? planned && !position.optBoolean("complete") ? position.optDouble("remaining", elapsed) : elapsed : planDuration(config())));
        currentActivity.setText(active ? current.optString("activity_name", "Allenamento") + (planned ? " · giro " + position.optInt("round", 1) : " · tempo attivo") + target(position) : "Scegli l’attività quando sei pronto.");
        startButton.setVisibility(!active || provisional ? View.VISIBLE : View.GONE); startButton.setText(provisional ? "Conferma" : "Inizia");
        pauseButton.setVisibility(active && !provisional ? View.VISIBLE : View.GONE); pauseButton.setText(paused ? "Riprendi" : "Pausa");
        finishButton.setVisibility(active ? View.VISIBLE : View.GONE);
        // Only enable/disable this field; never overwrite text typed by the user.
        activityInput.setEnabled(!active || provisional);
        double covered = energy.optDouble("estimated_seconds", 0), missing = energy.optDouble("unestimated_seconds", 0);
        energyLabel.setText(covered > 0 ? number(energy.optDouble("net_kcal", energy.optDouble("net", Double.NaN)), 0) + " kcal attive · " + number(energy.optDouble("gross_kcal", energy.optDouble("gross", Double.NaN)), 0) + " kcal totali\nStima da peso e MET · " + duration(covered) + " stimati" + (missing > 0 ? " · " + duration(missing) + " senza stima" : "") : "Calorie — · nessun intervallo stimabile. Servono peso e MET delle fasi.");
    }

    private void chooseRing() {
        ensurePermissions(() -> {
            runAction(() -> app.scanRing());
            ListView list = new ListView(this); list.setBackgroundColor(PANEL);
            devicesAdapter = new ArrayAdapter<>(this, android.R.layout.simple_list_item_1, new ArrayList<>()); list.setAdapter(devicesAdapter);
            devicesDialog = new AlertDialog.Builder(this).setTitle("Scegli il tuo Oura Gen3")
                    .setMessage("Metti l’anello vicino al telefono. Scegli solo il dispositivo che riconosci.")
                    .setView(list).setNegativeButton("Chiudi", null)
                    .setNeutralButton("Indirizzo manuale", (dialog, which) -> manualAddress())
                    .setPositiveButton("Aggiorna", null).create();
            list.setOnItemClickListener((parent, view, position, id) -> {
                if (position >= displayedDevices.size()) return;
                BluetoothDevice chosen = displayedDevices.get(position);
                runAction(() -> { app.selectRing(chosen.getAddress()); app.startCollector(); }); devicesDialog.dismiss();
            });
            devicesDialog.setOnShowListener(dialog -> devicesDialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> { runAction(() -> app.scanRing()); refreshDevices(); }));
            devicesDialog.show(); refreshDevices();
        });
    }

    private void refreshDevices() {
        if (devicesAdapter == null) return;
        displayedDevices.clear(); List<String> labels = new ArrayList<>();
        if (app.devices != null) for (BluetoothDevice device : new ArrayList<>(app.devices)) {
            String name;
            try { name = device.getName(); } catch (SecurityException denied) { continue; }
            displayedDevices.add(device); labels.add(nonempty(name, "Dispositivo Bluetooth") + "\n" + device.getAddress());
        }
        devicesAdapter.clear(); devicesAdapter.addAll(labels); devicesAdapter.notifyDataSetChanged();
        if (devicesDialog != null) devicesDialog.setMessage(labels.isEmpty() ? "Scansione in corso. Nessun anello rilevato per ora; puoi aggiornare l’elenco." : "Seleziona il tuo anello. Gli altri dispositivi non verranno collegati.");
    }

    private void manualAddress() {
        EditText address = new EditText(this); address.setSingleLine(); address.setHint("AA:BB:CC:DD:EE:FF"); address.setText(app.selectedRingAddress());
        new AlertDialog.Builder(this).setTitle("Indirizzo Bluetooth del tuo anello").setView(address).setNegativeButton("Annulla", null)
                .setPositiveButton("Collega", (dialog, which) -> {
                    String value = address.getText().toString().trim().toUpperCase(Locale.ROOT);
                    if (!BluetoothAdapter.checkBluetoothAddress(value)) { toast("L’indirizzo Bluetooth non è completo."); return; }
                    ensurePermissions(() -> runAction(() -> { app.selectRing(value); app.startCollector(); }));
                }).show();
    }

    private void ensurePermissions(Runnable action) {
        List<String> missing = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 31) {
            for (String permission : new String[]{Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT}) if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) missing.add(permission);
        } else if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) missing.add(Manifest.permission.ACCESS_FINE_LOCATION);
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) missing.add(Manifest.permission.POST_NOTIFICATIONS);
        if (missing.isEmpty()) { action.run(); return; }
        permissionAction = action; requestPermissions(missing.toArray(new String[0]), 410);
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode != 410) return;
        boolean granted = results.length == permissions.length && results.length > 0;
        for (int result : results) granted &= result == PackageManager.PERMISSION_GRANTED;
        Runnable action = permissionAction; permissionAction = null;
        if (granted && action != null) action.run();
        else toast("Consenti Bluetooth e notifiche per avviare la lettura dell’anello.");
    }

    private void handleLink(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        if (data == null || !"familos-homepulse".equals(data.getScheme()) || !"pair".equals(data.getHost())) return;
        pendingServer = nonempty(data.getQueryParameter("server"), ""); pendingCode = nonempty(data.getQueryParameter("code"), ""); tab = 4;
        // Do not retain the one-time code in an Intent once the visible form owns it.
        intent.setData(null);
    }

    private void pastePairing() {
        ClipboardManager clipboard = (ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
        ClipData clip = clipboard == null ? null : clipboard.getPrimaryClip();
        if (clip == null || clip.getItemCount() == 0) { toast("Gli appunti sono vuoti."); return; }
        String value = String.valueOf(clip.getItemAt(0).coerceToText(this)).trim();
        try {
            Uri uri = Uri.parse(value);
            if ("familos-homepulse".equals(uri.getScheme()) && "pair".equals(uri.getHost())) {
                serverInput.setText(nonempty(uri.getQueryParameter("server"), "")); codeInput.setText(nonempty(uri.getQueryParameter("code"), ""));
            } else codeInput.setText(value);
        } catch (RuntimeException invalid) { toast("Il link non è riconoscibile."); }
    }

    private void enroll() {
        String server = serverInput.getText().toString().trim(), code = codeInput.getText().toString().trim();
        Uri uri = Uri.parse(server);
        if (!"https".equals(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null || uri.getFragment() != null || uri.getQuery() != null) { toast("Inserisci l’indirizzo HTTPS del tuo server FamilOS."); return; }
        if (code.isEmpty()) { toast("Inserisci il codice monouso della dashboard."); return; }
        {
            new AlertDialog.Builder(this).setTitle("Conferma il tuo server")
                    .setMessage("Associare questo dispositivo al server " + uri.getHost() + "?")
                    .setNegativeButton("Annulla", null).setPositiveButton("Associa", (dialog, which) -> enrollConfirmed(server, code)).show();
        }
    }
    private void enrollConfirmed(String server, String code) {
        codeInput.setText(""); pendingCode = ""; pendingServer = server;
        runAction(() -> app.enroll(server, code));
    }

    private void editProfile() {
        JSONObject profile = settings().optJSONObject("profile"); if (profile == null) profile = new JSONObject();
        final JSONObject original = profile;
        LinearLayout form = column(); form.setPadding(dp(18), dp(10), dp(18), dp(18));
        String[][] fields = {{"weight_kg","Peso · kg","30","350"},{"height_cm","Altezza · cm","100","250"},{"age","Età · opzionale","18","100"},{"resting_hr","FC a riposo · opzionale","20","200"},{"body_fat_pct","Massa grassa · %","2","65"},{"reported_lean_pct","Massa magra dichiarata · %","2","98"},{"reported_retention_pct","Ritenzione dichiarata · %","0","100"}};
        Map<String, EditText> inputs = new HashMap<>();
        for (String[] field : fields) inputs.put(field[0], input(form, field[1], "profile_" + field[0], original.isNull(field[0]) ? "" : original.optString(field[0], ""), true));
        EditText source = input(form, "Fonte della composizione corporea", "profile_source", nullableString(original, "composition_source"), false);
        EditText goal = input(form, "Il tuo obiettivo", "profile_goal", nullableString(original, "goal"), false);
        form.addView(note("L’acqua appartiene alla massa priva di grasso. Se massa magra e grassa non sono complementari, la stima basale resta vuota."));
        showForm("Il tuo profilo", form, () -> {
            JSONObject updated = copy(original);
            for (String[] field : fields) {
                Double number = optional(inputs.get(field[0]));
                if (number != null && (number < Double.parseDouble(field[2]) || number > Double.parseDouble(field[3]) || "age".equals(field[0]) && number != Math.rint(number))) throw new IllegalArgumentException("Controlla " + field[1]);
                put(updated, field[0], number == null ? JSONObject.NULL : number);
            }
            put(updated, "composition_source", textOrNull(source)); put(updated, "goal", textOrNull(goal));
            JSONObject next = copy(settings()); put(next, "profile", updated); app.saveSettings(next); clearDrafts("profile_");
        });
    }

    private void editPlan() {
        JSONObject original = config(); LinearLayout form = column(); form.setPadding(dp(18), dp(10), dp(18), dp(18));
        EditText name = input(form, "Nome attività", "plan_activity", nullableString(original, "activity_name"), false);
        EditText rounds = input(form, "Giri · 1–100", "plan_rounds", String.valueOf(original.optInt("rounds", 1)), true);
        LinearLayout phaseContainer = column(); form.addView(phaseContainer); List<PhaseFields> phases = new ArrayList<>();
        JSONArray plan = original.optJSONArray("plan");
        if (plan != null) for (int i = 0; i < plan.length(); i++) addPhase(phaseContainer, phases, plan.optJSONObject(i));
        form.addView(button("Aggiungi fase", () -> { if (phases.size() < 40) addPhase(phaseContainer, phases, null); else toast("La scheda può contenere al massimo 40 fasi."); }, false));
        form.addView(note("I target BPM sono scelti da te. I MET sono stime dell’attività: i minuti senza MET restano esclusi dalle calorie. Senza fasi il timer è libero."));
        showForm("La tua scheda", form, () -> {
            double count = required(rounds); if (count < 1 || count > 100 || count != Math.rint(count)) throw new IllegalArgumentException("Inserisci un numero intero di giri tra 1 e 100.");
            JSONArray updated = new JSONArray(); double total = 0;
            for (PhaseFields fields : phases) {
                String title = fields.name.getText().toString().trim(); double seconds = required(fields.seconds);
                Double min = optional(fields.low), max = optional(fields.high), met = optional(fields.met);
                if (title.isEmpty() || title.length() > 120 || seconds < 5 || seconds > 7200 || seconds != Math.rint(seconds)) throw new IllegalArgumentException("Ogni fase richiede nome e durata intera tra 5 e 7200 secondi.");
                if ((min == null) != (max == null) || min != null && (min < 40 || max > 240 || min >= max || min != Math.rint(min) || max != Math.rint(max))) throw new IllegalArgumentException("Inserisci entrambi i target BPM, dal minore al maggiore (40–240).");
                if (met != null && (met < 1 || met > 20)) throw new IllegalArgumentException("I MET devono essere tra 1 e 20 oppure vuoti.");
                JSONObject phase = new JSONObject(); put(phase,"name",title); put(phase,"duration",(int)seconds); put(phase,"target_min",min == null ? JSONObject.NULL : min.intValue()); put(phase,"target_max",max == null ? JSONObject.NULL : max.intValue()); put(phase,"met",met == null ? JSONObject.NULL : met); updated.put(phase); total += seconds;
            }
            if (total * count > 86400) throw new IllegalArgumentException("La scheda completa non può superare 24 ore.");
            JSONObject nextConfig = copy(original); put(nextConfig,"activity_name",textOrNull(name)); put(nextConfig,"rounds",(int)count); put(nextConfig,"plan",updated);
            JSONObject next = copy(settings()); put(next,"config",nextConfig); app.saveSettings(next); clearDrafts("plan_");
        });
    }

    private static final class PhaseFields { EditText name, seconds, low, high, met; }
    private void addPhase(LinearLayout parent, List<PhaseFields> phases, JSONObject phase) {
        JSONObject value = phase == null ? new JSONObject() : phase; PhaseFields fields = new PhaseFields(); LinearLayout tile = card();
        fields.name = plainInput(tile,"Fase o esercizio",value.optString("name", ""),false);
        fields.seconds = plainInput(tile,"Durata · secondi",String.valueOf(value.optInt("duration",60)),true);
        fields.low = plainInput(tile,"FC minima · opzionale",nullableString(value,"target_min"),true);
        fields.high = plainInput(tile,"FC massima · opzionale",nullableString(value,"target_max"),true);
        fields.met = plainInput(tile,"MET · opzionale",nullableString(value,"met"),true);
        tile.addView(button("Elimina fase", () -> { parent.removeView(tile); phases.remove(fields); },false)); phases.add(fields); parent.addView(tile);
    }

    private void sportDialog() {
        JSONObject profile = settings().optJSONObject("profile");
        JSONArray allowed = state().optJSONArray("allowed_activities");
        if (allowed == null || allowed.length() == 0) { toast("Sincronizza prima le attività supportate dal tuo server."); return; }
        List<String> names = new ArrayList<>(); List<Integer> ids = new ArrayList<>();
        for (int i = 0; i < allowed.length(); i++) {
            JSONObject activity = allowed.optJSONObject(i); if (activity == null || !activity.has("id")) continue;
            ids.add(activity.optInt("id")); names.add(activity.optString("name",activity.optString("label","Attività")));
        }
        new AlertDialog.Builder(this).setTitle("Tipo di attività Gen3").setItems(names.toArray(new String[0]), (dialog, which) -> {
            if (profile == null || profile.isNull("age") || profile.isNull("resting_hr")) { toast("Inserisci età e FC a riposo nel tuo profilo prima del comando sport."); return; }
            ensurePermissions(() -> runAction(() -> app.requestSport(ids.get(which), profile.optInt("age"), profile.optInt("resting_hr"))));
        }).setNegativeButton("Annulla",null).show();
    }

    private void showForm(String title, LinearLayout form, CheckedAction save) {
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true); scroll.addView(form);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle(title).setView(scroll).setNegativeButton("Annulla", null).setPositiveButton("Salva", null).create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            try { save.run(); dialog.dismiss(); showTab(tab); toast("Salvato nel tuo profilo."); }
            catch (IllegalArgumentException invalid) { toast(nonempty(invalid.getMessage(), "Controlla i campi inseriti.")); }
            catch (Exception failure) { toast("Salvataggio non riuscito. I campi restano disponibili."); }
        })); dialog.show();
    }

    private JSONObject settings() { JSONObject value = app.store.settings(); return value == null ? new JSONObject() : value; }
    private JSONObject config() { JSONObject value = settings().optJSONObject("config"); return value == null ? new JSONObject() : value; }
    private JSONObject current() { JSONObject value = app.workout.current(); return value == null ? new JSONObject() : value; }
    private JSONObject state() { return app.state == null ? new JSONObject() : app.state; }
    private JSONObject analysis() {
        JSONObject analysis = state().optJSONObject("analysis"); if (analysis == null) analysis = new JSONObject();
        // Preserve cached historical rows and add local heart samples only when absent by timestamp/value.
        JSONObject merged = copy(analysis); JSONArray heart = merged.optJSONArray("heart_rate"); if (heart == null) heart = new JSONArray();
        java.util.HashSet<String> points = new java.util.HashSet<>();
        for (int i = 0; i < heart.length(); i++) { JSONObject point = heart.optJSONObject(i); if (point != null) points.add(point.optString("t") + ":" + point.optString("v")); }
        JSONArray local = app.store.recentReadings(); double start = merged.optDouble("window_start", now() - 86400), end = Math.max(now(), merged.optDouble("window_end", now()));
        if (local != null) for (int i = 0; i < local.length(); i++) {
            JSONObject point = local.optJSONObject(i); if (point == null) continue; double timestamp = epoch(point.opt("t")), bpm = point.optDouble("v", Double.NaN);
            if (timestamp < start || timestamp > end || !Double.isFinite(bpm) || bpm < 20 || bpm > 250) continue;
            if (points.add(point.optString("t") + ":" + point.optString("v"))) heart.put(point);
        }
        put(merged,"heart_rate",heart); if (!merged.has("window_start")) put(merged,"window_start",start); put(merged,"window_end",end);
        return merged;
    }

    private JSONObject clipAnalysis(JSONObject source, double start, double end, double workoutEnd) {
        JSONObject out = new JSONObject(); put(out,"window_start",start); put(out,"window_end",end); if (workoutEnd > 0) put(out,"workout_end",workoutEnd);
        for (String key : new String[]{"heart_rate","spo2","temperature","respiratory_rate","motion","step_increments"}) {
            JSONArray rows = source.optJSONArray(key), included = new JSONArray();
            if (rows != null) for (int i = 0; i < rows.length(); i++) { JSONObject row = rows.optJSONObject(i); if (row != null && row.optDouble("t") >= start && row.optDouble("t") <= end) included.put(row); }
            put(out,key,included);
        } return out;
    }

    private LinearLayout row() { LinearLayout view = new LinearLayout(this); view.setOrientation(LinearLayout.HORIZONTAL); return view; }
    private LinearLayout column() { LinearLayout view = new LinearLayout(this); view.setOrientation(LinearLayout.VERTICAL); return view; }
    private LinearLayout card() {
        LinearLayout view = column(); view.setPadding(dp(18),dp(20),dp(18),dp(20)); view.setBackground(background(PANEL,BORDER,16));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1,-2); params.setMargins(0,0,0,dp(14)); view.setLayoutParams(params); return view;
    }
    private TextView text(String value, int size, int color) {
        TextView view = new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(color); view.setFontFeatureSettings("tnum"); return view;
    }
    private TextView note(String value) { TextView view = text(value,12,MUTED); view.setPadding(0,dp(9),0,dp(13)); view.setLineSpacing(dp(3),1); return view; }
    private Button button(String title, Runnable action, boolean primary) {
        Button view = new Button(this); view.setText(title); view.setAllCaps(false); view.setTextSize(12); view.setTextColor(primary ? BG : TEXT); view.setMinHeight(dp(45)); view.setMinimumHeight(dp(45));
        view.setPadding(dp(13),dp(7),dp(13),dp(7)); view.setBackgroundTintList(null); view.setBackground(background(primary ? ACCENT : 0xff24393f,primary ? ACCENT : BORDER,10));
        view.setOnClickListener(ignored -> action.run()); LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1,-2); params.setMargins(0,dp(6),0,dp(3)); view.setLayoutParams(params); return view;
    }
    private void addWeighted(LinearLayout row, View child) { LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0,-2,1); params.setMargins(dp(3),0,dp(3),0); row.addView(child,params); }
    private EditText input(LinearLayout parent,String label,String key,String initial,boolean numeric) {
        EditText field = plainInput(parent,label,drafts.containsKey(key) ? drafts.get(key) : initial,numeric);
        field.addTextChangedListener(new TextWatcher() { public void beforeTextChanged(CharSequence s,int a,int b,int c) {} public void onTextChanged(CharSequence s,int a,int b,int c) { drafts.put(key,s.toString()); } public void afterTextChanged(Editable e) {} }); return field;
    }
    private EditText plainInput(LinearLayout parent,String label,String initial,boolean numeric) {
        TextView title = text(label,11,MUTED); title.setPadding(0,dp(13),0,dp(6)); parent.addView(title);
        EditText field = new EditText(this); field.setTextColor(TEXT); field.setHintTextColor(0xff789395); field.setTextSize(14); field.setSingleLine(); field.setSelectAllOnFocus(false);
        field.setInputType(numeric ? InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        field.setPadding(dp(12),dp(10),dp(12),dp(10)); field.setBackground(background(0xff111e25,BORDER,9)); field.setText(initial == null || "null".equals(initial) ? "" : initial);
        field.setContentDescription(label); parent.addView(field,new LinearLayout.LayoutParams(-1,dp(46))); return field;
    }
    private GradientDrawable background(int fill,int border,int radius) { GradientDrawable shape = new GradientDrawable(); shape.setColor(fill); shape.setCornerRadius(dp(radius)); shape.setStroke(dp(1),border); return shape; }
    private boolean wide() { return getResources().getConfiguration().screenWidthDp >= 600; }
    private int dp(float value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private void clearDrafts(String prefix) { drafts.keySet().removeIf(key -> key.startsWith(prefix)); }
    private void toast(String value) { Toast.makeText(this,value,Toast.LENGTH_LONG).show(); }
    private interface CheckedAction { void run() throws Exception; }
    private void runAction(CheckedAction action) { try { action.run(); refresh(); } catch (Exception failure) { toast("Operazione non riuscita. Controlla collegamento e stato del dispositivo."); } }
    private static JSONObject copy(JSONObject value) { try { return value == null ? new JSONObject() : new JSONObject(value.toString()); } catch (Exception invalid) { return new JSONObject(); } }
    private static void put(JSONObject object,String key,Object value) { try { object.put(key,value); } catch (Exception invalid) { throw new IllegalArgumentException("Valore non valido"); } }
    private static double now() { return System.currentTimeMillis() / 1000.0; }
    private static double epoch(Object value) {
        if (value instanceof Number) { double number = ((Number)value).doubleValue(); return Double.isFinite(number) && number > 0 ? number : 0; }
        if (value instanceof String) try { return java.time.OffsetDateTime.parse((String)value).toInstant().toEpochMilli() / 1000.0; } catch (RuntimeException invalid) { return 0; }
        return 0;
    }
    private String stamp(double epoch) { return epoch > 0 && Double.isFinite(epoch) ? dateTime.format(new Date((long)(epoch * 1000))) : "Orario non disponibile"; }
    private static String duration(double seconds) { long total = Double.isFinite(seconds) ? Math.max(0,(long)seconds) : 0; return String.format(Locale.ITALY,"%02d:%02d",total / 60,total % 60); }
    private static String number(double value,int digits) { return Double.isFinite(value) ? String.format(Locale.ITALY,"%." + digits + "f",value) : "—"; }
    private static String nonempty(String value,String fallback) { return value == null || value.isEmpty() ? fallback : value; }
    private static String nullableString(JSONObject object,String key) { return object == null || object.isNull(key) ? "" : object.optString(key,""); }
    private static Object textOrNull(EditText field) { String value = field.getText().toString().trim(); if (value.length() > 200) throw new IllegalArgumentException("Il testo può contenere al massimo 200 caratteri."); return value.isEmpty() ? JSONObject.NULL : value; }
    private static Double optional(EditText field) { String value = field.getText().toString().trim(); if (value.isEmpty()) return null; try { double number = Double.parseDouble(value.replace(',','.')); if (!Double.isFinite(number)) throw new NumberFormatException(); return number; } catch (NumberFormatException invalid) { throw new IllegalArgumentException("Inserisci un valore numerico oppure lascia vuoto."); } }
    private static double required(EditText field) { Double value = optional(field); if (value == null) throw new IllegalArgumentException("Completa i campi obbligatori."); return value; }
    private static String target(JSONObject phase) { return phase == null || phase.isNull("target_min") || phase.isNull("target_max") ? "" : " · " + phase.optInt("target_min") + "–" + phase.optInt("target_max") + " bpm"; }
    private static double planDuration(JSONObject config) { JSONArray plan = config.optJSONArray("plan"); double total = 0; if (plan != null) for (int i = 0; i < plan.length(); i++) { JSONObject phase = plan.optJSONObject(i); if (phase != null) total += phase.optDouble("duration",0); } return total * config.optInt("rounds",1); }
    private static String warningText(JSONArray warnings) {
        StringBuilder text = new StringBuilder();
        for (int i = 0; i < warnings.length(); i++) { if (i > 0) text.append(" · "); text.append(warnings.optString(i)); }
        return text.toString();
    }
    private static String statusLabel(String status) { return "completed".equals(status) ? "Terminata" : "paused".equals(status) ? "In pausa" : "active".equals(status) ? "In corso" : "rejected".equals(status) ? "Non confermata" : status; }
}
