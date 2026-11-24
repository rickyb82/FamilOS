package com.familos.homepulse;
import java.util.*;

/** Standalone JDK tests; all frames, credentials and timestamps are synthetic. */
public final class OuraProtocolTest {
    private static int count;
    private static void check(boolean value,String message){count++;if(!value)throw new AssertionError(message);}
    private interface Throwing {void run()throws Exception;}
    private static void rejects(Throwing test,String message)throws Exception{boolean rejected=false;try{test.run();}catch(OuraProtocol.ProtocolException expected){rejected=true;}check(rejected,message);}
    public static void main(String[] args)throws Exception {
        byte[] key=OuraProtocol.unhex("000102030405060708090a0b0c0d0e0f");
        byte[] nonce=OuraProtocol.unhex("000102030405060708090a0b0c0d0e");
        check(OuraProtocol.hex(OuraProtocol.encryptNonce(key,nonce)).equals("b61e6af8da7260d2214369b951bf8963"),"AES known answer");
        rejects(()->OuraProtocol.encryptNonce(new byte[0],nonce),"reject missing owner key");
        OuraProtocol.Live live=OuraProtocol.live(OuraProtocol.unhex("2f0f280211020000e81300000000ac0d01"),new int[]{2,0,0});
        check(live.bpm==60 && live.ibiMillis==1000,"valid IBI decoded");
        check(live.warnings.contains("Movimento segnalato"),"motion stays a warning");
        check(live.temperatureRaw==3500,"live temperature remains raw");
        OuraProtocol.Live invalid=OuraProtocol.live(OuraProtocol.unhex("2f0f280211020000e80300000000ac0d01"),new int[]{2,0,0});
        check(Double.isNaN(invalid.bpm),"invalid IBI cannot become HR");
        OuraProtocol.Live high=OuraProtocol.live(OuraProtocol.unhex("2f0f280201020000011000000000000000"),new int[]{2,0,0});
        check(high.bpm==60000 && !OuraProtocol.displaySupported(high.bpm),"raw high HR kept out of display");
        byte[] recordA=OuraProtocol.unhex("410401000000"),recordB=OuraProtocol.unhex("43050200000061");
        byte[] auth=OuraProtocol.unhex("2f102c000102030405060708090a0b0c0d0e");
        OuraProtocol.Framer framer=new OuraProtocol.Framer();List<byte[]> archived=new ArrayList<>();
        framer.feed(Arrays.copyOf(auth,4),f->{if(!OuraProtocol.sensitive(f))archived.add(f);});
        framer.feed(OuraProtocol.concat(Arrays.copyOfRange(auth,4,auth.length),OuraProtocol.concat(recordA,recordB)),f->{if(!OuraProtocol.sensitive(f))archived.add(f);});
        check(archived.size()==2 && Arrays.equals(archived.get(0),recordA) && Arrays.equals(archived.get(1),recordB),"fragmented auth excluded, concatenated sensor frames retained");
        check(OuraProtocol.sensitive(OuraProtocol.unhex("2f022e00")),"auth ACK excluded");
        check(!OuraProtocol.sensitive(OuraProtocol.unhex("2f03250300")),"feature latest not auth");
        check(OuraProtocol.hex(OuraProtocol.historyRequest(18645)).equals("1009d548000040ffffffff"),"cursor request");
        OuraProtocol.HistoryPage page=OuraProtocol.historyPage(OuraProtocol.unhex("1106020005000000"),Arrays.asList(recordA,recordB),1);
        check(page.nextCursor==3 && page.bytesLeft==5,"summary and next cursor");
        rejects(()->OuraProtocol.historyPage(OuraProtocol.unhex("1106020005000000"),Arrays.asList(recordB,recordA),1),"out of order history does not checkpoint");
        rejects(()->OuraProtocol.historyPage(OuraProtocol.unhex("1106010005000000"),Arrays.asList(recordA,recordB),1),"count mismatch does not checkpoint");
        rejects(()->OuraProtocol.historyPage(OuraProtocol.unhex("110102"),Arrays.asList(recordA,recordB),1),"short summary cannot imply zero bytes left");
        check(OuraProtocol.hex(OuraProtocol.sportParameters(5,40,60)).equals("2f07290305003c32be"),"manual sport parameters");
        rejects(()->OuraProtocol.sportParameters(99,40,60),"unknown activity id");
        rejects(()->OuraProtocol.sportParameters(5,0,60),"missing age");
        check(OuraProtocol.sportRestoreMode(OuraProtocol.unhex("2f06210301010000"))==1,"restore previous AUTO");
        rejects(()->OuraProtocol.sportRestoreMode(OuraProtocol.unhex("2f06210302010000")),"do not replace an existing REQUESTED session");
        OuraProtocol.ack(OuraProtocol.unhex("2f03230200"),0x23,2);
        rejects(()->OuraProtocol.ack(OuraProtocol.unhex("2f03230201"),0x23,2),"reject feature error");
        check(OuraProtocol.capabilities(OuraProtocol.unhex("2f06020102040303")).features.get(2)==4,"capabilities parsed");
        rejects(()->OuraProtocol.capabilities(OuraProtocol.unhex("2f06020102040205")),"conflicting capabilities");
        List<byte[]> beforeFailure=new ArrayList<>();OuraProtocol.Framer unsupported=new OuraProtocol.Framer();
        rejects(()->unsupported.feed(OuraProtocol.concat(recordA,OuraProtocol.bytes(0xfe,0)),beforeFailure::add),"unknown FE blocks parsing");
        check(beforeFailure.size()==1,"earlier frame survives later framing failure");
        System.out.println("PASS "+count+" protocol assertions; synthetic fixtures only");
    }
}
