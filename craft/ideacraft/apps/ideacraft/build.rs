//! Windows only: embed the app icon and version info (VERSIONINFO) into `ideacraft.exe`, so it
//! shows in Explorer, the taskbar, the Start menu and Alt-Tab.
//!
//! On every other target this does nothing. A missing resource compiler is a warning, so a
//! cross-compile from macOS or Linux still links, unless `IDEACRAFT_REQUIRE_WINRES=1` turns it
//! into an error (packaging/windows/package.ps1 sets it for release builds).

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=../../assets/app-icon/ideacraft.ico");
    println!("cargo:rerun-if-env-changed=IDEACRAFT_REQUIRE_WINRES");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let mut res = winresource::WindowsResource::new();
    res.set_icon("../../assets/app-icon/ideacraft.ico")
        .set("ProductName", "IdeaCraft")
        .set("FileDescription", "IdeaCraft concept maps and diagrams")
        .set("CompanyName", "IdeaCraft contributors")
        .set("LegalCopyright", "Copyright (c) the IdeaCraft contributors. MIT OR Apache-2.0.")
        .set("OriginalFilename", "ideacraft.exe")
        .set("InternalName", "ideacraft");
    if let Err(e) = res.compile() {
        if std::env::var_os("IDEACRAFT_REQUIRE_WINRES").is_some() {
            eprintln!("embedding Windows resources failed: {e}");
            std::process::exit(1);
        }
        println!("cargo:warning=ideacraft.exe built without icon/version resources: {e}");
    }
}
