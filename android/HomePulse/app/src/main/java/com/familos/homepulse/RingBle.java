package com.familos.homepulse;

import android.Manifest;
import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanFilter;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.ParcelUuid;
import android.os.SystemClock;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/** One GATT owner, serialized ATT operations. The service owns lifecycle and permissions. */
@SuppressLint("MissingPermission")
public final class RingBle implements AutoCloseable {
    public interface Listener {
        void onStatus(String status);
        /** NaN clears an invalid/unavailable sample; it must never enter gym detection. */
        void onLive(double bpm,long capturedAtMillis,List<String> warnings);
        void onDevices(List<BluetoothDevice> devices);
        void onArchiveChanged();
    }
    public volatile boolean ready=false, connected=false, busy=false;
    public volatile String status="Anello scollegato";
    private final Context context;
    private final Store store;
    private final Listener listener;
    private final Handler main=new Handler(Looper.getMainLooper());
    private final ExecutorService worker=Executors.newSingleThreadExecutor(r->{Thread t=new Thread(r,"HomePulse-ring");t.setDaemon(true);return t;});
    private final Object credentialsLock=new Object();
    private final ThreadLocal<Integer> operationGeneration=new ThreadLocal<>();
    private final SharedPreferences preferences;
    private final BluetoothAdapter adapter;
    private final Map<String,BluetoothDevice> devices=new LinkedHashMap<>();
    private final OuraProtocol.Framer framer=new OuraProtocol.Framer();
    private volatile BluetoothGatt gatt;
    private BluetoothGattCharacteristic write,notify;
    private volatile String logicalRing;
    private volatile byte[] ownerKey;
    private volatile int[] api=new int[0];
    private volatile Map<Integer,Integer> capabilities=Collections.emptyMap();
    private volatile JSONObject exerciseRaw=new JSONObject();
    private volatile int generation=0;
    private volatile boolean closed=false,desired=false,bonding=false;
    private boolean scanning=false,receiverRegistered=false,cccdPending=false;
    private String address;
    private Pending pending;
    private List<byte[]> pageRecords;
    private volatile long nextHistoryAt=0,nextExerciseAt=0,nextSportAt=0;
    private Runnable reconnect,setupTimeout;

    public RingBle(Context context,Store store,Listener listener) {
        this.context=context.getApplicationContext();this.store=store;this.listener=listener;
        preferences=this.context.getSharedPreferences("homepulse.ring",Context.MODE_PRIVATE);
        BluetoothManager manager=this.context.getSystemService(BluetoothManager.class);
        adapter=manager==null?null:manager.getAdapter();
    }
    private boolean ensureBondReceiver(){
        if(receiverRegistered)return true;
        IntentFilter filter=new IntentFilter(BluetoothDevice.ACTION_BOND_STATE_CHANGED);
        try{
            // Bluetooth broadcasts can originate from its privileged non-system UID.
            if(Build.VERSION.SDK_INT>=33)context.registerReceiver(bonds,filter,Context.RECEIVER_EXPORTED);
            else context.registerReceiver(bonds,filter);
            receiverRegistered=true;return true;
        }catch(SecurityException error){setStatus("Autorizza i dispositivi nelle vicinanze");return false;}
    }
    public boolean isReady(){return ready;}
    public boolean isConnected(){return connected;}
    public boolean isBusy(){return busy;}
    public String getStatus(){return status;}
    public int[] getApiVersion(){return api.clone();}
    public Map<Integer,Integer> getCapabilities(){return new LinkedHashMap<>(capabilities);}
    public JSONObject getExerciseRaw(){try{return new JSONObject(exerciseRaw.toString());}catch(Exception e){return new JSONObject();}}

    public void configure(String ring,byte[] key) {
        byte[] copy=key==null?null:key.clone();
        main.post(()->{
            if(closed){wipe(copy);return;}
            if(ring==null || ring.isEmpty() || copy==null || copy.length!=16){desired=false;dropConnection(false);wipe(copy);synchronized(credentialsLock){wipe(ownerKey);ownerKey=null;}setStatus("Credenziale anello non ancora configurata");return;}
            boolean changed=logicalRing!=null && !logicalRing.equals(ring) || ownerKey!=null && !Arrays.equals(ownerKey,copy);
            if(changed){desired=false;dropConnection(false);}
            synchronized(credentialsLock){wipe(ownerKey);ownerKey=copy;}logicalRing=ring;
            if(!connected && !busy && preferences.getBoolean("desired."+ring,false)) {
                String saved=preferences.getString("address."+ring,null);
                if(saved!=null) connectMain(saved);
            }
        });
    }
    public void scan(){main.post(()->{
        if(closed || !permissions())return;
        if(!configured()){setStatus("Attendi l’associazione dell’anello da FamilOS");return;}
        if(adapter==null || !adapter.isEnabled()){setStatus("Attiva il Bluetooth sul telefono");return;}
        stopScan();devices.clear();listener.onDevices(Collections.emptyList());
        BluetoothLeScanner scanner=adapter.getBluetoothLeScanner();
        if(scanner==null){setStatus("Scanner Bluetooth non disponibile");return;}
        try{
            scanner.startScan(Arrays.asList(new ScanFilter.Builder().setServiceUuid(new ParcelUuid(OuraProtocol.SERVICE)).build(),new ScanFilter.Builder().setManufacturerData(690,new byte[0]).build()),new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(),scanCallback);
            scanning=true;setStatus("Ricerca anello…");main.postDelayed(stopScanRunnable,15000);
        }catch(SecurityException error){setStatus("Autorizza i dispositivi nelle vicinanze");}
    });}
    private final Runnable stopScanRunnable=this::stopScan;
    private void stopScan(){main.removeCallbacks(stopScanRunnable);if(!scanning)return;scanning=false;try{if(adapter!=null && adapter.getBluetoothLeScanner()!=null)adapter.getBluetoothLeScanner().stopScan(scanCallback);}catch(SecurityException ignored){}}
    private final ScanCallback scanCallback=new ScanCallback(){
        @Override public void onScanResult(int callbackType,ScanResult result){BluetoothDevice device=result.getDevice();main.post(()->{if(!scanning || closed)return;devices.put(device.getAddress(),device);listener.onDevices(Collections.unmodifiableList(new ArrayList<>(devices.values())));});}
        @Override public void onBatchScanResults(List<ScanResult> results){for(ScanResult result:results)onScanResult(0,result);}
        @Override public void onScanFailed(int errorCode){main.post(()->{scanning=false;setStatus("Ricerca Bluetooth non disponibile ("+errorCode+")");});}
    };
    private boolean permissions(){
        boolean allowed=Build.VERSION.SDK_INT>=31?context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)==PackageManager.PERMISSION_GRANTED && context.checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN)==PackageManager.PERMISSION_GRANTED:context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)==PackageManager.PERMISSION_GRANTED;
        if(!allowed)setStatus("Autorizza Bluetooth e ricerca dispositivi nelle impostazioni dell’app");
        return allowed;
    }
    private boolean configured(){return logicalRing!=null && ownerKey!=null && ownerKey.length==16;}
    public void connect(String address){main.post(()->connectMain(address));}
    private void connectMain(String target) {
        if(closed || !permissions())return;
        if(!configured()){setStatus("Credenziale anello non ancora configurata");return;}
        if(adapter==null || !adapter.isEnabled()){setStatus("Attiva il Bluetooth sul telefono");return;}
        if(!BluetoothAdapter.checkBluetoothAddress(target)){setStatus("Indirizzo anello non valido");return;}
        if(connected && target.equals(address) && (ready || busy))return;
        if(!ensureBondReceiver())return;
        desired=true;address=target;
        preferences.edit().putString("address."+logicalRing,target).putBoolean("desired."+logicalRing,true).apply();
        stopScan();cancelReconnect();dropConnection(false);busy=true;setStatus("Connessione…");
        try{
            BluetoothDevice device=adapter.getRemoteDevice(target);
            gatt=device.connectGatt(context,false,callback,BluetoothDevice.TRANSPORT_LE,BluetoothDevice.PHY_LE_1M_MASK,main);
            if(gatt==null){dropConnection(true);setStatus("Connessione Bluetooth non avviata");return;}
            armSetupTimeout(30000);
        }catch(SecurityException error){desired=false;dropConnection(false);setStatus("Autorizza il collegamento Bluetooth");}
    }
    private final BluetoothGattCallback callback=new BluetoothGattCallback(){
        @Override public void onConnectionStateChange(BluetoothGatt source,int result,int state){main.post(()->{
            if(source!=gatt || closed)return;
            if(result==BluetoothGatt.GATT_SUCCESS && state==BluetoothProfile.STATE_CONNECTED){connected=true;ready=false;setStatus("Verifica servizi anello…");if(!source.discoverServices())failLink("Ricerca servizi non avviata",true);}
            else if(state==BluetoothProfile.STATE_DISCONNECTED || result!=BluetoothGatt.GATT_SUCCESS){dropConnection(!bonding);setStatus(bonding?"Completa l’associazione Bluetooth richiesta da Android":"Anello scollegato");}
        });}
        @Override public void onServicesDiscovered(BluetoothGatt source,int result){main.post(()->{
            if(source!=gatt || closed)return;
            if(result!=BluetoothGatt.GATT_SUCCESS){if(security(result))requestBond();else failLink("Servizi anello non disponibili",true);return;}
            BluetoothGattService service=source.getService(OuraProtocol.SERVICE);
            if(service==null){failLink("Servizio Oura non disponibile",false);return;}
            write=service.getCharacteristic(OuraProtocol.WRITE);notify=service.getCharacteristic(OuraProtocol.NOTIFY);
            if(write==null || notify==null){failLink("Caratteristiche Oura mancanti",false);return;}
            subscribe();
        });}
        @Override public void onDescriptorWrite(BluetoothGatt source,BluetoothGattDescriptor descriptor,int result){main.post(()->{
            if(source!=gatt || closed || !OuraProtocol.CCCD.equals(descriptor.getUuid()) || !cccdPending)return;
            cccdPending=false;
            if(result==BluetoothGatt.GATT_SUCCESS){cancelSetupTimeout();startHandshake();}
            else if(security(result))requestBond();else failLink("Notifiche anello rifiutate",true);
        });}
        @Override public void onCharacteristicWrite(BluetoothGatt source,BluetoothGattCharacteristic characteristic,int result){main.post(()->{
            if(source!=gatt || closed || !OuraProtocol.WRITE.equals(characteristic.getUuid()))return;
            Pending request=pending;if(request==null)return;
            if(result==BluetoothGatt.GATT_SUCCESS)request.written.complete(null);
            else{request.fail(new OuraProtocol.ProtocolException("Scrittura anello rifiutata"));if(security(result))requestBond();else failLink("Scrittura Bluetooth rifiutata",true);}
        });}
        @Override public void onCharacteristicChanged(BluetoothGatt source,BluetoothGattCharacteristic characteristic,byte[] value){byte[] copy=value.clone();long at=System.currentTimeMillis();main.post(()->{if(source==gatt && OuraProtocol.NOTIFY.equals(characteristic.getUuid()))incoming(copy,at);});}
        @SuppressWarnings("deprecation") @Override public void onCharacteristicChanged(BluetoothGatt source,BluetoothGattCharacteristic characteristic){if(Build.VERSION.SDK_INT<33){byte[] value=characteristic.getValue();if(value!=null)onCharacteristicChanged(source,characteristic,value);}}
        @Override public void onCharacteristicRead(BluetoothGatt source,BluetoothGattCharacteristic characteristic,byte[] value,int result){main.post(()->{if(source!=gatt || closed)return;if(security(result))requestBond();else if(result==BluetoothGatt.GATT_SUCCESS && !cccdPending)subscribe();else if(result!=BluetoothGatt.GATT_SUCCESS)failLink("Lettura protetta non disponibile",true);});}
        @SuppressWarnings("deprecation") @Override public void onCharacteristicRead(BluetoothGatt source,BluetoothGattCharacteristic characteristic,int result){if(Build.VERSION.SDK_INT<33)onCharacteristicRead(source,characteristic,characteristic.getValue(),result);}
    };
    @SuppressWarnings("deprecation") private void subscribe(){
        if(gatt==null || notify==null)return;
        try{
            BluetoothGattDescriptor descriptor=notify.getDescriptor(OuraProtocol.CCCD);
            if(descriptor==null || !gatt.setCharacteristicNotification(notify,true)){failLink("Descrittore notifiche non disponibile",false);return;}
            cccdPending=true;boolean accepted;
            if(Build.VERSION.SDK_INT>=33)accepted=gatt.writeDescriptor(descriptor,BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)==0;
            else{descriptor.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);accepted=gatt.writeDescriptor(descriptor);}
            if(!accepted){cccdPending=false;failLink("Attivazione notifiche non avviata",true);}
        }catch(SecurityException error){failLink("Permesso Bluetooth mancante",false);}
    }
    private static boolean security(int result){return result==BluetoothGatt.GATT_INSUFFICIENT_AUTHENTICATION || result==BluetoothGatt.GATT_INSUFFICIENT_ENCRYPTION;}
    private void requestBond(){
        if(closed || gatt==null)return;
        cancelSetupTimeout();bonding=true;ready=false;failPending("Associazione Bluetooth richiesta");
        BluetoothDevice device=gatt.getDevice();
        try{
            int state=device.getBondState();
            if(state==BluetoothDevice.BOND_BONDED){bonding=false;failLink("Associazione protetta rifiutata: controlla il dispositivo nelle impostazioni Bluetooth",false);return;}
            if(state!=BluetoothDevice.BOND_BONDING && !device.createBond()){bonding=false;failLink("Associazione Bluetooth non avviata",false);return;}
            setStatus("Conferma l’associazione Bluetooth richiesta da Android");armSetupTimeout(90000);
        }catch(SecurityException error){bonding=false;failLink("Autorizza l’associazione Bluetooth",false);}
    }
    private final BroadcastReceiver bonds=new BroadcastReceiver(){
        @SuppressWarnings("deprecation") @Override public void onReceive(Context ignored,Intent intent){
            if(!BluetoothDevice.ACTION_BOND_STATE_CHANGED.equals(intent.getAction()))return;
            BluetoothDevice device=Build.VERSION.SDK_INT>=33?intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE,BluetoothDevice.class):intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
            int state=intent.getIntExtra(BluetoothDevice.EXTRA_BOND_STATE,BluetoothDevice.ERROR);
            main.post(()->{
                if(closed || !bonding || device==null || address==null || !address.equals(device.getAddress()))return;
                // A broadcast alone never authenticates the ring: reconnect and repeat owner auth.
                if(state==BluetoothDevice.BOND_BONDED){bonding=false;cancelSetupTimeout();dropConnection(false);main.postDelayed(()->{if(desired && !closed)connectMain(address);},750);}
                else if(state==BluetoothDevice.BOND_NONE){bonding=false;failLink("Associazione Bluetooth non completata",false);}
            });
        }
    };
    private void incoming(byte[] bytes,long at){
        if(closed)return;
        try{framer.feed(bytes,frame->{
            if(!OuraProtocol.sensitive(frame)){
                try{store.append("sensor_packet",payload(frame),at);listener.onArchiveChanged();}
                catch(Exception error){desired=false;persistIntent(false);failLink("Archivio non scrivibile: acquisizione fermata",false);throw new OuraProtocol.ProtocolException("Archivio non scrivibile");}
            }
            receive(frame,at);
        });maintenanceMain();}
        catch(Exception error){if(gatt!=null)failLink(message(error),false);}
    }
    private JSONObject payload(byte[] frame)throws Exception{
        JSONObject value=new JSONObject().put("raw_hex",OuraProtocol.hex(frame));JSONArray version=new JSONArray();for(int n:api)version.put(n);value.put("api_version",version);if(logicalRing!=null)value.put("logical_ring_id",logicalRing);return value;
    }
    private void receive(byte[] frame,long at)throws Exception{
        int tag=OuraProtocol.u(frame[0]);
        if(tag==0x2f && frame.length>=4 && OuraProtocol.u(frame[2])==0x28 && frame[3]==2){
            if(ready){OuraProtocol.Live value=OuraProtocol.live(frame,api);List<String>warnings=new ArrayList<>(value.warnings);double bpm=value.bpm;
                if(Double.isFinite(bpm) && !OuraProtocol.displaySupported(bpm)){warnings.add("Battito fuori intervallo di visualizzazione: pacchetto conservato");bpm=Double.NaN;}
                listener.onLive(bpm,at,Collections.unmodifiableList(warnings));}
            return;
        }
        if(pageRecords!=null && tag>=0x41){if(pageRecords.size()>=64)throw new OuraProtocol.ProtocolException("Troppi record nella pagina storica");pageRecords.add(frame);return;}
        Pending request=pending;
        if(request==null)return;
        if(tag==0x2f && frame.length>=3 && OuraProtocol.u(frame[2])==0x2f){request.fail(new OuraProtocol.ProtocolException("Autenticazione anello richiesta o rifiutata"));return;}
        if(tag==0x30 && frame.length==3){request.fail(new OuraProtocol.ProtocolException("Comando non supportato dal firmware"));return;}
        if(request.matches(frame))request.response.complete(frame);
    }
    private static final class Pending {
        final int tag,extended,generation;
        final CompletableFuture<Void>written=new CompletableFuture<>();
        final CompletableFuture<byte[]>response=new CompletableFuture<>();
        Pending(int tag,int extended,int generation){this.tag=tag;this.extended=extended;this.generation=generation;}
        boolean matches(byte[] frame){return frame.length>=2 && OuraProtocol.u(frame[0])==tag && (extended<0 || frame.length>=3 && OuraProtocol.u(frame[2])==extended);}
        void fail(Throwable error){written.completeExceptionally(error);response.completeExceptionally(error);}
    }
    @SuppressWarnings("deprecation") private byte[] exchange(byte[] request,int tag,int extended,int seconds)throws Exception{
        final int token=currentOperation();Pending operation=new Pending(tag,extended,token);byte[] outgoing=request.clone();
        main.post(()->{
            if(closed || token!=generation || !connected || gatt==null || write==null || pending!=null){operation.fail(new OuraProtocol.ProtocolException("Anello occupato o scollegato"));return;}
            pending=operation;
            try{
                boolean accepted;
                if(Build.VERSION.SDK_INT>=33)accepted=gatt.writeCharacteristic(write,outgoing,BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)==0;
                else{write.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);write.setValue(outgoing);accepted=gatt.writeCharacteristic(write);}
                if(!accepted)operation.fail(new OuraProtocol.ProtocolException("Scrittura Bluetooth non avviata"));
            }catch(SecurityException error){operation.fail(new OuraProtocol.ProtocolException("Permesso Bluetooth mancante"));}
        });
        long deadline=SystemClock.elapsedRealtime()+seconds*1000L;
        try{
            operation.written.get(seconds,TimeUnit.SECONDS);
            long remaining=deadline-SystemClock.elapsedRealtime();if(remaining<=0)throw new TimeoutException();
            byte[] reply=operation.response.get(remaining,TimeUnit.MILLISECONDS);currentOperation();return reply;
        }catch(TimeoutException error){main.post(()->{if(token==generation)failLink("Conferma anello non ricevuta: riconnessione",true);});throw new OuraProtocol.ProtocolException("Conferma anello non ricevuta");}
        catch(java.util.concurrent.ExecutionException error){Throwable cause=error.getCause();if(cause instanceof Exception)throw(Exception)cause;throw new OuraProtocol.ProtocolException("Operazione anello interrotta");}
        finally{main.post(()->{if(pending==operation)pending=null;wipe(outgoing);});}
    }
    private int currentOperation(){Integer token=operationGeneration.get();if(token==null || token!=generation || closed)throw new OuraProtocol.ProtocolException("Operazione anello superata dalla nuova connessione");return token;}
    private byte[] exchange(byte[] request,int tag,int extended)throws Exception{return exchange(request,tag,extended,15);}
    private interface Operation {void run()throws Exception;}
    private void startOperation(Operation operation,boolean handshake){
        if(closed || busy && !handshake || !connected)return;
        busy=true;int token=generation;
        worker.execute(()->{
            operationGeneration.set(token);
            try{currentOperation();operation.run();}
            catch(Exception error){main.post(()->{if(token==generation && !closed && !bonding)setStatus(message(error));});}
            finally{operationGeneration.remove();main.post(()->{if(token==generation){busy=false;maintenanceMain();}});}
        });
    }
    private void startHandshake(){startOperation(()->{
        updateStatus("Lettura firmware…");api=OuraProtocol.firmware(exchange(OuraProtocol.bytes(8,3,0,0,0),9,-1));
        Map<Integer,Integer> found=new LinkedHashMap<>();int total=-1;
        for(int page=0;page<16;page++){
            OuraProtocol.Capabilities reply=OuraProtocol.capabilities(exchange(OuraProtocol.bytes(0x2f,2,1,page),0x2f,2));
            if(total!=-1 && total!=reply.pages)throw new OuraProtocol.ProtocolException("Inventario capacità cambiato");total=reply.pages;
            for(Map.Entry<Integer,Integer> entry:reply.features.entrySet()){Integer previous=found.put(entry.getKey(),entry.getValue());if(previous!=null && !previous.equals(entry.getValue()))throw new OuraProtocol.ProtocolException("Versioni capacità incoerenti");}
            if(page+1>=total)break;
        }
        capabilities=Collections.unmodifiableMap(found);
        byte[] key;synchronized(credentialsLock){key=ownerKey==null?null:ownerKey.clone();}
        try{
            updateStatus("Autenticazione del proprietario…");
            byte[] nonce=OuraProtocol.complete(exchange(OuraProtocol.bytes(0x2f,1,0x2b),0x2f,0x2c),18,0x2f,0x2c);
            if(nonce.length!=18)throw new OuraProtocol.ProtocolException("Nonce anello non valido");
            byte[] encrypted=OuraProtocol.encryptNonce(key,Arrays.copyOfRange(nonce,3,18));
            byte[] answer=OuraProtocol.complete(exchange(OuraProtocol.concat(OuraProtocol.bytes(0x2f,17,0x2d),encrypted),0x2f,0x2e),4,0x2f,0x2e);
            wipe(encrypted);wipe(nonce);
            if(answer[3]!=0)throw new OuraProtocol.ProtocolException("Credenziale anello rifiutata; nessun reset eseguito");
        }finally{wipe(key);}
        String controlKey=sportKey();long control=store.getLong(controlKey,-1);
        if(control>=200){nextSportAt=SystemClock.elapsedRealtime()+60000;try{restoreSport(control,controlKey);}catch(Exception error){updateStatus("Arresto cardio da confermare");}}
        if(!found.containsKey(2))throw new OuraProtocol.ProtocolException("Battito live non pubblicizzato dal firmware");
        OuraProtocol.ack(exchange(OuraProtocol.bytes(0x2f,3,0x22,2,3),0x2f,0x23),0x23,2);
        OuraProtocol.ack(exchange(OuraProtocol.bytes(0x2f,3,0x26,2,2),0x2f,0x27),0x27,2);
        ready=true;updateStatus("Collegato · attendi un segnale valido");
        nextHistoryAt=SystemClock.elapsedRealtime()+60000;
        boolean more=historyPages(32);nextHistoryAt=SystemClock.elapsedRealtime()+(more?2000:60000);
    },true);}
    private boolean historyPages(int maxPages)throws Exception{
        String ring=logicalRing;if(ring==null)throw new OuraProtocol.ProtocolException("Anello non configurato");
        int[] historyApi=api.clone();
        long cursor=store.getLong("history.cursor."+ring,0);
        for(int page=0;page<maxPages;page++){
            int token=currentOperation();
            CompletableFuture<Void> prepared=new CompletableFuture<>();main.post(()->{if(token!=generation){prepared.completeExceptionally(new OuraProtocol.ProtocolException("Connessione storica interrotta"));return;}pageRecords=new ArrayList<>();prepared.complete(null);});prepared.get(5,TimeUnit.SECONDS);
            byte[] summary;
            try{summary=exchange(OuraProtocol.historyRequest(cursor),0x11,-1,25);}catch(Exception error){main.post(()->{if(token==generation)pageRecords=null;});throw error;}
            CompletableFuture<List<byte[]>> collected=new CompletableFuture<>();main.post(()->{if(token!=generation){collected.completeExceptionally(new OuraProtocol.ProtocolException("Connessione storica interrotta"));return;}List<byte[]> records=pageRecords;pageRecords=null;collected.complete(records==null?Collections.emptyList():records);});
            List<byte[]> records=collected.get(5,TimeUnit.SECONDS);
            OuraProtocol.HistoryPage checked=OuraProtocol.historyPage(summary,records,cursor);
            // This single Store transaction owns both journal insertion and cursor commit.
            currentOperation();store.archiveHistory(records,checked.nextCursor,ring,historyApi);cursor=checked.nextCursor;
            main.post(()->{if(!closed)listener.onArchiveChanged();});
            if(checked.bytesLeft==0 || records.isEmpty()){updateStatus("Collegato · storico aggiornato");return false;}
        }
        updateStatus("Collegato · recupero dello storico in corso");return true;
    }
    public void maintenance(){main.post(this::maintenanceMain);}
    private void maintenanceMain(){
        if(closed || !ready || !connected || busy)return;
        String controlKey=sportKey();long now=SystemClock.elapsedRealtime(),control=store.getLong(controlKey,-1);
        boolean stopDue=control>=200 && now>=nextSportAt;
        boolean historyDue=now>=nextHistoryAt,exerciseDue=now>=nextExerciseAt && capabilities.containsKey(3);
        if(!stopDue && !historyDue && !exerciseDue)return;
        if(stopDue)nextSportAt=now+60000;
        if(historyDue)nextHistoryAt=now+60000;
        if(exerciseDue)nextExerciseAt=now+60000;
        startOperation(()->{
            if(stopDue)try{restoreSport(control,controlKey);}catch(Exception error){updateStatus("Arresto cardio da confermare");}
            if(exerciseDue)try{exerciseRead();}catch(Exception error){updateStatus("Lettura cardio attività rinviata");}
            if(historyDue){boolean more=historyPages(32);nextHistoryAt=SystemClock.elapsedRealtime()+(more?2000:60000);}
        },false);
    }
    private void exerciseRead()throws Exception{
        byte[] state=exchange(OuraProtocol.bytes(0x2f,2,0x20,3),0x2f,0x21);
        byte[] latest=exchange(OuraProtocol.bytes(0x2f,2,0x24,3),0x2f,0x25);
        OuraProtocol.complete(latest,5,0x2f,0x25);if(latest[3]!=3)throw new OuraProtocol.ProtocolException("Risposta cardio attività inattesa");
        JSONObject values=new JSONObject().put("status_raw_hex",OuraProtocol.hex(state)).put("latest_raw_hex",OuraProtocol.hex(latest)).put("validated_live",false);
        if(latest.length>=17 && latest[4]==0){values.put("heart_rate_value_raw",OuraProtocol.u(latest[13]));values.put("quality_raw",OuraProtocol.u(latest[9]));}
        exerciseRaw=values;
    }
    private String sportKey(){return "sport.control."+logicalRing;}
    public void startSport(int id,int age,int rhr){main.post(()->{
        if(!ready || busy || !capabilities.containsKey(3)){setStatus("Anello non pronto per la registrazione cardio");return;}
        final byte[] params;try{params=OuraProtocol.sportParameters(id,age,rhr);}catch(Exception error){setStatus(message(error));return;}
        final String controlKey=sportKey();
        startOperation(()->{
            int restore=OuraProtocol.sportRestoreMode(exchange(OuraProtocol.bytes(0x2f,2,0x20,3),0x2f,0x21));
            store.setLong(controlKey,200+restore);
            try{
                OuraProtocol.ack(exchange(OuraProtocol.bytes(0x2f,3,0x22,3,0),0x2f,0x23),0x23,3);
                OuraProtocol.ack(exchange(params,0x2f,0x2a),0x2a,3);
                OuraProtocol.ack(exchange(OuraProtocol.bytes(0x2f,3,0x22,3,2),0x2f,0x23),0x23,3);
                store.setLong(controlKey,100+restore);updateStatus("Registrazione cardio attività richiesta");
            }catch(Exception error){nextSportAt=SystemClock.elapsedRealtime()+60000;try{restoreSport(200+restore,controlKey);}catch(Exception ignored){}throw error;}
        },false);
    });}
    public void stopSport(){main.post(()->{
        try{long control=store.getLong(sportKey(),-1);if(control<100)return;store.setLong(sportKey(),200+control%100);nextSportAt=0;maintenanceMain();}
        catch(Exception error){setStatus("Arresto cardio non salvato: riprova");}
    });}
    private void restoreSport(long control,String controlKey)throws Exception{
        int restore=(int)(control%100);if(restore!=0 && restore!=1)throw new OuraProtocol.ProtocolException("Stato ripristino cardio non valido");
        OuraProtocol.ack(exchange(OuraProtocol.bytes(0x2f,3,0x22,3,0),0x2f,0x23),0x23,3);
        if(restore==1)OuraProtocol.ack(exchange(OuraProtocol.bytes(0x2f,3,0x22,3,1),0x2f,0x23),0x23,3);
        store.setLong(controlKey,-1);
    }
    public void disconnect(){main.post(()->{
        desired=false;persistIntent(false);cancelReconnect();stopScan();
        if(ready && !busy){startOperation(()->{
            try{OuraProtocol.ack(exchange(OuraProtocol.bytes(0x2f,3,0x22,2,1),0x2f,0x23,3),0x23,2);OuraProtocol.ack(exchange(OuraProtocol.bytes(0x2f,3,0x26,2,0),0x2f,0x27,3),0x27,2);}
            finally{main.post(()->{dropConnection(false);setStatus("Anello scollegato");});}
        },false);}else{dropConnection(false);setStatus("Anello scollegato");}
    });}
    private void persistIntent(boolean value){if(logicalRing!=null)preferences.edit().putBoolean("desired."+logicalRing,value).apply();}
    private void armSetupTimeout(long millis){cancelSetupTimeout();int token=generation;setupTimeout=()->{if(token==generation)failLink("Collegamento protetto non completato",!bonding);};main.postDelayed(setupTimeout,millis);}
    private void cancelSetupTimeout(){if(setupTimeout!=null){main.removeCallbacks(setupTimeout);setupTimeout=null;}}
    private void failLink(String text,boolean retry){dropConnection(retry);setStatus(text);}
    private void failPending(String text){Pending old=pending;pending=null;if(old!=null)old.fail(new OuraProtocol.ProtocolException(text));}
    private void dropConnection(boolean retry){
        generation++;cancelSetupTimeout();failPending("Collegamento anello interrotto");connected=false;ready=false;busy=false;cccdPending=false;write=null;notify=null;pageRecords=null;api=new int[0];capabilities=Collections.emptyMap();exerciseRaw=new JSONObject();framer.reset();
        BluetoothGatt old=gatt;gatt=null;
        if(old!=null){try{old.disconnect();}catch(SecurityException ignored){}old.close();}
        if(retry && desired && !closed && !bonding && address!=null){cancelReconnect();reconnect=()->{if(desired && !closed)connectMain(address);};main.postDelayed(reconnect,5000);}
    }
    private void cancelReconnect(){if(reconnect!=null){main.removeCallbacks(reconnect);reconnect=null;}}
    private void setStatus(String value){status=value;if(!closed)listener.onStatus(value);}
    private void updateStatus(String value){Integer active=operationGeneration.get();int token=active==null?generation:active;main.post(()->{if(token==generation && !closed)setStatus(value);});}
    private static String message(Exception error){return error instanceof OuraProtocol.ProtocolException?error.getMessage():"Operazione anello non completata";}
    private static void wipe(byte[] data){if(data!=null)Arrays.fill(data,(byte)0);}
    @Override public void close(){main.post(()->{
        if(closed)return;closed=true;desired=false;cancelReconnect();stopScan();dropConnection(false);synchronized(credentialsLock){wipe(ownerKey);ownerKey=null;}
        if(receiverRegistered){try{context.unregisterReceiver(bonds);}catch(IllegalArgumentException ignored){}receiverRegistered=false;}
        worker.shutdownNow();
    });}
}
