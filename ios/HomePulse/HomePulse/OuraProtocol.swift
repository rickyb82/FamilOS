import Foundation
import CommonCrypto

struct OuraLive {
    var bpm:Int?;var ibi:Int?;var flags:Int;var state:Int;var validity:Int;var age:Int;var temperatureRaw:Int;var warnings:[String]
}
enum OuraWire {
    static let service="98ed0001-a541-11e4-b6a0-0002a5d5c51b"
    static let write="98ed0002-a541-11e4-b6a0-0002a5d5c51b", notify="98ed0003-a541-11e4-b6a0-0002a5d5c51b"
    static func complete(_ data:Data,minimum:Int,tag:UInt8,extended:UInt8?=nil)throws->[UInt8]{
        let b=[UInt8](data);guard b.count>=minimum,b[0]==tag,Int(b[1])+2==b.count else{throw AppError.message("Pacchetto anello incompleto")}
        if let extended, b.count<3 || b[2] != extended {throw AppError.message("Risposta anello inattesa")};return b
    }
    static func firmware(_ frame:Data)throws->[Int]{let b=try complete(frame,minimum:20,tag:9);return b[2..<5].map(Int.init)}
    static func capabilities(_ frame:Data)throws->(Int,[Int:Int]){
        let b=try complete(frame,minimum:4,tag:0x2f,extended:2)
        guard (1...16).contains(Int(b[3])),(b.count-4)%2==0 else{throw AppError.message("Capability anello non valide")}
        var v:[Int:Int]=[:];for i in stride(from:4,to:b.count,by:2){if let old=v[Int(b[i])],old != Int(b[i+1]){throw AppError.message("Capability incoerenti")};v[Int(b[i])]=Int(b[i+1])};return(Int(b[3]),v)
    }
    static func ack(_ frame:Data,extended:UInt8,feature:UInt8)throws {
        let b=try complete(frame,minimum:5,tag:0x2f,extended:extended)
        guard b[3]==feature,b[4]==0 else{throw AppError.message("Comando anello rifiutato (esito \(b[4]))")}
    }
    static func cipher(key:Data,nonce:Data)throws->Data {
        guard key.count==16,nonce.count==15 else{throw AppError.message("Credenziale anello non disponibile")}
        var result=Data(count:32);var length=0
        let status=result.withUnsafeMutableBytes{out in key.withUnsafeBytes{k in nonce.withUnsafeBytes{n in
            CCCrypt(CCOperation(kCCEncrypt),CCAlgorithm(kCCAlgorithmAES),CCOptions(kCCOptionECBMode|kCCOptionPKCS7Padding),k.baseAddress,key.count,nil,n.baseAddress,nonce.count,out.baseAddress,32,&length)
        }}}
        guard status==kCCSuccess,length==16 else{throw AppError.message("Autenticazione anello non riuscita")};result.count=length;return result
    }
    static func displaySupported(_ bpm:Int)->Bool{(20...250).contains(bpm)}
    static func live(_ frame:Data,api:[Int])throws->OuraLive {
        let modern=api.lexicographicallyPrecedes([1,10,0]) == false
        let b=try complete(frame,minimum:modern ? 17:15,tag:0x2f,extended:0x28)
        guard b[3]==2 else{throw AppError.message("Notifica diversa dal battito live")}
        let ibi=Int(b[8])|((Int(b[9])&15)<<8),validity=Int(b[9]>>4)
        let valid=b[5]==2 && b[4]&1 != 0 && validity==1 && ibi>0 && ibi<=2000
        let names:[(UInt8,String)]=[(16,"Movimento segnalato"),(4,"Segnale ottico debole"),(8,"Dita fredde"),(2,"Ricerca segnale"),(32,"Identificazione segnale")]
        let offset=modern ? 14:13
        return OuraLive(bpm:valid ? Int((60000.0/Double(ibi)).rounded()):nil,ibi:valid ? ibi:nil,flags:Int(b[4]),state:Int(b[5]),validity:validity,age:Int(b[6])|(Int(b[7])<<8),temperatureRaw:Int(Int16(bitPattern:UInt16(b[offset])|(UInt16(b[offset+1])<<8))),warnings:names.filter{b[4]&$0.0 != 0}.map{$0.1})
    }
    static func sensitive(_ data:Data)->Bool {
        let b=[UInt8](data);guard !b.isEmpty else{return false}
        if b[0]==0x24 || b[0]==0x25{return true}
        return b.count>=3 && b[0]==0x2f && (0x2b...0x2f).contains(b[2])
    }
    static func historyRequest(cursor:UInt32,max:UInt8=64)->Data {Data([0x10,9,UInt8(cursor&255),UInt8((cursor>>8)&255),UInt8((cursor>>16)&255),UInt8((cursor>>24)&255),max,255,255,255,255])}
    static func cursor(_ frame:Data)throws->UInt32 {let b=[UInt8](frame);guard b.count>=6,b[0]>=0x41,b[0]<=0x85,Int(b[1])+2==b.count else{throw AppError.message("Cursore storico esteso non supportato")};return UInt32(b[2])|(UInt32(b[3])<<8)|(UInt32(b[4])<<16)|(UInt32(b[5])<<24)}
    static func sportParameters(activity:UInt16,age:Int,resting:Int)throws->Data {
        guard (18...100).contains(age),(30...200).contains(resting) else{throw AppError.message("Servono età e FC a riposo valide")}
        return Data([0x2f,7,0x29,3,UInt8(activity&255),UInt8(activity>>8),UInt8(resting),50,UInt8(230-age)])
    }
}
struct OuraFramer {
    private(set) var buffer=Data()
    private(set) var opaqueRejected:Data?
    mutating func consume(_ value:Data,onFrame:((Data)throws->Void)?=nil)throws->[Data] {
        opaqueRejected=nil
        buffer.append(value);guard buffer.count<=65536 else{buffer.removeAll();throw AppError.message("Buffer anello oltre limite")}
        var frames:[Data]=[]
        while buffer.count>=2 {
            let b=[UInt8](buffer),length:Int
            if b[0]==0xfe {opaqueRejected=buffer;buffer.removeAll();throw AppError.message("Formato storico FE conservato raw ma non decodificato")}
            if b[0]==0xfd {guard b.count>=4 else{break};length=Int(b[3])+4}else{length=Int(b[1])+2}
            guard length>=2,length<=4096 else{buffer.removeAll();throw AppError.message("Lunghezza anello inattesa")}
            guard buffer.count>=length else{break};let frame=Data(buffer.prefix(length));buffer.removeFirst(length);try onFrame?(frame);frames.append(frame)
        }
        return frames
    }
}
