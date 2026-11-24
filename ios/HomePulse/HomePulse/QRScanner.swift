import SwiftUI
import AVFoundation

struct QRScanner:UIViewControllerRepresentable {
    var onCode:(String)->Void
    func makeUIViewController(context:Context)->ScannerController{let c=ScannerController();c.onCode=onCode;return c}
    func updateUIViewController(_ uiViewController:ScannerController,context:Context){}
}
final class ScannerController:UIViewController,AVCaptureMetadataOutputObjectsDelegate {
    var onCode:((String)->Void)?
    private let capture=AVCaptureSession();private var delivered=false
    override func viewDidLoad(){super.viewDidLoad();view.backgroundColor = .black
        let label=UILabel();label.text="Inquadra il QR di FamilOS";label.textColor = .white;label.textAlignment = .center;label.translatesAutoresizingMaskIntoConstraints=false;view.addSubview(label)
        NSLayoutConstraint.activate([label.topAnchor.constraint(equalTo:view.safeAreaLayoutGuide.topAnchor,constant:24),label.centerXAnchor.constraint(equalTo:view.centerXAnchor)])
        AVCaptureDevice.requestAccess(for:.video){[weak self] allowed in DispatchQueue.main.async{guard let self else{return};if allowed{self.configure()}else{label.text="Fotocamera non autorizzata: puoi incollare il codice"}}}
    }
    private func configure(){
        guard let device=AVCaptureDevice.default(for:.video),let input=try? AVCaptureDeviceInput(device:device),capture.canAddInput(input) else{return}
        capture.addInput(input);let output=AVCaptureMetadataOutput();guard capture.canAddOutput(output) else{return};capture.addOutput(output)
        output.setMetadataObjectsDelegate(self,queue:.main);output.metadataObjectTypes=[.qr]
        let preview=AVCaptureVideoPreviewLayer(session:capture);preview.videoGravity = .resizeAspectFill;preview.frame=view.bounds;view.layer.insertSublayer(preview,at:0)
        let session=capture;DispatchQueue.global(qos:.userInitiated).async{session.startRunning()}
    }
    override func viewDidDisappear(_ animated:Bool){super.viewDidDisappear(animated);let session=capture;DispatchQueue.global().async{session.stopRunning()}}
    func metadataOutput(_ output:AVCaptureMetadataOutput,didOutput metadataObjects:[AVMetadataObject],from connection:AVCaptureConnection){guard !delivered,let value=(metadataObjects.first as? AVMetadataMachineReadableCodeObject)?.stringValue else{return};delivered=true;onCode?(value)}
}
