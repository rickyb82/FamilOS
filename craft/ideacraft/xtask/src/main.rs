//! Repository tasks: `cargo xtask ci` (fmt, clippy, tests, layers, assets, wasm), `cargo xtask
//! layers`, `cargo xtask assets`, `cargo xtask wasm` and `cargo xtask icons` (renders the app icon).
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

mod icons;

/// Each crate and the internal crates / UI toolkits it may depend on. Lower layers never reach up.
const LAYERS: &[(&str, &[&str])] = &[
    ("crates/core", &[]),
    ("crates/formats", &["ideacraft-core"]),
    ("crates/engine", &["ideacraft-core", "ideacraft-formats"]),
    ("crates/ui-egui", &["ideacraft-engine", "egui"]),
    ("crates/mcp", &["ideacraft-engine"]),
    ("apps/ideacraft", &["ideacraft-ui-egui", "egui", "eframe", "rfd"]),
    ("apps/ideacraft-cli", &["ideacraft-engine", "ideacraft-mcp"]),
    ("apps/ideacraft-web", &["ideacraft-ui-egui", "egui", "eframe", "rfd"]),
];
const WATCHED: &[&str] =
    &["ideacraft-core", "ideacraft-formats", "ideacraft-mcp", "ideacraft-engine", "ideacraft-ui-egui", "egui", "eframe", "winit", "rfd"];

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().map(Path::to_path_buf).unwrap_or_default()
}

fn layers() -> Result<(), String> {
    let mut bad = Vec::new();
    for (krate, allowed) in LAYERS {
        let manifest = root().join(krate).join("Cargo.toml");
        let text = std::fs::read_to_string(&manifest).map_err(|e| format!("{}: {e}", manifest.display()))?;
        let mut in_deps = false;
        for line in text.lines() {
            let line = line.trim();
            if line.starts_with('[') {
                in_deps = line == "[dependencies]";
                continue;
            }
            let name = line.split(['=', ' ', '.']).next().unwrap_or_default();
            if in_deps && WATCHED.contains(&name) && !allowed.contains(&name) {
                bad.push(format!("{krate} must not depend on {name}"));
            }
        }
    }
    if bad.is_empty() { Ok(()) } else { Err(bad.join("\n")) }
}

fn cargo(args: &[&str]) -> Result<(), String> {
    eprintln!("$ cargo {}", args.join(" "));
    let status = Command::new(env!("CARGO")).args(args).current_dir(root()).status().map_err(|e| e.to_string())?;
    if status.success() { Ok(()) } else { Err(format!("cargo {} failed", args.join(" "))) }
}

fn ci() -> Result<(), String> {
    cargo(&["fmt", "--all", "--", "--check"])?;
    cargo(&["clippy", "--workspace", "--all-targets", "--", "-D", "warnings"])?;
    cargo(&["test", "--workspace"])?;
    layers()?;
    assets()?;
    wasm()
}

/// Every file under `assets/` has a row in ATTRIBUTION.md (author, source, licence).
fn assets() -> Result<(), String> {
    fn walk(dir: &Path, out: &mut Vec<PathBuf>) -> Result<(), String> {
        for entry in std::fs::read_dir(dir).map_err(|e| format!("{}: {e}", dir.display()))? {
            let path = entry.map_err(|e| e.to_string())?.path();
            if path.is_dir() { walk(&path, out)? } else { out.push(path) }
        }
        Ok(())
    }
    let root = root();
    let attribution = std::fs::read_to_string(root.join("ATTRIBUTION.md")).map_err(|e| format!("ATTRIBUTION.md: {e}"))?;
    let mut files = Vec::new();
    walk(&root.join("assets"), &mut files)?;
    let missing: Vec<String> = files
        .iter()
        .filter_map(|f| f.strip_prefix(&root).ok())
        .map(|rel| rel.to_string_lossy().replace('\\', "/"))
        .filter(|rel| !attribution.contains(&format!("`{rel}`")))
        .collect();
    if missing.is_empty() { Ok(()) } else { Err(format!("assets without a row in ATTRIBUTION.md:\n{}", missing.join("\n"))) }
}

/// The web app and everything under it build for wasm32 with clippy clean.
fn wasm() -> Result<(), String> {
    cargo(&["clippy", "-p", "ideacraft-web", "--target", "wasm32-unknown-unknown", "--", "-D", "warnings"])
}

fn main() -> ExitCode {
    let task = std::env::args().nth(1).unwrap_or_default();
    let result = match task.as_str() {
        "ci" => ci(),
        "layers" => layers(),
        "assets" => assets(),
        "wasm" => wasm(),
        "icons" => icons::run(),
        _ => Err("usage: cargo xtask <ci|layers|assets|wasm|icons>".to_string()),
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("{e}");
            ExitCode::FAILURE
        }
    }
}
