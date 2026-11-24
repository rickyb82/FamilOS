package com.familos.homepulse;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Private durable journal. ACK removes only delivery references, never sensor data. */
public final class Store extends SQLiteOpenHelper {
    private final String credentialAlias;

    public Store(Context context) { this(context,"homepulse.db"); }
    public Store(Context context,String databaseName) { super(context.getApplicationContext(), safeName(databaseName), null, 1);credentialAlias="com.familos.homepulse.credentials.v1."+databaseName;setWriteAheadLoggingEnabled(true);getWritableDatabase(); }
    private static String safeName(String value) { if(value==null||!value.matches("[A-Za-z0-9_.-]{1,100}")||value.equals(".")||value.equals(".."))throw new IllegalArgumentException("Nome archivio non valido");return value; }
    public String keyAlias() { return credentialAlias; }
    @Override public void onConfigure(SQLiteDatabase db) { db.setForeignKeyConstraintsEnabled(true); db.execSQL("PRAGMA synchronous=FULL"); }
    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE journal(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL,captured_at REAL NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL)");
        db.execSQL("CREATE TABLE outbox(event_id TEXT PRIMARY KEY REFERENCES journal(event_id),batch_id TEXT)");
        db.execSQL("CREATE TABLE batches(batch_id TEXT PRIMARY KEY,body TEXT NOT NULL)");
        db.execSQL("CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL)");
        db.execSQL("CREATE TABLE secrets(key TEXT PRIMARY KEY,value BLOB NOT NULL)");
        db.execSQL("CREATE TABLE sessions(id TEXT PRIMARY KEY,started REAL NOT NULL,body TEXT NOT NULL)");
        db.execSQL("CREATE TABLE history_records(digest TEXT PRIMARY KEY,ring TEXT NOT NULL,raw BLOB NOT NULL,received REAL NOT NULL)");
        db.execSQL("CREATE TABLE readings(id INTEGER PRIMARY KEY AUTOINCREMENT,at REAL NOT NULL,bpm REAL NOT NULL,warnings TEXT NOT NULL,flags_raw INTEGER,ibi_validity_raw INTEGER)");
        db.execSQL("CREATE TRIGGER journal_no_delete BEFORE DELETE ON journal BEGIN SELECT RAISE(ABORT,'journal is append-only'); END");
        db.execSQL("CREATE TRIGGER journal_no_update BEFORE UPDATE ON journal BEGIN SELECT RAISE(ABORT,'journal is append-only'); END");
    }
    @Override public void onUpgrade(SQLiteDatabase db,int oldVersion,int newVersion) { throw new IllegalStateException("Archivio locale da aggiornare senza eliminare i dati"); }
    public static JSONObject copy(JSONObject value) { try { return value == null ? new JSONObject() : new JSONObject(value.toString()); } catch(JSONException e) { throw new IllegalArgumentException("JSON non valido"); } }
    public static JSONArray copy(JSONArray value) { try { return value == null ? new JSONArray() : new JSONArray(value.toString()); } catch(JSONException e) { throw new IllegalArgumentException("JSON non valido"); } }
    public static JSONObject object(Object... pairs) { JSONObject result=new JSONObject(); try { for(int i=0;i<pairs.length;i+=2) result.put((String)pairs[i],pairs[i+1]==null?JSONObject.NULL:pairs[i+1]); return result; } catch(JSONException e) { throw new IllegalArgumentException("JSON non valido"); } }
    static IllegalStateException failure(Exception e) { return new IllegalStateException("Archivio locale non disponibile; dati non eliminati", e); }
    private static String text(Cursor cursor,int index) { return cursor.getString(index); }
    public static String hex(byte[] bytes) { StringBuilder result=new StringBuilder(bytes.length*2); for(byte value:bytes) result.append(String.format(java.util.Locale.ROOT,"%02x",value&255)); return result.toString(); }
    public static byte[] unhex(String text) { if(text==null || (text.length()&1)!=0) throw new IllegalArgumentException("Formato esadecimale non valido"); byte[] result=new byte[text.length()/2]; for(int i=0;i<result.length;i++){int a=Character.digit(text.charAt(i*2),16),b=Character.digit(text.charAt(i*2+1),16);if(a<0||b<0)throw new IllegalArgumentException("Formato esadecimale non valido");result[i]=(byte)((a<<4)|b);}return result; }
    private static String digest(String ring,byte[] raw) { try { MessageDigest hash=MessageDigest.getInstance("SHA-256");hash.update(ring.getBytes(StandardCharsets.UTF_8));hash.update((byte)0);return hex(hash.digest(raw)); } catch(Exception e){throw failure(e);} }
    private static void sensorOnly(byte[] raw) { if(raw.length<1||raw.length>4096)throw new IllegalArgumentException("Pacchetto sensore non valido");int tag=raw[0]&255; if(tag==0x24||tag==0x25||(raw.length>=3&&tag==0x2f&&(raw[2]&255)>=0x2b&&(raw[2]&255)<=0x2f))throw new IllegalArgumentException("Autenticazione esclusa dal journal sensori"); }
    private static boolean secretName(String name) { return name.equals("access_token")||name.equals("owner_key_hex")||name.equals("auth_key")||name.equals("pairing_code")||name.equals("owner_key")||name.equals("key_hex"); }
    private static boolean containsSecret(Object input) { if(input instanceof JSONObject){JSONObject object=(JSONObject)input;for(Iterator<String> keys=object.keys();keys.hasNext();){String key=keys.next();if(secretName(key)||containsSecret(object.opt(key)))return true;}}else if(input instanceof JSONArray){JSONArray array=(JSONArray)input;for(int i=0;i<array.length();i++)if(containsSecret(array.opt(i)))return true;}return false; }
    public static JSONObject redacted(JSONObject input) { JSONObject result=new JSONObject(); try { for(Iterator<String> keys=input.keys();keys.hasNext();){String key=keys.next();if(secretName(key))continue;Object value=input.get(key); if(value instanceof JSONObject)value=redacted((JSONObject)value);else if(value instanceof JSONArray)value=redactArray((JSONArray)value);result.put(key,value);}return result;}catch(JSONException e){throw new IllegalArgumentException("JSON non valido");} }
    private static JSONArray redactArray(JSONArray input)throws JSONException { JSONArray result=new JSONArray();for(int i=0;i<input.length();i++){Object value=input.get(i);if(value instanceof JSONObject)value=redacted((JSONObject)value);else if(value instanceof JSONArray)value=redactArray((JSONArray)value);result.put(value);}return result; }
    private JSONObject insert(SQLiteDatabase db,String kind,JSONObject payload,long at,String id) {
        if(kind==null||kind.isEmpty()||kind.length()>200||at<0)throw new IllegalArgumentException("Evento non valido");
        if(kind.equals("sensor_packet")||kind.equals("ring_history"))sensorOnly(unhex(payload.optString("raw_hex","")));
        if(containsSecret(payload))throw new IllegalArgumentException("Credenziali escluse dal journal");
        ContentValues value=new ContentValues();value.put("event_id",id);value.put("captured_at",at/1000.0);value.put("kind",kind);value.put("payload",payload.toString());long sequence=db.insertOrThrow("journal",null,value);
        ContentValues delivery=new ContentValues();delivery.put("event_id",id);db.insertOrThrow("outbox",null,delivery);
        return object("event_id",id,"sequence",sequence,"captured_at",at/1000.0,"kind",kind,"payload",copy(payload));
    }
    public synchronized JSONObject append(String kind,JSONObject payload,long capturedAtMillis) { SQLiteDatabase db=getWritableDatabase();db.beginTransaction();try {JSONObject result=insert(db,kind,copy(payload),capturedAtMillis,UUID.randomUUID().toString());db.setTransactionSuccessful();return result;}finally{db.endTransaction();} }
    public synchronized JSONObject append(String kind,JSONObject payload) { return append(kind,payload,System.currentTimeMillis()); }
    private static void put(SQLiteDatabase db,String key,String value) { ContentValues row=new ContentValues();row.put("key",key);row.put("value",value);replace(db,"kv",row); }
    private static void replace(SQLiteDatabase db,String table,ContentValues row) { if(db.insertWithOnConflict(table,null,row,SQLiteDatabase.CONFLICT_REPLACE)==-1)throw new IllegalStateException("Scrittura archivio non riuscita; dati precedenti conservati"); }
    private String getValue(String key) { try(Cursor cursor=getReadableDatabase().rawQuery("SELECT value FROM kv WHERE key=?",new String[]{key})){return cursor.moveToFirst()?cursor.getString(0):null;} }
    public synchronized long getLong(String key,long fallback) { String value=getValue(key);try{return value==null?fallback:Long.parseLong(value);}catch(NumberFormatException e){return fallback;} }
    public synchronized void setLong(String key,long value) { put(getWritableDatabase(),key,Long.toString(value)); }
    public synchronized JSONObject getJson(String key,JSONObject fallback) { String value=getValue(key);if(value==null)return copy(fallback);try{return new JSONObject(value);}catch(JSONException e){throw failure(e);} }
    public synchronized void setJson(String key,JSONObject value) { if(value==null){getWritableDatabase().delete("kv","key=?",new String[]{key});return;}put(getWritableDatabase(),key,redacted(value).toString()); }
    public synchronized JSONObject settings() { return getJson("settings",object("profile",new JSONObject(),"config",object("work",40,"rest",20,"rounds",8,"plan",new JSONArray()),"weekly_schedule",new JSONArray(),"auto_detection",object("enabled",false))); }
    public synchronized void saveSettings(JSONObject settings) { setJson("settings",settings); }
    private int count(String sql) { try(Cursor c=getReadableDatabase().rawQuery(sql,null)){return c.moveToFirst()?c.getInt(0):0;} }
    public synchronized int pendingCount() { return count("SELECT count(*) FROM outbox"); }
    public synchronized int journalCount() { return count("SELECT count(*) FROM journal"); }
    public synchronized int pendingWorkoutCount() { return count("SELECT count(*) FROM outbox o JOIN journal j USING(event_id) WHERE j.kind='workout'"); }
    public synchronized JSONObject prepareBatch(int limit) { SQLiteDatabase db=getWritableDatabase();db.beginTransaction();try{
        try(Cursor old=db.rawQuery("SELECT body FROM batches ORDER BY rowid LIMIT 1",null)){if(old.moveToFirst()){JSONObject result=new JSONObject(old.getString(0));db.setTransactionSuccessful();return result;}}
        JSONArray events=new JSONArray();try(Cursor rows=db.rawQuery("SELECT j.event_id,j.sequence,j.captured_at,j.kind,j.payload FROM journal j JOIN outbox o USING(event_id) WHERE o.batch_id IS NULL ORDER BY j.sequence LIMIT ?",new String[]{Integer.toString(Math.max(1,Math.min(500,limit)))})){while(rows.moveToNext())events.put(object("event_id",text(rows,0),"sequence",rows.getLong(1),"captured_at",rows.getDouble(2),"kind",text(rows,3),"payload",new JSONObject(text(rows,4))));}
        if(events.length()==0){db.setTransactionSuccessful();return null;}String id=UUID.randomUUID().toString();JSONObject batch=object("batch_id",id,"events",events);ContentValues stored=new ContentValues();stored.put("batch_id",id);stored.put("body",batch.toString());db.insertOrThrow("batches",null,stored);
        ContentValues marked=new ContentValues();marked.put("batch_id",id);for(int i=0;i<events.length();i++)db.update("outbox",marked,"event_id=?",new String[]{events.getJSONObject(i).getString("event_id")});db.setTransactionSuccessful();return batch;
    }catch(JSONException e){throw failure(e);}finally{db.endTransaction();} }
    public synchronized void acknowledge(String batchID,Set<String> acceptedIDs) { SQLiteDatabase db=getWritableDatabase();db.beginTransaction();try{
        JSONObject batch;try(Cursor row=db.rawQuery("SELECT body FROM batches WHERE batch_id=?",new String[]{batchID})){if(!row.moveToFirst()){db.setTransactionSuccessful();return;}batch=new JSONObject(row.getString(0));}
        Set<String> expected=new HashSet<>();JSONArray events=batch.getJSONArray("events");for(int i=0;i<events.length();i++)expected.add(events.getJSONObject(i).getString("event_id"));if(acceptedIDs==null||!expected.containsAll(acceptedIDs))throw new IllegalArgumentException("Conferma server incoerente: coda conservata");
        for(String id:acceptedIDs)db.delete("outbox","event_id=? AND batch_id=?",new String[]{id,batchID});ContentValues reset=new ContentValues();reset.putNull("batch_id");db.update("outbox",reset,"batch_id=?",new String[]{batchID});db.delete("batches","batch_id=?",new String[]{batchID});db.setTransactionSuccessful();
    }catch(JSONException e){throw failure(e);}finally{db.endTransaction();} }
    public synchronized int archiveHistory(List<byte[]> frames,long nextCursor,String logicalRing,int[] api) { if(logicalRing==null||logicalRing.isEmpty()||nextCursor<0||nextCursor>0xffffffffL)throw new IllegalArgumentException("Identità o cursore ring non validi");SQLiteDatabase db=getWritableDatabase();db.beginTransaction();int added=0;try{
        long now=System.currentTimeMillis();JSONArray version=new JSONArray();if(api!=null)for(int value:api)version.put(value);
        for(byte[] frame:frames){sensorOnly(frame);if((frame[0]&255)<0x41)throw new IllegalArgumentException("Record storico non valido");String hash=digest(logicalRing,frame);ContentValues record=new ContentValues();record.put("digest",hash);record.put("ring",logicalRing);record.put("raw",frame);record.put("received",now/1000.0);boolean duplicate;try(Cursor existing=db.rawQuery("SELECT 1 FROM history_records WHERE digest=?",new String[]{hash})){duplicate=existing.moveToFirst();}if(duplicate)continue;db.insertOrThrow("history_records",null,record);
            insert(db,"ring_history",object("raw_hex",hex(frame),"api_version",version,"logical_ring_id",logicalRing),now,UUID.nameUUIDFromBytes(hash.getBytes(StandardCharsets.UTF_8)).toString());added++;
        }put(db,"history.cursor."+logicalRing,Long.toString(nextCursor));db.setTransactionSuccessful();return added;
    }finally{db.endTransaction();} }
    public synchronized void addReading(long ms,double bpm,List<String> warnings) { addReading(ms,bpm,warnings,null,null); }
    public synchronized void addReading(long ms,double bpm,List<String> warnings,Integer flags,Integer validity) { if(ms<0||!Double.isFinite(bpm)||bpm<=0)throw new IllegalArgumentException("Lettura non valida");ContentValues value=new ContentValues();value.put("at",ms/1000.0);value.put("bpm",bpm);value.put("warnings",new JSONArray(warnings==null?java.util.Collections.emptyList():warnings).toString());if(flags!=null)value.put("flags_raw",flags);if(validity!=null)value.put("ibi_validity_raw",validity);getWritableDatabase().insertOrThrow("readings",null,value); }
    public synchronized JSONArray recentReadings() { return readings(1000); }
    public synchronized JSONArray readings(int limit) { JSONArray result=new JSONArray();try(Cursor rows=getReadableDatabase().rawQuery("SELECT at,bpm,warnings,flags_raw,ibi_validity_raw FROM (SELECT id,at,bpm,warnings,flags_raw,ibi_validity_raw FROM readings ORDER BY id DESC LIMIT ?) ORDER BY id",new String[]{Integer.toString(Math.max(1,Math.min(10000,limit)))})){while(rows.moveToNext())result.put(object("t",rows.getDouble(0),"v",rows.getDouble(1),"warnings",new JSONArray(rows.getString(2)),"flags_raw",rows.isNull(3)?null:rows.getInt(3),"ibi_validity_raw",rows.isNull(4)?null:rows.getInt(4)));}catch(JSONException e){throw failure(e);}return result; }
    public synchronized JSONObject currentSession() { String value=getValue("activeWorkout");if(value==null)return null;try{return new JSONObject(value);}catch(JSONException e){throw failure(e);} }
    public synchronized JSONArray sessions() { JSONArray result=new JSONArray();try(Cursor rows=getReadableDatabase().rawQuery("SELECT body FROM sessions ORDER BY started DESC LIMIT 500",null)){while(rows.moveToNext())result.put(new JSONObject(rows.getString(0)));}catch(JSONException e){throw failure(e);}return result; }
    private void saveSession(SQLiteDatabase db,JSONObject session) { ContentValues value=new ContentValues();value.put("id",session.optString("id"));value.put("started",session.optDouble("started_at",0));value.put("body",redacted(session).toString());replace(db,"sessions",value); }
    public synchronized void commitWorkout(JSONObject session,JSONObject payload) { SQLiteDatabase db=getWritableDatabase();db.beginTransaction();try{saveSession(db,session);String status=session.optString("status");JSONObject current=currentSession();boolean same=current==null||current.optString("id").equals(session.optString("id"));if(same&&(status.equals("active")||status.equals("paused")))put(db,"activeWorkout",session.toString());else if(current!=null&&same)db.delete("kv","key=?",new String[]{"activeWorkout"});insert(db,"workout",payload,System.currentTimeMillis(),UUID.randomUUID().toString());db.setTransactionSuccessful();}finally{db.endTransaction();} }
    public synchronized void reconcileSessions(JSONArray remote,JSONObject active) { SQLiteDatabase db=getWritableDatabase();db.beginTransaction();try{if(pendingWorkoutCount()!=0){db.setTransactionSuccessful();return;}JSONObject current=currentSession();boolean explicit=false;for(int i=0;i<remote.length();i++){JSONObject session=remote.optJSONObject(i);if(session==null)continue;saveSession(db,session);if(current!=null&&session.optString("id").equals(current.optString("id")))explicit=true;}if(active!=null)put(db,"activeWorkout",active.toString());else if(explicit)db.delete("kv","key=?",new String[]{"activeWorkout"});db.setTransactionSuccessful();}finally{db.endTransaction();} }
    private SecretKey key(boolean create)throws Exception { KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);if(store.containsAlias(credentialAlias))return (SecretKey)store.getKey(credentialAlias,null);if(!create)throw new IllegalStateException("Chiave protetta non disponibile");KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");generator.init(new KeyGenParameterSpec.Builder(credentialAlias,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setRandomizedEncryptionRequired(true).setUserAuthenticationRequired(false).build());return generator.generateKey(); }
    public synchronized void setSecret(String name,JSONObject value) { if(value==null){getWritableDatabase().delete("secrets","key=?",new String[]{name});return;}try{Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key(true));cipher.updateAAD(name.getBytes(StandardCharsets.UTF_8));byte[] encrypted=cipher.doFinal(value.toString().getBytes(StandardCharsets.UTF_8)),iv=cipher.getIV();byte[] packed=new byte[2+iv.length+encrypted.length];packed[0]=1;packed[1]=(byte)iv.length;System.arraycopy(iv,0,packed,2,iv.length);System.arraycopy(encrypted,0,packed,2+iv.length,encrypted.length);ContentValues row=new ContentValues();row.put("key",name);row.put("value",packed);replace(getWritableDatabase(),"secrets",row);}catch(Exception e){throw new IllegalStateException("Salvataggio protetto non riuscito",e);} }
    public synchronized JSONObject getSecret(String name) { try(Cursor row=getReadableDatabase().rawQuery("SELECT value FROM secrets WHERE key=?",new String[]{name})){if(!row.moveToFirst())return null;byte[] packed=row.getBlob(0);if(packed.length<31||packed[0]!=1||(packed[1]&255)!=12)throw new IllegalStateException("Credenziali protette non valide");Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key(false),new GCMParameterSpec(128,java.util.Arrays.copyOfRange(packed,2,14)));cipher.updateAAD(name.getBytes(StandardCharsets.UTF_8));return new JSONObject(new String(cipher.doFinal(java.util.Arrays.copyOfRange(packed,14,packed.length)),StandardCharsets.UTF_8));}catch(Exception e){throw new IllegalStateException("Credenziali protette non disponibili; archivio conservato",e);} }
}
