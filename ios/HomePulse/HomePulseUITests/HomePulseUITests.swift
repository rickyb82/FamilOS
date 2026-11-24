import XCTest

final class HomePulseUITests:XCTestCase {
    func capture(_ app:XCUIApplication,_ name:String){let attachment=XCTAttachment(screenshot:app.screenshot());attachment.name=name;attachment.lifetime = .keepAlways;add(attachment)}
    func testOfflineNavigationAndWorkoutLifecycle(){
        continueAfterFailure=false
        let app=XCUIApplication();app.launchArguments=["--uitest-isolated-store"];app.launch()
        XCTAssertTrue(app.navigationBars["Home Pulse"].waitForExistence(timeout:15));capture(app,"01-home")
        app.buttons["Allenamento"].tap()
        XCTAssertTrue(app.buttons["Inizia allenamento"].waitForExistence(timeout:5));capture(app,"02-allenamento")
        app.buttons["Inizia allenamento"].tap()
        XCTAssertTrue(app.buttons["Pausa"].waitForExistence(timeout:5));capture(app,"03-sessione-offline")
        app.buttons["Pausa"].tap();XCTAssertTrue(app.buttons["Riprendi"].exists)
        app.buttons["Riprendi"].tap();XCTAssertTrue(app.buttons["Pausa"].exists)
        app.buttons["Termina"].tap();XCTAssertTrue(app.buttons["Inizia allenamento"].waitForExistence(timeout:5))
        app.buttons["Diario"].tap();XCTAssertTrue(app.navigationBars["Diario"].exists);capture(app,"04-diario")
        app.buttons["Calendario"].tap();XCTAssertTrue(app.navigationBars["Calendario"].exists);capture(app,"05-calendario")
        app.buttons["Impostazioni"].tap();XCTAssertTrue(app.navigationBars["Impostazioni"].exists);capture(app,"06-impostazioni")
        app.terminate();app.launch()
        app.buttons["Diario"].tap();XCTAssertTrue(app.staticTexts["Allenamento"].firstMatch.exists)
    }
}
