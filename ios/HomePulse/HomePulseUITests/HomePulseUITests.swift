import XCTest

final class HomePulseUITests:XCTestCase {
    func testNavigationRemainsResponsiveWhileArchiveIsLocked(){
        continueAfterFailure=false
        let app=XCUIApplication();app.launchArguments=["--uitest-isolated-store","--uitest-storage-stall"];app.launch()
        XCTAssertTrue(app.staticTexts["archive-stall-active"].waitForExistence(timeout:15))
        let start=Date()
        app.tabBars.buttons["Impostazioni"].tap();XCTAssertTrue(app.navigationBars["Impostazioni"].waitForExistence(timeout:2))
        app.tabBars.buttons["Diario"].tap();XCTAssertTrue(app.navigationBars["Diario"].waitForExistence(timeout:2))
        app.tabBars.buttons["Home"].tap();XCTAssertTrue(app.navigationBars["Home Pulse"].waitForExistence(timeout:2))
        XCTAssertLessThan(Date().timeIntervalSince(start),12)
        XCTAssertTrue(app.staticTexts["archive-stall-active"].exists,"Navigation must finish before the 15-second database stall ends")
        capture(app,"responsive-during-database-stall")
    }
    func capture(_ app:XCUIApplication,_ name:String){let attachment=XCTAttachment(screenshot:app.screenshot());attachment.name=name;attachment.lifetime = .keepAlways;add(attachment)}
    func testOfflineNavigationAndWorkoutLifecycle(){
        continueAfterFailure=false
        let app=XCUIApplication();app.launchArguments=["--uitest-isolated-store"];app.launch()
        XCTAssertTrue(app.navigationBars["Home Pulse"].waitForExistence(timeout:15));capture(app,"01-home")
        app.buttons["Allenamento"].tap()
        XCTAssertTrue(app.buttons["Inizia allenamento"].waitForExistence(timeout:5));capture(app,"02-allenamento")
        app.buttons["Inizia allenamento"].tap()
        XCTAssertTrue(app.buttons["Pausa"].waitForExistence(timeout:5));capture(app,"03-sessione-offline")
        app.buttons["Pausa"].tap();XCTAssertTrue(app.buttons["Riprendi"].waitForExistence(timeout:5))
        app.buttons["Riprendi"].tap();XCTAssertTrue(app.buttons["Pausa"].waitForExistence(timeout:5))
        app.buttons["Termina"].tap();XCTAssertTrue(app.buttons["Inizia allenamento"].waitForExistence(timeout:5))
        app.buttons["Diario"].tap();XCTAssertTrue(app.navigationBars["Diario"].exists);capture(app,"04-diario")
        app.tabBars.buttons["Sonno"].tap();XCTAssertTrue(app.navigationBars["Sonno"].exists);capture(app,"05-sonno")
        app.buttons["Impostazioni"].tap();XCTAssertTrue(app.navigationBars["Impostazioni"].exists);capture(app,"06-impostazioni")
        app.terminate();app.launch()
        app.buttons["Diario"].tap();XCTAssertTrue(app.staticTexts["Allenamento"].firstMatch.exists)
    }
}
