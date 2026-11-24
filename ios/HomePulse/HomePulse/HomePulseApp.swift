import SwiftUI
import UIKit

@MainActor enum AppRuntime {static var model:AppModel?;static var initializationError:String?}
final class AppDelegate:NSObject,UIApplicationDelegate {
    func application(_ application:UIApplication,handleEventsForBackgroundURLSession identifier:String,completionHandler:@escaping()->Void){
        guard identifier=="com.familos.homepulse.uploads" else{completionHandler();return}
        Task{@MainActor in if let model=AppRuntime.model{model.api.backgroundCompletion=completionHandler}else{completionHandler()}}
    }
}
@main struct HomePulseApp:App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var delegate
    init(){
        do{
            let root=try FileManager.default.url(for:.applicationSupportDirectory,in:.userDomainMask,appropriateFor:nil,create:true).appendingPathComponent("HomePulse",isDirectory:true)
            #if DEBUG
            let isolated=ProcessInfo.processInfo.arguments.contains("--uitest-isolated-store")
            let folder=isolated ? root.appendingPathComponent("UITest") : root
            #else
            let folder=root
            #endif
            let store=try EventStore(url:folder.appendingPathComponent("journal.sqlite"))
            AppRuntime.model=AppModel(store:store,folder:folder)
        }catch{AppRuntime.initializationError="Archivio locale non disponibile. Libera spazio e riapri l’app; la raccolta non è stata avviata."}
    }
    var body:some Scene{WindowGroup{
        if let model=AppRuntime.model{RootView().environmentObject(model)}else{ContentUnavailableView("Archivio non disponibile",systemImage:"externaldrive.badge.exclamationmark",description:Text(AppRuntime.initializationError ?? "Riapri l’app"))}
    }}
}
