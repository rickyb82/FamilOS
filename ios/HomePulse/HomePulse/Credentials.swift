import Foundation
import Security

struct SecureStore {
    static let service="com.familos.homepulse"
    static func write(_ data:Data,key:String) throws {
        let query:[String:Any]=[kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:service,kSecAttrAccount as String:key]
        let attributes:[String:Any]=[kSecValueData as String:data,kSecAttrAccessible as String:kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let updated=SecItemUpdate(query as CFDictionary,attributes as CFDictionary)
        if updated==errSecItemNotFound {var add=query;attributes.forEach{add[$0.key]=$0.value};guard SecItemAdd(add as CFDictionary,nil)==errSecSuccess else{throw AppError.message("Salvataggio credenziali non riuscito")}}
        else if updated != errSecSuccess {throw AppError.message("Keychain non disponibile")}
    }
    static func read(_ key:String)->Data? {
        let q:[String:Any]=[kSecClass as String:kSecClassGenericPassword,kSecAttrService as String:service,kSecAttrAccount as String:key,kSecReturnData as String:true,kSecMatchLimit as String:kSecMatchLimitOne]
        var value:CFTypeRef?;guard SecItemCopyMatching(q as CFDictionary,&value)==errSecSuccess else{return nil};return value as? Data
    }
    static func credentials()->Credentials? {read("bridge").flatMap{try? JSONDecoder().decode(Credentials.self,from:$0)}}
}

extension Data {
    init?(hex:String) {
        guard hex.count%2==0 else{return nil};self.init();var i=hex.startIndex
        while i<hex.endIndex {let end=hex.index(i,offsetBy:2);guard let byte=UInt8(hex[i..<end],radix:16) else{return nil};append(byte);i=end}
    }
    var hex:String {map{String(format:"%02x",$0)}.joined()}
}
