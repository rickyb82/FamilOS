import SwiftUI
import Charts
import MapKit
import UIKit

private struct DismissibleKeyboard:ViewModifier {
    func body(content:Content)->some View{
        content.scrollDismissesKeyboard(.interactively).submitLabel(.done)
            .background(OutsideKeyboardDismissal().allowsHitTesting(false))
            .onSubmit{dismiss()}
            .toolbar{ToolbarItemGroup(placement:.keyboard){Spacer();Button("Fine"){dismiss()}.accessibilityIdentifier("homepulse-keyboard-done")}}
    }
    private func dismiss(){UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder),to:nil,from:nil,for:nil)}
}
extension View{func dismissibleKeyboard()->some View{modifier(DismissibleKeyboard())}}

/// Observe outside taps without consuming a control's normal touch or a scroll.
private struct OutsideKeyboardDismissal:UIViewRepresentable {
    final class Host:UIView {
        var windowChanged:((UIWindow?)->Void)?
        override func didMoveToWindow(){super.didMoveToWindow();windowChanged?(window)}
    }
    final class Coordinator:NSObject,UIGestureRecognizerDelegate {
        private weak var window:UIWindow?
        private lazy var tap:UITapGestureRecognizer={
            let value=UITapGestureRecognizer(target:self,action:#selector(dismiss))
            value.cancelsTouchesInView=false;value.delaysTouchesBegan=false;value.delaysTouchesEnded=false
            value.delegate=self;return value
        }()
        func attach(to next:UIWindow?){
            guard window !== next else{return}
            window?.removeGestureRecognizer(tap);window=next;next?.addGestureRecognizer(tap)
        }
        @objc private func dismiss(){window?.endEditing(false)}
        func gestureRecognizer(_ gestureRecognizer:UIGestureRecognizer,shouldReceive touch:UITouch)->Bool{
            var view=touch.view
            while let current=view{
                // Tapping any text editor should focus it and keep its keyboard.
                if current is UITextInput{return false}
                view=current.superview
            }
            return true
        }
        func gestureRecognizer(_ gestureRecognizer:UIGestureRecognizer,shouldRecognizeSimultaneouslyWith otherGestureRecognizer:UIGestureRecognizer)->Bool{true}
    }
    func makeCoordinator()->Coordinator{Coordinator()}
    func makeUIView(context:Context)->Host{
        let view=Host();view.isUserInteractionEnabled=false
        view.windowChanged={[weak coordinator=context.coordinator] window in coordinator?.attach(to:window)}
        return view
    }
    func updateUIView(_ view:Host,context:Context){context.coordinator.attach(to:view.window)}
    static func dismantleUIView(_ view:Host,coordinator:Coordinator){view.windowChanged=nil;coordinator.attach(to:nil)}
}

struct RootView:View {
    @EnvironmentObject var model:AppModel
    @Environment(\.scenePhase) var scene
    var body:some View{
        TabView{
            HomeView().tabItem{Label("Home",systemImage:"heart.fill")}
            WorkoutView().tabItem{Label("Allenamento",systemImage:"figure.strengthtraining.traditional")}
            SleepView().tabItem{Label("Sonno",systemImage:"bed.double.fill")}
            DiaryView().tabItem{Label("Diario",systemImage:"chart.xyaxis.line")}
            SettingsView().tabItem{Label("Impostazioni",systemImage:"slider.horizontal.3")}
        }.tint(.teal).preferredColorScheme(.dark)
        #if DEBUG
        .overlay(alignment:.top){VStack{
            if model.archiveTestStall{Text("Archivio di prova occupato").font(.caption2).accessibilityIdentifier("archive-stall-active")}
            if model.diagnosticTestStall{Text("Scrittura diagnostica di prova occupata").font(.caption2).accessibilityIdentifier("diagnostic-stall-active")}
        }.allowsHitTesting(false)}
        #endif
        .onOpenURL{url in Task{await model.openPairingURL(url)}}
        .task{model.ring.setForeground(scene == .active);model.api.setForeground(scene == .active);await model.refresh();await model.requestBatteryNotifications()}
        .onChange(of:scene){_,value in model.ring.setForeground(value == .active);model.api.setForeground(value == .active);if value == .active{Task{await model.refresh()}}}
        .alert("Home Pulse",isPresented:Binding(get:{model.message != nil},set:{if !$0{model.message=nil}})){Button("OK"){model.message=nil}}message:{Text(model.message ?? "")}
    }
}
struct Card<Content:View>:View{
    let title:String;@ViewBuilder var content:()->Content
    var body:some View{VStack(alignment:.leading,spacing:14){Text(title).font(.headline).foregroundStyle(.secondary);content()}.padding(20).frame(maxWidth:.infinity,alignment:.leading).background(.white.opacity(0.055),in:RoundedRectangle(cornerRadius:24))}
}
struct HomeView:View {
    @EnvironmentObject var model:AppModel
    var body:some View{NavigationStack{ScrollView{VStack(spacing:18){
        HStack{VStack(alignment:.leading){Text("Il tuo corpo, qui").font(.largeTitle.bold());Text(model.ownerName.map{"Ciao, \($0)"} ?? "Benvenuto in Home Pulse").foregroundStyle(.secondary)};Spacer();Image(systemName:"waveform.path.ecg").font(.largeTitle).foregroundStyle(.teal)}
        RingBatteryCard()
        SelectedCardioCard(ring:model.ring,recorded:model.recorded)
        DailyEnergyCard()
        HStack(alignment:.top,spacing:12){
            Card(title:"Temperatura anello"){
                let values=model.serverPoints("temperature")
                if let point=values.last{Text(point.value,format:.number.precision(.fractionLength(2)))+Text(" °C");Text(point.date,style:.relative).font(.caption).foregroundStyle(.secondary)}
                else if let value=model.metrics.temperatureC{Text(String(format:"%.2f °C",value)).font(.title2.bold());Text("Sensore 1 · storico\nOra anello non risolta").font(.caption).foregroundStyle(.secondary)}else{Text("—").font(.largeTitle);Text("In attesa dello storico").font(.caption)}
            }
            Card(title:model.serverData["spo2_catalog"]["latest_sleep"]["display_percentage"].number == nil ? "SpO₂ · ultima misura":"SpO₂ · media sonno"){
                let summary=model.serverData["spo2_catalog"]["latest_sleep"]
                if let average=summary["display_percentage"].number,let end=summary["ended_at"].number{
                    Text(String(format:"%.0f %%",average)).font(.title2.bold())
                    Text("Sonno del \(Date(timeIntervalSince1970:end).formatted(.dateTime.day().month()))").font(.caption).foregroundStyle(.secondary)
                }else if let point=model.serverPoints("spo2").last{Text(String(format:"%.0f %%",point.value)).font(.title2.bold());Text("Misurata il \(point.date.formatted(.dateTime.day().month().hour().minute()))").font(.caption).foregroundStyle(.secondary)}else{Text("—").font(.largeTitle);Text("In attesa di campioni elaborati").font(.caption).foregroundStyle(.secondary)}
            }
        }
        Card(title:"Respirazione storica"){
            if let point=model.serverPoints("respiratory_rate").last{Text("\(point.value,specifier:"%.1f") respiri/min").font(.title2.bold());Text(point.date,style:.relative).font(.caption).foregroundStyle(.secondary)}else{Text("—").font(.largeTitle);Text("In attesa di misure elaborate dall’anello").font(.caption).foregroundStyle(.secondary)}
        }
        Card(title:"Movimento e passi archiviati"){
            HStack{metric("Passi nei record",model.metrics.steps.map(String.init) ?? "—");Spacer();metric("Ultimo movimento",model.metrics.motionSeconds.map{"\($0) s"} ?? "—")}
            Text("Dati storici dell’anello. Non rappresentano pedalate o cadenza.").font(.caption).foregroundStyle(.secondary)
        }
        if let active=model.current{Card(title:"Sessione in corso"){Text(active.activityName).font(.title3.bold());TimelineView(.periodic(from:.now,by:1)){context in Text(active.elapsed(at:context.date).durationText).monospacedDigit()};if active.provisional{Text(GymDetection.prompt).font(.subheadline).foregroundStyle(.teal)}}}
        SyncCard(progress:model.transfer)
    }.padding()}.navigationTitle("Home Pulse").navigationBarTitleDisplayMode(.inline).background(Color.black)}}
    func metric(_ title:String,_ value:String)->some View{VStack(alignment:.leading){Text(value).font(.title2.bold()).monospacedDigit();Text(title).font(.caption).foregroundStyle(.secondary)}}
}
struct DailyEnergyCard:View{
    @EnvironmentObject var model:AppModel
    func number(_ value:JSONValue)->String{value.number.map{String(Int($0.rounded()))} ?? "—"}
    var body:some View{let energy=model.serverData["daily_energy"]
        if energy != .null{Card(title:"Calorie di oggi · finora"){
            HStack{VStack{Text(number(energy["base_kcal"])).font(.title2.bold());Text("Di base").font(.caption)};Spacer();VStack{Text(number(energy["active_kcal"])).font(.title2.bold());Text("Attive stimate").font(.caption)};Spacer();VStack{Text(number(energy["total_kcal"])).font(.title2.bold());Text("Totale parziale").font(.caption)}}
            Text("kcal · stima da profilo e MET. I periodi senza dati non sono conteggiati come attività.").font(.caption).foregroundStyle(.secondary)
            if let seconds=energy["unestimated_activity_seconds"].number{Text("Attività senza stima: \(seconds.durationText)").font(.caption).foregroundStyle(.secondary)}
        }}
    }
}
struct RingBatteryCard:View{
    @EnvironmentObject var model:AppModel
    var body:some View{HStack{
        VStack(alignment:.leading,spacing:5){
            Label("Batteria Oura",systemImage:model.batteryChargeState?.isCharging == true ? "battery.100percent.bolt":((model.batteryLevel ?? 100)<=10 ? "battery.0percent":"battery.100percent")).font(.headline)
            if let state=model.batteryChargeState{Text(state.label).font(.subheadline).foregroundStyle(.secondary).accessibilityIdentifier("ring-battery-charge-state")}
        }
        Spacer()
        Text(model.batteryLevel.map{"\($0)%"} ?? "—").font(.title.bold()).monospacedDigit()
            .foregroundStyle((model.batteryLevel ?? 100)<=10 ? Color.orange:Color.teal)
            .accessibilityIdentifier("ring-battery-percentage")
    }.padding(20).background(.white.opacity(0.055),in:RoundedRectangle(cornerRadius:24))}
}
struct LiveCard:View{
    @EnvironmentObject var model:AppModel;@ObservedObject var ring:RingBridge
    var body:some View{Card(title:"Battito"){
        HStack(alignment:.firstTextBaseline){Text(model.displayedBPM.map(String.init) ?? "—").foregroundStyle(Color.primary).font(.system(size:82,weight:.semibold,design:.rounded)).monospacedDigit();Text("BPM").font(.title3).foregroundStyle(.secondary);Spacer();Image(systemName:"heart.fill").foregroundStyle(.red).font(.title)}
    }}
}
struct SelectedCardioCard:View{
    @ObservedObject var ring:RingBridge
    @ObservedObject var recorded:RecordedCardioController
    var body:some View{
        if ring.spo2ProbeActive {
            Card(title:"Prova SpO₂"){
                Text(ring.spo2Status).font(.subheadline)
                Button("Interrompi prova e ripristina battito"){Task{await ring.stopSpO2Probe()}}.buttonStyle(.borderedProminent)
            }
        }else if ring.recordedWorkoutProbeActive || recorded.running{
            Card(title:"Cardio registrato"){
                HStack(alignment:.firstTextBaseline){
                    Text(recorded.result?.latest?.bpm.map{String(Int($0))} ?? "—").font(.system(size:82,weight:.semibold,design:.rounded)).monospacedDigit()
                    Text("BPM").foregroundStyle(.secondary)
                }
                Text(recorded.status).font(.caption).foregroundStyle(.secondary)
            }
        }else{LiveCard(ring:ring)}
    }
}
struct RingSettingsControls:View{
    @EnvironmentObject var model:AppModel;@ObservedObject var ring:RingBridge
    var body:some View{VStack(alignment:.leading,spacing:12){
        Label(ring.status,systemImage:ring.connected ? "antenna.radiowaves.left.and.right":"antenna.radiowaves.left.and.right.slash").font(.subheadline)
        Text(ring.batteryStatus).font(.caption).foregroundStyle(.secondary)
        Text(ring.spo2Status).font(.caption).foregroundStyle(.secondary)
        Button("Controlla SpO₂ automatica"){Task{await ring.refreshSpO2()}}.disabled(!ring.ready || ring.busy)
        if ring.spo2ProbeActive {
            Button("Interrompi prova SpO₂ e ripristina battito"){Task{await ring.stopSpO2Probe()}}
        }else{
            Button("Prova misura SpO₂ diurna · 6 minuti"){Task{do{try await ring.startSpO2Probe()}catch{model.message=error.localizedDescription}}}.disabled(!ring.ready || ring.busy || ring.recordedWorkoutProbeActive)
        }
        if let error=ring.lastConnectionError{Text(error+" Ritento automaticamente; puoi premere Scollega per fermare i tentativi.").font(.caption).foregroundStyle(.orange)}
        ForEach(model.warnings,id:\.self){Text($0).font(.caption).foregroundStyle(.orange)}
        HStack{Button("Cerca anello"){ring.scan()}.buttonStyle(.borderedProminent);if ring.connected || ring.hasPendingConnection{Button("Scollega"){ring.disconnect()}.buttonStyle(.bordered)}}
        ForEach(ring.devices,id:\.identifier){device in Button(device.name ?? "Anello Oura"){ring.connect(device)}.buttonStyle(.bordered)}
        if ring.ready{Button("Scarica storico"){Task{await ring.downloadHistory()}}.disabled(ring.busy)}
        DisclosureGroup("Qualità e origine"){
            Text("Il numero resta l’ultimo battito valido ricevuto. Il grafico contiene soltanto nuove letture valide. Il movimento resta un avviso. La temperatura live resta grezza.").font(.caption)
            if let age=model.heartSampleAge{Text("Ultimo battito valido ricevuto \(age) s fa").font(.caption)}
            if let flags=model.heartDisplay.flags,let validity=model.heartDisplay.ibiValidity,let state=model.heartDisplay.measurementState{
                Text("Ultimo pacchetto · flags \(flags), validità IBI \(validity), stato \(state)").font(.caption.monospaced())
            }
            if let raw=model.temperatureRaw{Text("Temperatura live raw: \(raw)").font(.caption.monospaced())}
        }.font(.caption)
    }}
}
struct SyncCard:View{
    @EnvironmentObject var model:AppModel;@ObservedObject var progress:TransferProgress
    var body:some View{Card(title:"FamilOS"){
        HStack{Image(systemName:model.enrolled ? "checkmark.icloud":"icloud.slash").foregroundStyle(.teal);Text(model.syncStatus);Spacer()}
        Text("\(progress.pending) eventi in coda · \(progress.journalCount) conservati sul telefono").font(.caption).foregroundStyle(.secondary)
        Button("Sincronizza ora"){Task{await model.refresh();model.api.upload()}}.disabled(!model.enrolled)
    }}
}
struct TransferStatusText:View{@ObservedObject var progress:TransferProgress;var body:some View{Text(progress.status).font(.caption)}}
struct PulseChart:View{
    let readings:[Reading]
    private func segment(at index:Int)->Int{
        if let segment=readings[index].segment{return segment}
        guard index>0 else{return 0}
        return (1...index).reduce(0){count,i in count+(readings[i].date.timeIntervalSince(readings[i-1].date)>8 ? 1:0)}
    }
    var body:some View{if readings.isEmpty{ContentUnavailableView("Nessun campione",systemImage:"waveform.path.ecg",description:Text("I campioni reali compariranno qui."))}else{Chart{ForEach(Array(readings.enumerated()),id:\.element.id){index,p in LineMark(x:.value("Ora",p.date),y:.value("BPM",p.bpm),series:.value("Segmento",segment(at:index))).foregroundStyle(.teal)}}.chartYScale(domain:.automatic(includesZero:false)).chartXAxis{AxisMarks(values:.automatic(desiredCount:3))}}}
}
struct WorkoutView:View{
    @EnvironmentObject var model:AppModel;@State private var activity=""
    var body:some View{NavigationStack{TimelineView(.periodic(from:.now,by:1)){context in ScrollView{VStack(spacing:18){
        RingBatteryCard()
        SelectedCardioCard(ring:model.ring,recorded:model.recorded)
        if let session=model.current{
            let elapsed=session.elapsed(at:context.date),position=WorkoutEngine.position(elapsed:elapsed,config:session.config)
            Card(title:session.provisional ? "Sessione rilevata · da confermare":"Sessione attiva"){
                Text(session.activityName).font(.title2.bold());Text(position.name).font(.largeTitle.bold()).foregroundStyle(.teal)
                Text(Double(position.remaining).durationText).font(.system(size:64,weight:.medium,design:.rounded)).monospacedDigit()
                ProgressView(value:min(elapsed,session.config.total),total:session.config.total).tint(.teal)
                HStack{Text("Giro \(position.round)/\(session.config.rounds)");Spacer();Text(elapsed.durationText+" / "+session.config.total.durationText)}.font(.caption)
                if let min=position.targetMin,let max=position.targetMax{Text("Obiettivo scelto: \(min)–\(max) BPM").font(.subheadline)}
                HStack{if session.state=="paused"{Button("Riprendi",systemImage:"play.fill"){model.resume()}}else{Button("Pausa",systemImage:"pause.fill"){model.pause()}};Spacer();Button("Termina",systemImage:"stop.fill",role:.destructive){model.end()}}.buttonStyle(.borderedProminent)
                if session.provisional{
                    Text(GymDetection.prompt).font(.headline)
                    TextField("Attività",text:$activity).textFieldStyle(.roundedBorder)
                    HStack{Button("Conferma attività"){model.resolveCandidate(id:session.id,name:activity,negative:false)};Spacer();Button("Non mi alleno",role:.destructive){model.resolveCandidate(id:session.id,name:nil,negative:true)}}
                }
            }
            EnergyCard(session:session,elapsed:elapsed)
        }else{
            Card(title:"Pronto quando vuoi"){
                TextField("Cosa farai?",text:optionalText($model.settings.config.activity_name)).textFieldStyle(.roundedBorder)
                Text("\(model.settings.config.phases.count) fasi · \(model.settings.config.rounds) giri · \(model.settings.config.total.durationText)").foregroundStyle(.secondary)
                Button("Inizia allenamento",systemImage:"play.fill"){model.startWorkout()}.buttonStyle(.borderedProminent).controlSize(.large)
                Text("Timer e scheda funzionano anche senza rete o anello.").font(.caption).foregroundStyle(.secondary)
            }
        }
        Card(title:"La tua scheda"){
            ForEach(Array(model.settings.config.phases.enumerated()),id:\.offset){i,p in HStack{Text("\(i+1)").foregroundStyle(.teal);Text(p.name);Spacer();Text(Double(p.duration).durationText).monospacedDigit();if let met=p.met{Text("\(met,specifier:"%.1f") MET").font(.caption).foregroundStyle(.secondary)}}}
            NavigationLink("Modifica fasi e ripetizioni"){PlanEditor()}
            NavigationLink("Calendario allenamenti"){CalendarView()}
        }
        Card(title:"Cardio durante l’attività"){
            Text(model.ring.exerciseStatus).font(.subheadline).accessibilityIdentifier("ring-exercise-status")
            if !model.allowedActivities.isEmpty{
                Picker("Attività anello",selection:Binding(get:{model.settings.config.activity_id ?? -1},set:{v in model.settings.config.activity_id=v<0 ? nil:v;if let item=model.allowedActivities.first(where:{$0.0==v}){model.settings.config.activity_name=item.1}})){
                    Text("Scegli attività").tag(-1);ForEach(model.allowedActivities,id:\.0){item in Text(item.1).tag(item.0)}
                }
            }
            Text("La registrazione dedicata richiede età, FC a riposo e un’attività compatibile. Gli ultimi valori di questa funzione restano separati dal battito live.").font(.caption).foregroundStyle(.secondary)
            Button("Avvia registrazione cardio nell’anello"){Task{await model.startRingSport()}}.disabled(!model.ring.ready || model.ring.busy)
            Button("Leggi stato cardio attività"){Task{await model.ring.readExercise()}}.disabled(!model.ring.ready || model.ring.busy)
            Button("Arresta registrazione cardio"){Task{await model.stopRingSport()}}.disabled(!model.ring.ready || model.ring.busy)
        }
        Card(title:"Cardio registrato offline"){
            Text(model.ring.recordedHistorySummary).font(.subheadline)
            Text("Elabora le tracce direttamente sull’iPhone, anche senza rete. Sincronizza i risultati con FamilOS appena raggiungibile. Il grafico viene aggiornato durante la registrazione e rielaborato alla fine.").font(.caption).foregroundStyle(.secondary)
            Button("Avvia cardio registrato offline"){Task{await model.startRingSport(recordedOnly:true)}}.disabled(!model.ring.ready || model.ring.busy || model.ring.recordedWorkoutProbeActive)
        }
    }.padding()}.navigationTitle("Allenamento").dismissibleKeyboard()}}}
}
struct SleepView:View {
    @EnvironmentObject var model:AppModel;@State private var selectedID:String?
    var periods:[SleepPeriod]{model.sleepData["periods"].array.compactMap{SleepPeriod($0)}.sorted{$0.start>$1.start}}
    var body:some View{NavigationStack{ScrollView{VStack(spacing:18){
        if !model.enrolled{
            ContentUnavailableView("Associa FamilOS",systemImage:"bed.double",description:Text("Il sonno viene elaborato sul server dai record dell’anello."))
        }else if periods.isEmpty{
            if model.refreshingSleep{ProgressView("Aggiornamento sonno…")}
            else{ContentUnavailableView("Nessun periodo disponibile",systemImage:"bed.double",description:Text("I periodi compariranno dopo la sincronizzazione e l’elaborazione dello storico dell’anello."))}
        }else{
            let period=periods.first{$0.id==selectedID} ?? periods[0]
            Card(title:"Ultime due settimane"){
                Picker("Periodo",selection:Binding(get:{period.id},set:{selectedID=$0})){
                    ForEach(periods){item in Text(item.clock(item.start)).tag(item.id)}
                }
            }
            Card(title:"\(period.clock(period.start)) → \(period.clock(period.end))"){
                HStack{sleepMetric("Sonno totale",period.total);Spacer();sleepMetric("A letto",period.timeInBed)}
                if let efficiency=period.efficiency{Text("Efficienza \(efficiency,specifier:"%.0f")%").font(.subheadline).foregroundStyle(.secondary)}
                if let latency=period.latency{Text("Addormentamento: \(latency.durationText)").font(.subheadline).foregroundStyle(.secondary)}
            }
            Card(title:"SpO₂ · media sonno"){
                if let average=period.spo2["display_percentage"].number{
                    Text(String(format:"%.0f %%",average)).font(.title2.bold())
                    Text("\(Int(period.spo2["sample_count"].number ?? 0)) campioni · minimo \(Int(period.spo2["minimum"].number ?? 0))% · massimo \(Int(period.spo2["maximum"].number ?? 0))%").font(.caption).foregroundStyle(.secondary)
                }else{Text("—").font(.title2);Text("Nessun campione SpO₂ elaborato in questo periodo.").font(.caption).foregroundStyle(.secondary)}
            }
            if !period.stages.isEmpty{Card(title:"Fasi del sonno"){
                ForEach([("awake","Sveglio"),("rem","REM"),("light","Leggero"),("deep","Profondo")],id:\.0){key,name in
                    HStack{Text(name);Spacer();Text(period.stages[key].map{$0.durationText} ?? "—").monospacedDigit()}
                }
            }}
            if !period.phaseRuns.isEmpty{Card(title:"Sequenza delle fasi"){
                Chart(period.phaseRuns){run in
                    BarMark(xStart:.value("Minuti",Double(run.start)/60),xEnd:.value("Minuti",Double(run.end)/60),y:.value("Fase",run.stage))
                        .foregroundStyle(by:.value("Fase",run.stage))
                }.chartLegend(.hidden).chartXAxisLabel("Minuti nella sequenza registrata").frame(height:170)
                Text("Sequenza a intervalli di \(period.phaseStep) secondi. La copertura può essere parziale rispetto al periodo a letto.").font(.caption).foregroundStyle(.secondary)
            }}
        }
        if !model.sleepStatus.isEmpty{Text(model.sleepStatus).font(.caption).foregroundStyle(.secondary)}
        Button("Aggiorna sonno"){Task{await model.refreshSleep()}}.buttonStyle(.bordered).disabled(!model.enrolled || model.refreshingSleep)
    }.padding()}.navigationTitle("Sonno").refreshable{await model.refreshSleep()}.task{await model.refreshSleep()}}}
    func sleepMetric(_ name:String,_ seconds:Double?)->some View{VStack(alignment:.leading){Text(seconds.map{$0.durationText} ?? "—").font(.title.bold()).monospacedDigit();Text(name).font(.caption).foregroundStyle(.secondary)}}
}
struct EnergyCard:View{
    let session:WorkoutSession;let elapsed:Double
    var body:some View{let energy=WorkoutEngine.energy(to:elapsed,config:session.config,profile:session.profile)
        Card(title:"Energia stimata da peso e MET"){
            if energy.estimatedSeconds>0{HStack{VStack(alignment:.leading){Text("\(energy.gross,specifier:"%.1f") kcal").font(.title2.bold());Text("Lorde").font(.caption)};Spacer();VStack(alignment:.trailing){Text("\(energy.net,specifier:"%.1f") kcal").font(.title2.bold());Text("Nette oltre 1 MET").font(.caption)}}}
            else{Text("Peso o MET non disponibili").foregroundStyle(.secondary)}
            if energy.unestimatedSeconds>0{Text("\(energy.unestimatedSeconds.durationText) senza stima energetica").font(.caption).foregroundStyle(.secondary)}
            Text("Stima convenzionale, indipendente dal battito misurato.").font(.caption).foregroundStyle(.secondary)
        }
    }
}
struct PlanEditor:View{
    @EnvironmentObject var model:AppModel
    var body:some View{Form{
        Section("Sessione"){Stepper("Giri: \(model.settings.config.rounds)",value:$model.settings.config.rounds,in:1...100)
            if model.settings.config.plan.isEmpty{Stepper("Lavoro: \(model.settings.config.work) s",value:$model.settings.config.work,in:5...3600,step:5);Stepper("Recupero: \(model.settings.config.rest) s",value:$model.settings.config.rest,in:0...3600,step:5)}
        }
        Section("Fasi personalizzate"){
            ForEach($model.settings.config.plan){$p in VStack(alignment:.leading){TextField("Nome fase",text:$p.name);HStack{Text("Durata (secondi)");Spacer();TextField("Durata",value:$p.duration,format:.number).keyboardType(.numberPad).multilineTextAlignment(.trailing)}
                HStack{TextField("FC min (opz.)",value:$p.target_min,format:.number);TextField("FC max (opz.)",value:$p.target_max,format:.number)}.keyboardType(.numberPad)
                TextField("MET opzionale",value:$p.met,format:.number).keyboardType(.decimalPad)
            }}.onDelete{model.settings.config.plan.remove(atOffsets:$0)}.onMove{model.settings.config.plan.move(fromOffsets:$0,toOffset:$1)}
            Button("Aggiungi fase",systemImage:"plus"){model.settings.config.plan.append(WorkoutPhase())}
        }
        Section{Button("Salva scheda"){Task{await model.saveSettings()}};Text("Obiettivi e MET sono facoltativi e scelti da te. La scheda della sessione già iniziata resta quella salvata all’avvio.").font(.caption).foregroundStyle(.secondary)}
    }.navigationTitle("Scheda").dismissibleKeyboard().toolbar{EditButton()}}
}
private let diaryMetrics=[("heart_rate","Battito"),("spo2","SpO₂"),("temperature","Temperatura"),("respiratory_rate","Respiro"),("motion","Movimento"),("step_increments","Passi")]
struct MetricHistoryChart:View{
    let title:String;let points:[ChartPoint];var start:Date?=nil;var end:Date?=nil
    var domain:ClosedRange<Date>{let a=start ?? points.first?.date ?? Date(),b=end ?? points.last?.date ?? Date();return a...max(a.addingTimeInterval(1),b)}
    var body:some View{Chart(points){point in
        if title=="Battito"{LineMark(x:.value("Ora",point.date),y:.value(title,point.value),series:.value("Tratto",point.segment ?? 0)).foregroundStyle(.teal)}
        else{PointMark(x:.value("Ora",point.date),y:.value(title,point.value)).foregroundStyle(.teal).symbolSize(12)}
    }.chartXScale(domain:domain).chartYScale(domain:.automatic(includesZero:false)).frame(height:180).accessibilityLabel("Grafico \(title)")}
}
struct DiaryView:View{
    @EnvironmentObject var model:AppModel
    @State private var range:DiaryRange = .two
    @State private var analysis:JSONValue = .null
    @State private var loading=false
    var body:some View{NavigationStack{List{
        Section{
            Picker("Periodo dei grafici",selection:$range){ForEach(DiaryRange.allCases){item in Text(item.label).tag(item)}}.accessibilityIdentifier("diary-range")
            if loading{ProgressView("Caricamento grafici…")}
            if analysis["offline"] == .bool(true){Text("Dati salvati sul telefono").font(.caption).foregroundStyle(.secondary)}
        }
        ForEach(diaryMetrics,id:\.0){metric,title in
            let points=ChartSeries.decode(analysis[metric])
            if !points.isEmpty{Section(title){MetricHistoryChart(title:title,points:points,start:analysis["window_start"].number.map{Date(timeIntervalSince1970:$0)},end:analysis["window_end"].number.map{Date(timeIntervalSince1970:$0)})}}
        }
        if !loading,diaryMetrics.allSatisfy({analysis[$0.0].array.isEmpty}){Text("Nessun campione disponibile nel periodo selezionato").foregroundStyle(.secondary)}
        DisclosureGroup("Allenamenti e cronologia"){
            Text("Allenamenti · \(model.sessions.count)").font(.headline)
            if model.sessions.isEmpty{Text("Nessun allenamento salvato").foregroundStyle(.secondary)}
            ForEach(model.sessions){session in NavigationLink{SessionDetail(session:session)}label:{VStack(alignment:.leading){HStack{Text(session.activityName);Spacer();Text(session.accumulated.durationText).monospacedDigit()};Text(session.startedAt,format:.dateTime.day().month().hour().minute()).font(.caption).foregroundStyle(.secondary)}}}
            Divider()
            Text("Cronologia FamilOS").font(.headline)
            ForEach(Array(model.serverData["sessions"].array.enumerated()),id:\.offset){_,session in NavigationLink{ServerSessionDetail(session:session)}label:{VStack(alignment:.leading){Text(session["activity_name"].string ?? "Allenamento");if let stamp=session["started_at"].number{Text(Date(timeIntervalSince1970:stamp),format:.dateTime.day().month().hour().minute()).font(.caption).foregroundStyle(.secondary)}}}}
            if model.serverData["sessions"].array.isEmpty{Text("Nessuna sessione server disponibile offline").foregroundStyle(.secondary)}
        }.accessibilityIdentifier("diary-workouts-and-history")
    }.navigationTitle("Diario").refreshable{await model.refresh();analysis=await model.diaryAnalysis(hours:range.rawValue)}
    .task(id:range){analysis = .null;loading=true
        while !Task.isCancelled{
            let value=await model.diaryAnalysis(hours:range.rawValue)
            guard !Task.isCancelled else{return};analysis=value;loading=false
            do{try await Task.sleep(nanoseconds:30_000_000_000)}catch{return}
        }
    }}}
}
struct SessionDetail:View{
    @EnvironmentObject var model:AppModel
    let session:WorkoutSession
    @State private var analysis:JSONValue = .null
    @State private var loading=true
    var body:some View{ScrollView{VStack(spacing:16){
        Card(title:session.activityName){Text(session.startedAt,format:.dateTime.day().month().year().hour().minute());Text(session.accumulated.durationText).font(.largeTitle.bold())
            if let average=analysis["stats"]["average_bpm"].number,let maximum=analysis["stats"]["max_bpm"].number{Text("Media \(average,specifier:"%.0f") BPM · picco \(Int(maximum)) BPM").font(.subheadline)}
        }
        if loading{ProgressView("Caricamento grafici allenamento…")}
        ForEach(diaryMetrics,id:\.0){metric,title in
            let points=ChartSeries.bound(ChartSeries.decode(analysis[metric]).filter{$0.date>=session.startedAt && $0.date<=(session.endedAt ?? Date())})
            if !points.isEmpty{Card(title:title){MetricHistoryChart(title:title,points:points,start:session.startedAt,end:session.endedAt ?? Date())}}
        }
        if !loading,analysis == .null{Text("Grafici non ancora disponibili sul telefono").foregroundStyle(.secondary)}
        EnergyCard(session:session,elapsed:session.accumulated)
        Card(title:"Fasi della sessione"){ForEach(session.config.phases){phase in HStack{Text(phase.name);Spacer();Text(Double(phase.duration).durationText)}}}
    }.padding()}.navigationTitle("Allenamento").task(id:session.id){analysis=await model.workoutAnalysis(session);loading=false}}
}
struct RecordedSessionSummary:View {
    @EnvironmentObject var model:AppModel
    let session:WorkoutSession;@ObservedObject var recorded:RecordedCardioController
    @State private var points:[Reading]=[];@State private var average:Double?;@State private var maximum:Int?;@State private var found=false;@State private var provisional=false
    var body:some View{Group{if found{Card(title:"Cardio registrato · offline"){
        PulseChart(readings:points).frame(height:180)
        HStack{Text(average.map{"Media \(Int($0)) BPM"} ?? "Segnale insufficiente");Spacer();if let maximum{Text("Picco \(maximum) BPM")}}
        Text(provisional ? "Elaborazione provvisoria salvata sul telefono":"Elaborazione finale salvata sul telefono").font(.caption).foregroundStyle(.secondary)
    }}}.task(id:recorded.result?.revision){
        let start=session.startedAt,end=session.endedAt ?? Date()
        let values=(try? await model.store.perform{$0.recordedResults(start:start,end:end)}) ?? []
        let prepared=await Task.detached(priority:.utility){
            let rows=values.flatMap{$0.acceptedReadings}.filter{$0.date>=start && $0.date<=end}.sorted{$0.date<$1.date}
            let average=rows.isEmpty ? nil:Double(rows.reduce(0){$0+$1.bpm})/Double(rows.count)
            return(AppModel.displayReadings(rows),average,rows.map{$0.bpm}.max())
        }.value
        found = !values.isEmpty;provisional=values.contains{!$0.final};points=prepared.0;average=prepared.1;maximum=prepared.2
    }}
}
struct CalendarView:View{
    @EnvironmentObject var model:AppModel
    var body:some View{NavigationStack{List{
        Section("Prossime tre settimane · Europe/Rome"){
            ForEach(Array(WorkoutEngine.occurrences(rules:model.settings.weekly_schedule,after:model.now).enumerated()),id:\.offset){_,item in VStack(alignment:.leading,spacing:5){Text(romeDate(item.1,pattern:"EEEE d MMMM"));Text(romeDate(item.1,pattern:"HH:mm z")+" – "+romeDate(item.2,pattern:"HH:mm")).foregroundStyle(.secondary)}}
        }
        Section{Text("Il calendario è un promemoria della pianificazione. Non avvia da solo una sessione.").font(.caption).foregroundStyle(.secondary)}
        Section("Orari settimanali"){
            ForEach(Array(model.settings.weekly_schedule.indices),id:\.self){i in WeeklyEditor(rule:$model.settings.weekly_schedule[i])}
            Button("Salva calendario"){Task{await model.saveSettings()}}
        }
    }.navigationTitle("Calendario").dismissibleKeyboard()}}
}
struct WeeklyEditor:View{
    @Binding var rule:WeeklyRule
    let names=["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]
    func clock(_ value:Binding<String>)->Binding<Date>{Binding(get:{let f=DateFormatter();f.timeZone=TimeZone(identifier:"Europe/Rome");f.dateFormat="HH:mm";return f.date(from:value.wrappedValue) ?? Date()},set:{date in let f=DateFormatter();f.timeZone=TimeZone(identifier:"Europe/Rome");f.dateFormat="HH:mm";value.wrappedValue=f.string(from:date)})}
    var body:some View{VStack{Picker("Giorno",selection:$rule.weekday){ForEach(1...7,id:\.self){Text(names[$0-1]).tag($0)}};HStack{DatePicker("Inizio",selection:clock($rule.start),displayedComponents:.hourAndMinute);DatePicker("Fine",selection:clock($rule.end),displayedComponents:.hourAndMinute)}}.environment(\.timeZone,TimeZone(identifier:"Europe/Rome")!)}
}
struct SettingsView:View{
    @EnvironmentObject var model:AppModel
    @State var code="";@State var server="https://familos.example/api/wellness/v1";@State var scanning=false
    var body:some View{NavigationStack{Form{
        Section("FamilOS"){
            if model.enrolled{Label(model.ownerName ?? "Dispositivo associato",systemImage:"checkmark.shield");Text(model.syncStatus).font(.caption);TransferStatusText(progress:model.transfer)}else{
                TextField("Indirizzo server",text:$server).textInputAutocapitalization(.never).keyboardType(.URL)
                TextField("Codice o link di associazione",text:$code).textInputAutocapitalization(.never).autocorrectionDisabled().privacySensitive()
                Button("Associa questo iPhone"){Task{if let url=URL(string:code),url.scheme=="familos-homepulse"{await model.openPairingURL(url)}else{await model.enroll(code:code,server:server)};code=""}}
                Button("Scansiona QR",systemImage:"qrcode.viewfinder"){scanning=true}
            }
        }
        Section("Monitoraggio cardio"){
            Toggle("Cardio attività anche fuori allenamento",isOn:Binding(get:{model.continuousCardio},set:{enabled in Task{await model.setContinuousCardio(enabled)}}))
            Text(model.continuousStatus).font(.caption).foregroundStyle(.secondary)
            Text("Inizia e Termina delimitano le sessioni nel diario. Il monitoraggio esteso continua separatamente e consuma più batteria; viene sospeso durante la carica.").font(.caption).foregroundStyle(.secondary)
        }
        Section("Anello e Bluetooth"){RingSettingsControls(ring:model.ring)}
        Section("Batteria anello"){
            Text(model.batteryLevel.map{"Ultima lettura: \($0)%"} ?? "In attesa della prima lettura")
            Text(model.batteryNotificationStatus).font(.caption)
            Button("Aggiorna batteria"){Task{await model.ring.refreshBattery()}}.disabled(!model.ring.ready || model.ring.busy)
            Button("Abilita notifiche batteria"){Task{await model.requestBatteryNotifications()}}
            Text("Un avviso quando la carica è completa e uno al 10% o meno. Si riattiva dopo una lettura di ricarica almeno al 20%. Se l’anello è scollegato, livello e stato mostrati restano gli ultimi ricevuti.").font(.caption).foregroundStyle(.secondary)
        }
        Section("Profilo · nessun valore presunto"){
            Picker("Parametro fisiologico · energia",selection:$model.settings.profile.energy_sex){Text("Non specificato").tag(String?.none);Text("Maschile").tag(String?.some("male"));Text("Femminile").tag(String?.some("female"))}
            TextField("Peso (kg)",value:$model.settings.profile.weight_kg,format:.number).keyboardType(.decimalPad)
            TextField("Età",value:$model.settings.profile.age,format:.number).keyboardType(.numberPad)
            TextField("Altezza (cm)",value:$model.settings.profile.height_cm,format:.number).keyboardType(.decimalPad)
            TextField("FC a riposo (BPM)",value:$model.settings.profile.resting_hr,format:.number).keyboardType(.numberPad)
            TextField("Massa grassa (%)",value:$model.settings.profile.body_fat_pct,format:.number).keyboardType(.decimalPad)
            TextField("Massa magra dichiarata (%)",value:$model.settings.profile.reported_lean_pct,format:.number).keyboardType(.decimalPad)
            TextField("Ritenzione dichiarata (%)",value:$model.settings.profile.reported_retention_pct,format:.number).keyboardType(.decimalPad)
            TextField("Obiettivo",text:optionalText($model.settings.profile.goal))
            TextField("Fonte composizione corporea",text:optionalText($model.settings.profile.composition_source))
        }
        Section("Palestra e rilevamento"){
            Toggle("Rileva l’inizio in palestra",isOn:$model.settings.auto_detection.enabled)
            Text(model.settings.auto_detection.gym.name).font(.headline);Text(model.settings.auto_detection.gym.address).font(.caption)
            NavigationLink("Posizione palestra e permessi"){GymSettingsView(gym:model.gym)}
            Stepper("Aumento: +\(model.settings.auto_detection.heart_rate_delta_bpm) BPM",value:$model.settings.auto_detection.heart_rate_delta_bpm,in:5...60)
            Stepper("Durata aumento: \(model.settings.auto_detection.sustain_seconds) s",value:$model.settings.auto_detection.sustain_seconds,in:10...180,step:5)
            Stepper("Attesa tra rilevamenti: \(model.settings.auto_detection.cooldown_seconds/60) min",value:Binding(get:{model.settings.auto_detection.cooldown_seconds/60},set:{model.settings.auto_detection.cooldown_seconds=$0*60}),in:5...180,step:5)
            Text("Regola tecnica modificabile, non zona cardiaca. Usa un livello recente di riferimento e almeno 6 campioni validi; non parte da una vecchia lettura.").font(.caption).foregroundStyle(.secondary)
        }
        Section{Button("Salva impostazioni"){Task{await model.saveSettings()}};Text("Le credenziali restano nel Keychain. La raccolta in background dipende dalle autorizzazioni e dai risvegli concessi da iOS.").font(.caption).foregroundStyle(.secondary)}
    }.navigationTitle("Impostazioni").dismissibleKeyboard().sheet(isPresented:$scanning){QRScanner{value in scanning=false;code=value;Task{if let url=URL(string:value){await model.openPairingURL(url)};code=""}}}}}
}
struct GymSettingsView:View{
    @ObservedObject var gym:GymDetection
    @State var latitude="";@State var longitude=""
    var body:some View{Form{
        Section("Autorizzazioni"){
            Text(gym.permission);Text(gym.notificationPermission)
            Button("Autorizza posizione e notifiche"){gym.requestPermissions()}
            Text("Solo ingresso e uscita dall’area della palestra. Nessun tracciamento continuo del percorso.").font(.caption).foregroundStyle(.secondary)
        }
        Section("Controlla il punto"){
            Button("Trova l’indirizzo della palestra"){Task{await gym.locateGym()}}
            Text(gym.status).font(.caption)
            if let coordinate=gym.location{
                MapReader{proxy in Map(initialPosition:.region(MKCoordinateRegion(center:coordinate,latitudinalMeters:1200,longitudinalMeters:1200))){Marker(gym.engine.settings.gym.name,coordinate:coordinate)}.frame(height:260).onTapGesture{point in if let new=proxy.convert(point,from:.local){try? gym.setCoordinate(new)}}}
                Text("Tocca la mappa per correggere il punto.").font(.caption)
            }
            TextField("Latitudine",text:$latitude).keyboardType(.numbersAndPunctuation)
            TextField("Longitudine",text:$longitude).keyboardType(.numbersAndPunctuation)
            Button("Usa queste coordinate"){if let lat=Double(latitude.replacingOccurrences(of:",",with:".")),let lon=Double(longitude.replacingOccurrences(of:",",with:".")){try? gym.setCoordinate(CLLocationCoordinate2D(latitude:lat,longitude:lon))}}
        }
    }.navigationTitle("Palestra").dismissibleKeyboard().onAppear{if let c=gym.location{latitude=String(c.latitude);longitude=String(c.longitude)}}}
}
func optionalText(_ binding:Binding<String?>)->Binding<String>{Binding(get:{binding.wrappedValue ?? ""},set:{binding.wrappedValue=$0.nilIfEmpty})}
extension Double {var durationText:String{let value=max(0,Int(self));return value>=3600 ? String(format:"%d:%02d:%02d",value/3600,(value/60)%60,value%60):String(format:"%02d:%02d",value/60,value%60)}}

func romeDate(_ date:Date,pattern:String)->String{let formatter=DateFormatter();formatter.locale=Locale(identifier:"it_IT");formatter.timeZone=TimeZone(identifier:"Europe/Rome");formatter.dateFormat=pattern;return formatter.string(from:date)}

struct ServerSessionDetail:View{
    @EnvironmentObject var model:AppModel
    let session:JSONValue
    @State private var analysis:JSONValue = .null
    var body:some View{List{
        Section("Sessione FamilOS"){
            Text(session["activity_name"].string ?? "Allenamento")
            if let time=session["started_at"].number{Text(Date(timeIntervalSince1970:time),format:.dateTime.day().month().year().hour().minute())}
            if let elapsed=session["active_seconds"].number{Text(elapsed.durationText).font(.title)}
        }
        ForEach(diaryMetrics,id:\.0){metric,title in
            let start=session["started_at"].number ?? 0,end=session["ended_at"].number ?? Date().timeIntervalSince1970
            let points=ChartSeries.bound(ChartSeries.decode(analysis[metric]).filter{$0.date.timeIntervalSince1970>=start && $0.date.timeIntervalSince1970<=end})
            if !points.isEmpty{Section(title){MetricHistoryChart(title:title,points:points)}}
        }
        if analysis["cardio"] != .null{Section("Fasce cardio"){
            let cardio=analysis["cardio"]
            if (cardio["target_seconds"].number ?? 0)>0{
                ForEach([("inside_seconds","Dentro fascia"),("below_seconds","Sotto fascia"),("above_seconds","Sopra fascia"),("unknown_seconds","Senza misure")],id:\.0){key,label in
                    HStack{Text(label);Spacer();Text((cardio[key].number ?? 0).durationText).monospacedDigit()}
                }
                ForEach(Array(cardio["phases"].array.enumerated()),id:\.offset){_,phase in
                    VStack(alignment:.leading){Text(phase["name"].string ?? "Fase").font(.headline)
                        Text("\(Int(phase["target_min"].number ?? 0))–\(Int(phase["target_max"].number ?? 0)) BPM · dentro \((phase["inside_seconds"].number ?? 0).durationText)")}
                }
            }else{Text("Nessuna fascia impostata nella scheda di questa sessione.")}
            Text("Pause e vuoti del segnale sono esclusi dalle fasce.").font(.caption).foregroundStyle(.secondary)
        }}
        if analysis == .null{Text("Grafici non ancora disponibili sul telefono").foregroundStyle(.secondary)}
    }.navigationTitle("Riepilogo").task{if let id=session["id"].string{analysis=await model.sessionAnalysis(id)}}}
}
