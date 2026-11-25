import SwiftUI
import UIKit

@MainActor enum AppRuntime {
    private static var loading:Task<AppModel,Error>?
    static func load()async throws->AppModel{
        if let loading{return try await loading.value}
        let task=Task<AppModel,Error>{
            let archive=try await Task.detached(priority:.userInitiated){
                let root=try FileManager.default.url(for:.applicationSupportDirectory,in:.userDomainMask,appropriateFor:nil,create:true).appendingPathComponent("HomePulse",isDirectory:true)
                #if DEBUG
                let isolated=ProcessInfo.processInfo.arguments.contains("--uitest-isolated-store")
                let folder=isolated ? root.appendingPathComponent("UITest"):root
                #else
                let folder=root
                #endif
                let store=try EventStore(url:folder.appendingPathComponent("journal.sqlite"))
                return (store,folder,AppArchiveSnapshot.load(store))
            }.value
            let model=AppModel(store:archive.0,folder:archive.1,snapshot:archive.2)
            let active=UIApplication.shared.applicationState == .active
            model.ring.setForeground(active);model.api.setForeground(active)
            #if DEBUG
            let arguments=ProcessInfo.processInfo.arguments
            if arguments.contains("--uitest-isolated-store"),arguments.contains("--uitest-storage-stall"){
                // Intentionally block only the isolated test archive. Navigation
                // must continue while database operations wait on its lock.
                archive.0.enqueue{store in
                    store.holdArchiveForUITest(seconds:15){Task{@MainActor in model.archiveTestStall=true}}
                    Task{@MainActor in model.archiveTestStall=false}
                }
            }
            #endif
            return model
        }
        loading=task;return try await task.value
    }
}
@MainActor final class AppBootstrap:ObservableObject {
    @Published var model:AppModel?;@Published var error:String?
    init(){Task{do{model=try await AppRuntime.load()}catch{self.error="Archivio locale non disponibile. Libera spazio e riapri l’app; la raccolta non è stata avviata."}}}
}
final class AppDelegate:NSObject,UIApplicationDelegate {
    func application(_ application:UIApplication,didFinishLaunchingWithOptions launchOptions:[UIApplication.LaunchOptionsKey:Any]?=nil)->Bool{
        if launchOptions?[.bluetoothCentrals] != nil{updateActivity(false)}
        return true
    }
    func applicationDidEnterBackground(_ application:UIApplication){updateActivity(false)}
    func applicationDidBecomeActive(_ application:UIApplication){updateActivity(true)}
    private func updateActivity(_ active:Bool){Task{@MainActor in
        guard let model=try? await AppRuntime.load() else{return}
        model.ring.setForeground(active);model.api.setForeground(active)
    }}
    func application(_ application:UIApplication,handleEventsForBackgroundURLSession identifier:String,completionHandler:@escaping()->Void){
        guard identifier=="com.familos.homepulse.uploads" else{completionHandler();return}
        Task{@MainActor in do{let model=try await AppRuntime.load();model.api.attachBackgroundCompletion(completionHandler)}catch{completionHandler()}}
    }
}
@main struct HomePulseApp:App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var bootstrap=AppBootstrap()
    var body:some Scene{WindowGroup{
        if let model=bootstrap.model{RootView().environmentObject(model)}
        else if let error=bootstrap.error{ContentUnavailableView("Archivio non disponibile",systemImage:"externaldrive.badge.exclamationmark",description:Text(error))}
        else{ProgressView("Apertura archivio…")}
    }}
}
