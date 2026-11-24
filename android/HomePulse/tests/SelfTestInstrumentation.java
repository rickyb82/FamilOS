package com.familos.homepulse;
import android.app.Instrumentation;
import android.os.Bundle;
import android.database.Cursor;
import java.util.*;
import java.security.KeyStore;
import org.json.*;

/** Real SQLite and Keystore tests. A unique database prevents writes to user data. */
public final class SelfTestInstrumentation extends Instrumentation {
 private int assertions;
 private void check(boolean ok,String name){assertions++;if(!ok)throw new AssertionError(name);}
 @Override public void onCreate(Bundle args){super.onCreate(args);start();}
 @Override public void onStart(){
  Bundle result=new Bundle();Store store=null;String name="test-"+UUID.randomUUID()+".db",alias=null;
  try{
   store=new Store(getTargetContext(),name);alias=store.keyAlias();
   JSONObject first=store.append("test",Store.object("value",1),1700000000123L);
   store.append("test",Store.object("value",2),1700000001123L);
   JSONObject batch=store.prepareBatch(500);String id=batch.getString("batch_id");
   check(batch.getJSONArray("events").length()==2,"batch includes both events");
   check(Math.abs(first.getDouble("captured_at")-1700000000.123)<0.001,"timestamp retains milliseconds");
   store.close();store=new Store(getTargetContext(),name);
   check(store.prepareBatch(500).toString().equals(batch.toString()),"in-flight batch survives reopen unchanged");
   boolean refused=false;try{store.acknowledge(id,Collections.singleton("unknown"));}catch(IllegalArgumentException e){refused=true;}
   check(refused&&store.pendingCount()==2,"unknown ACK keeps outbox");
   store.acknowledge(id,Collections.singleton(first.getString("event_id")));
   check(store.pendingCount()==1&&store.journalCount()==2,"partial ACK retains unacknowledged and raw");
   JSONObject retry=store.prepareBatch(500);check(retry.getJSONArray("events").length()==1,"retry contains only pending event");
   store.acknowledge(retry.getString("batch_id"),Collections.singleton(retry.getJSONArray("events").getJSONObject(0).getString("event_id")));
   check(store.pendingCount()==0&&store.journalCount()==2,"complete ACK retains journal");
   byte[] raw=Store.unhex("410401000000");
   check(store.archiveHistory(Arrays.asList(raw,raw),2,"synthetic-ring",new int[]{2,0,0})==1,"history deduplicates");
   check(store.archiveHistory(Arrays.asList(raw),2,"synthetic-ring",new int[]{2,0,0})==0,"history retry deduplicates");
   int before=store.journalCount();refused=false;
   try{store.archiveHistory(Arrays.asList(Store.unhex("43050200000061"),Store.unhex("2f022e00")),99,"synthetic-ring",new int[]{2,0,0});}catch(IllegalArgumentException e){refused=true;}
   check(refused&&store.journalCount()==before&&store.getLong("history.cursor.synthetic-ring",0)==2,"failed history page rolls back records and checkpoint");
   refused=false;try{store.append("test",Store.object("owner_key_hex","synthetic"));}catch(IllegalArgumentException e){refused=true;}
   check(refused,"credentials excluded from raw journal");
   JSONObject secret=Store.object("access_token","synthetic-token-not-a-user-secret");store.setSecret("test",secret);
   check(store.getSecret("test").toString().equals(secret.toString()),"Keystore AES-GCM roundtrip");
   try(Cursor c=store.getReadableDatabase().rawQuery("SELECT value FROM secrets WHERE key='test'",null)){c.moveToFirst();check(!new String(c.getBlob(0),java.nio.charset.StandardCharsets.UTF_8).contains("synthetic-token"),"database contains ciphertext");}
   store.close();store=new Store(getTargetContext(),name);check(store.getSecret("test").toString().equals(secret.toString()),"Keystore survives database reopen");
   JSONObject cfg=Store.object("work",5,"rest",0,"rounds",1,"plan",new JSONArray().put(Store.object("name","Test","duration",5,"met",4)));
   store.saveSettings(Store.object("profile",Store.object("weight_kg",126),"config",cfg));Workout workout=new Workout(store);
   workout.start("Prova isolata");Thread.sleep(80);workout.pause();double paused=workout.elapsedSeconds();Thread.sleep(80);
   check(Math.abs(paused-workout.elapsedSeconds())<0.001,"pause freezes active time");
   workout.resume();Thread.sleep(80);check(workout.elapsedSeconds()>paused,"resume increments active time");workout.end();
   check(workout.current()==null&&store.sessions().length()==1,"ended session remains in diary");
   check(Workout.position(10,cfg).getBoolean("complete"),"plan completion separate from session duration");
   JSONObject energy=Workout.energy(10,cfg,Store.object("weight_kg",126));
   check(energy.getDouble("estimated_seconds")==5&&energy.getDouble("unestimated_seconds")==5,"energy coverage excludes time beyond plan");
   check(Math.abs(energy.getDouble("gross")-7.35)<0.001,"MET energy units");
   store.addReading(1700000000123L,120,Arrays.asList("Movimento segnalato"),17,1);
   check(store.recentReadings().getJSONObject(0).getDouble("v")==120,"motion warning retains valid HR");
   OuraProtocolTest.main(new String[0]);
   result.putString("stream","PASS "+assertions+" SQLite/Keystore/workout assertions + 25 protocol assertions; isolated database only\n");finish(-1,result);
  }catch(Throwable e){result.putString("stream","FAIL "+e.getClass().getSimpleName()+": "+e.getMessage()+"\n");finish(0,result);}
  finally{if(store!=null)store.close();getTargetContext().deleteDatabase(name);if(alias!=null)try{KeyStore keys=KeyStore.getInstance("AndroidKeyStore");keys.load(null);keys.deleteEntry(alias);}catch(Exception ignored){}}
 }
}
