package com.familos.homepulse;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import javax.crypto.Cipher;
import javax.crypto.spec.SecretKeySpec;

/** Original reconstruction from the documented local protocol, without vendor libraries. */
public final class OuraProtocol {
    public static final UUID SERVICE = UUID.fromString("98ed0001-a541-11e4-b6a0-0002a5d5c51b");
    public static final UUID WRITE = UUID.fromString("98ed0002-a541-11e4-b6a0-0002a5d5c51b");
    public static final UUID NOTIFY = UUID.fromString("98ed0003-a541-11e4-b6a0-0002a5d5c51b");
    public static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    private OuraProtocol() {}

    public static final class ProtocolException extends IllegalArgumentException {
        public ProtocolException(String message) { super(message); }
    }
    public static int u(byte b) { return b & 255; }
    public static byte[] bytes(int... values) {
        byte[] result = new byte[values.length];
        for (int i = 0; i < values.length; i++) result[i] = (byte) values[i];
        return result;
    }
    public static byte[] concat(byte[] a, byte[] b) {
        byte[] result = Arrays.copyOf(a, a.length + b.length);
        System.arraycopy(b, 0, result, a.length, b.length);
        return result;
    }
    public static String hex(byte[] value) {
        char[] digits = "0123456789abcdef".toCharArray(), text = new char[value.length * 2];
        for (int i = 0; i < value.length; i++) { text[2*i] = digits[u(value[i]) >>> 4]; text[2*i+1] = digits[u(value[i]) & 15]; }
        return new String(text);
    }
    public static byte[] unhex(String text) {
        if (text == null || text.length() % 2 != 0) throw new ProtocolException("Esadecimale non valido");
        byte[] result = new byte[text.length()/2];
        for (int i=0; i<result.length; i++) {
            int a=Character.digit(text.charAt(i*2),16), b=Character.digit(text.charAt(i*2+1),16);
            if (a<0 || b<0) throw new ProtocolException("Esadecimale non valido");
            result[i]=(byte)((a<<4)|b);
        }
        return result;
    }
    public static byte[] complete(byte[] frame, int minimum, int tag, int extended) {
        if (frame == null || frame.length < minimum || frame.length < 2 || u(frame[0]) != tag || u(frame[1])+2 != frame.length)
            throw new ProtocolException("Pacchetto anello incompleto");
        if (extended >= 0 && (frame.length < 3 || u(frame[2]) != extended))
            throw new ProtocolException("Risposta anello inattesa");
        return frame;
    }
    public static int[] firmware(byte[] frame) {
        complete(frame,20,9,-1);
        return new int[]{u(frame[2]),u(frame[3]),u(frame[4])};
    }
    public static final class Capabilities {
        public final int pages;
        public final Map<Integer,Integer> features;
        Capabilities(int pages, Map<Integer,Integer> features) { this.pages=pages; this.features=Collections.unmodifiableMap(features); }
    }
    public static Capabilities capabilities(byte[] frame) {
        complete(frame,4,0x2f,2);
        int pages=u(frame[3]);
        if (pages<1 || pages>16 || (frame.length-4)%2 != 0) throw new ProtocolException("Capacità anello non valide");
        Map<Integer,Integer> features=new LinkedHashMap<>();
        for(int i=4;i<frame.length;i+=2) {
            int id=u(frame[i]), version=u(frame[i+1]);
            Integer previous=features.put(id,version);
            if(previous!=null && previous!=version) throw new ProtocolException("Capacità incoerenti");
        }
        return new Capabilities(pages,features);
    }
    public static void ack(byte[] frame,int extended,int feature) {
        complete(frame,5,0x2f,extended);
        if(u(frame[3])!=feature || frame[4]!=0) throw new ProtocolException("Comando anello rifiutato (esito "+u(frame[4])+")");
    }
    public static byte[] encryptNonce(byte[] ownerKey,byte[] nonce) {
        if(ownerKey==null || ownerKey.length!=16 || nonce==null || nonce.length!=15)
            throw new ProtocolException("Credenziale anello non disponibile");
        try {
            Cipher cipher=Cipher.getInstance("AES/ECB/PKCS5Padding");
            cipher.init(Cipher.ENCRYPT_MODE,new SecretKeySpec(ownerKey,"AES"));
            byte[] result=cipher.doFinal(nonce);
            if(result.length!=16) throw new ProtocolException("Risposta crittografica non valida");
            return result;
        } catch(java.security.GeneralSecurityException error) {
            throw new ProtocolException("Autenticazione crittografica non disponibile");
        }
    }
    public static boolean sensitive(byte[] frame) {
        if(frame==null || frame.length==0) return false;
        int tag=u(frame[0]);
        return tag==0x24 || tag==0x25 || tag==0x2f && frame.length>=3 && u(frame[2])>=0x2b && u(frame[2])<=0x2f;
    }
    public static boolean displaySupported(double bpm) { return Double.isFinite(bpm) && bpm>=20 && bpm<=250; }
    public static boolean modern(int[] api) {
        if(api==null || api.length!=3) throw new ProtocolException("Versione API non disponibile");
        return api[0]>1 || api[0]==1 && api[1]>=10;
    }
    public static final class Live {
        public final double bpm;
        public final int ibiMillis, validity, flags, state, ageRaw, temperatureRaw;
        public final List<String> warnings;
        Live(double bpm,int ibi,int validity,int flags,int state,int age,int temp,List<String> warnings) {
            this.bpm=bpm;this.ibiMillis=ibi;this.validity=validity;this.flags=flags;this.state=state;this.ageRaw=age;this.temperatureRaw=temp;
            this.warnings=Collections.unmodifiableList(warnings);
        }
    }
    public static Live live(byte[] frame,int[] api) {
        boolean modern=modern(api);complete(frame,modern?17:15,0x2f,0x28);
        if(u(frame[3])!=2) throw new ProtocolException("Notifica diversa dal battito live");
        int flags=u(frame[4]), state=u(frame[5]), ibi=u(frame[8])|((u(frame[9])&15)<<8), validity=u(frame[9])>>>4;
        boolean valid=state==2 && (flags&1)!=0 && validity==1 && ibi>0 && ibi<=2000;
        List<String> warnings=new ArrayList<>();
        int[] masks={16,4,8,2,32};String[] labels={"Movimento segnalato","Segnale ottico debole","Dita fredde","Ricerca segnale","Identificazione segnale"};
        for(int i=0;i<masks.length;i++) if((flags&masks[i])!=0) warnings.add(labels[i]);
        int offset=modern?14:13;
        return new Live(valid?Math.round(60000.0/ibi):Double.NaN,ibi,validity,flags,state,u(frame[6])|(u(frame[7])<<8),(short)(u(frame[offset])|(u(frame[offset+1])<<8)),warnings);
    }
    public static long uint32(byte[] frame,int offset) {
        if(offset<0 || frame.length<offset+4) throw new ProtocolException("Intero storico incompleto");
        return (long)u(frame[offset])|((long)u(frame[offset+1])<<8)|((long)u(frame[offset+2])<<16)|((long)u(frame[offset+3])<<24);
    }
    public static long cursor(byte[] frame) {
        if(frame==null || frame.length<6 || u(frame[0])<0x41 || u(frame[0])>0x85 || u(frame[1])+2!=frame.length)
            throw new ProtocolException("Cursore storico esteso non supportato");
        return uint32(frame,2);
    }
    public static byte[] historyRequest(long cursor) {
        if(cursor<0 || cursor>0xffffffffL) throw new ProtocolException("Cursore fuori intervallo");
        return bytes(0x10,9,(int)cursor,(int)(cursor>>>8),(int)(cursor>>>16),(int)(cursor>>>24),64,255,255,255,255);
    }
    public static final class HistoryPage {
        public final long nextCursor, bytesLeft;
        HistoryPage(long nextCursor,long bytesLeft){this.nextCursor=nextCursor;this.bytesLeft=bytesLeft;}
    }
    public static HistoryPage historyPage(byte[] summary,List<byte[]> records,long requestedCursor) {
        complete(summary,8,0x11,-1);
        if(u(summary[2])!=records.size() || records.size()>64) throw new ProtocolException("Conteggio storico incoerente: cursore conservato");
        long next=requestedCursor, previous=requestedCursor;
        for(byte[] record:records) {
            long value=cursor(record);
            if(value<previous || value>=0xffffffffL) throw new ProtocolException("Ordine storico inatteso: cursore conservato");
            previous=value;next=value+1;
        }
        if(!records.isEmpty() && next<=requestedCursor) throw new ProtocolException("Storico senza avanzamento");
        return new HistoryPage(next,uint32(summary,4));
    }
    public static boolean allowedActivity(int id) { return id==65535 || id==14 || id==12 || id==5 || id==8 || id==10 || id==15; }
    public static byte[] sportParameters(int activity,int age,int resting) {
        if(!allowedActivity(activity)) throw new ProtocolException("Scegli un’attività compatibile verificata");
        if(age<18 || age>100 || resting<30 || resting>200) throw new ProtocolException("Servono età e FC a riposo valide");
        return bytes(0x2f,7,0x29,3,activity,activity>>>8,resting,50,230-age);
    }
    public static int sportRestoreMode(byte[] status) {
        complete(status,8,0x2f,0x21);
        if(u(status[3])!=3 || status[7]!=0 || u(status[4])>1) throw new ProtocolException("Registrazione cardio già attiva o stato non modificabile");
        return u(status[4]);
    }
    public interface FrameSink { void accept(byte[] frame) throws Exception; }
    public static final class Framer {
        private byte[] buffer=new byte[0];
        public void reset(){buffer=new byte[0];}
        public int bufferedBytes(){return buffer.length;}
        public void feed(byte[] value,FrameSink sink) throws Exception {
            if(value==null || buffer.length+value.length>65536) {reset();throw new ProtocolException("Buffer anello oltre limite");}
            buffer=concat(buffer,value);
            while(buffer.length>=2) {
                int tag=u(buffer[0]),length;
                if(tag==0xfe){reset();throw new ProtocolException("Formato storico FE non decodificato: cursore conservato");}
                if(tag==0xfd){if(buffer.length<4)return;length=u(buffer[3])+4;}else length=u(buffer[1])+2;
                if(length<2 || length>4096){reset();throw new ProtocolException("Lunghezza anello inattesa");}
                if(buffer.length<length)return;
                byte[] frame=Arrays.copyOf(buffer,length);buffer=Arrays.copyOfRange(buffer,length,buffer.length);
                sink.accept(frame);
            }
        }
    }
}
