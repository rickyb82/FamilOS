//! IdeaCraft headless.
//!
//! ```text
//! ideacraft-cli commands                                   # every command and its parameters
//! ideacraft-cli templates                                  # built-in templates
//! ideacraft-cli run [--in FILE | --sample | --template ID] [--cmd 'id={json}']… [--out FILE]
//! ideacraft-cli convert IN OUT                             # import/export by extension
//! ideacraft-cli outline FILE                               # Markdown outline on stdout
//! ideacraft-cli check FILE [--lang it]                   # validate a network diagram (ip, vlan, interfaces)
//! ideacraft-cli mcp [--in FILE] [--automation-read-root DIR] [--automation-write-root DIR]
//!                                                          # MCP server on stdio, for AI agents
//! ```
//! `--in` and `convert` read IdeaCraft maps and every importable format (FreeMind/Freeplane,
//! XMind, CmapTools CXL, OPML, Markdown, Mermaid); `--out`/`convert` write by extension:
//! `.ideacraft.json`/`.json`, `.md`, `.opml` or `.mm`.
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unimplemented, clippy::todo, clippy::unreachable)]

use std::path::Path;
use std::process::ExitCode;

use ideacraft_engine::core::Map;
use ideacraft_engine::formats::{self, templates};
use ideacraft_engine::{COMMANDS, Session};
use serde_json::{Value, json};

const USAGE: &str = "usage:
  ideacraft-cli commands
  ideacraft-cli templates
  ideacraft-cli run [--in FILE | --sample | --template ID] [--cmd 'id={json}']… [--out FILE]
  ideacraft-cli convert IN OUT
  ideacraft-cli outline FILE
  ideacraft-cli check FILE [--lang it]   (validate a network diagram: ip, subnets, vlan, gateways, interfaces)
  ideacraft-cli mcp [--in FILE | FILE] [--template ID] [--automation-read-root DIR] [--automation-write-root DIR]
                     (Model Context Protocol server on stdio; the agent reads files only inside the read
                     root and writes only inside the write root, none without them)";

fn read(path: &str) -> Result<Map, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("{path}: {e}"))?;
    formats::import_bytes(path, &bytes).map(|(m, _)| m).map_err(|e| format!("{path}: {e}"))
}

/// The map in the format the file name asks for.
fn render(map: &Map, path: &str) -> String {
    let lower = path.to_ascii_lowercase();
    if lower.ends_with(".md") || lower.ends_with(".markdown") || lower.ends_with(".txt") {
        formats::to_markdown(map)
    } else if lower.ends_with(".opml") {
        formats::to_opml(map)
    } else if lower.ends_with(".mm") {
        formats::to_freemind(map)
    } else if lower.ends_with(".svg") {
        formats::to_svg(map)
    } else {
        map.to_json()
    }
}

fn write(map: &Map, path: &str) -> Result<(), String> {
    std::fs::write(path, render(map, path)).map_err(|e| format!("{path}: {e}"))
}

fn run(args: &[String]) -> Result<(), String> {
    match args.first().map(String::as_str) {
        Some("commands") => {
            for c in COMMANDS {
                println!("{:<22} {:<14} {}", c.id, c.shortcut, c.params);
            }
            Ok(())
        }
        Some("templates") => {
            for t in templates::ALL {
                println!("{:<14} {:<18} {}", t.id, t.name, t.description);
            }
            Ok(())
        }
        Some("mcp") => {
            let mut server = ideacraft_mcp::Server::new();
            let (mut read_root, mut write_root): (Option<String>, Option<String>) = (None, None);
            let mut it = args.iter().skip(1);
            while let Some(a) = it.next() {
                let (flag, inline) = match a.split_once('=') {
                    Some((f, v)) if f.starts_with("--automation-") => (f, Some(v.to_string())),
                    _ => (a.as_str(), None),
                };
                if let Some(slot) = match flag {
                    "--automation-read-root" => Some(&mut read_root),
                    "--automation-write-root" => Some(&mut write_root),
                    _ => None,
                } {
                    let dir = match inline {
                        Some(v) => v,
                        None => it.next().cloned().ok_or_else(|| format!("mcp: {flag} needs a folder"))?,
                    };
                    if slot.replace(dir).is_some() {
                        return Err(format!("mcp: {flag} given more than once"));
                    }
                    continue;
                }
                match a.as_str() {
                    "--in" | "--open" | "--file" => {
                        let path = it.next().ok_or("--in needs a file")?;
                        server.open_file(path).map_err(|e| format!("mcp: {e}"))?;
                    }
                    "--sample" => {
                        let id = it.next().map_or("brainstorm", String::as_str);
                        server.call("new_map", &json!({"template": id})).map_err(|e| format!("mcp: {e}"))?;
                    }
                    "--template" => {
                        let id = it.next().ok_or("--template needs an id (see `ideacraft-cli templates`)")?;
                        server.call("new_map", &json!({"template": id})).map_err(|e| format!("mcp: {e}"))?;
                    }
                    other if !other.starts_with('-') => {
                        server.open_file(other).map_err(|e| format!("mcp: {e}"))?;
                    }
                    other => return Err(format!("mcp: unknown option {other}\n{USAGE}")),
                }
            }
            // `--in` files are the operator's own, opened above before the roots apply.
            let roots = ideacraft_mcp::roots::AutomationRoots::new(read_root.as_deref().map(Path::new), write_root.as_deref().map(Path::new))
                .map_err(|e| format!("mcp: {e}"))?;
            let show = |p: Option<&Path>| p.map_or_else(|| "none".to_string(), |p| p.display().to_string());
            eprintln!("ideacraft-cli: files confined: read root {}, write root {}", show(roots.read_root()), show(roots.write_root()));
            server.confine(roots);
            eprintln!("ideacraft-cli: MCP server on stdio (protocol {}); close stdin to stop", ideacraft_mcp::PROTOCOL_VERSION);
            let stdin = std::io::stdin();
            server.serve(stdin.lock(), std::io::stdout()).map_err(|e| format!("mcp: {e}"))
        }
        Some("outline") => {
            let path = args.get(1).ok_or(USAGE)?;
            print!("{}", formats::to_markdown(&read(path)?));
            Ok(())
        }
        Some("check") => {
            let path = args.get(1).ok_or(USAGE)?;
            let italian = args.iter().skip(2).any(|a| a == "it" || a == "--lang=it") || args.windows(2).any(|w| w[0] == "--lang" && w[1] == "it");
            let map = read(path)?;
            let findings = ideacraft_engine::core::netcheck::validate(&map);
            for f in &findings {
                let sev = match f.severity {
                    ideacraft_engine::core::netcheck::Severity::Error => "ERROR",
                    ideacraft_engine::core::netcheck::Severity::Warning => "WARN ",
                    ideacraft_engine::core::netcheck::Severity::Info => "info ",
                };
                println!("{sev} [{}] {}", f.code.name(), f.message(italian));
            }
            let errors = findings.iter().filter(|f| f.severity == ideacraft_engine::core::netcheck::Severity::Error).count();
            println!("{} findings, {errors} errors", findings.len());
            if errors > 0 { Err(String::new()) } else { Ok(()) }
        }
        Some("convert") => {
            let (Some(input), Some(output)) = (args.get(1), args.get(2)) else { return Err(USAGE.into()) };
            write(&read(input)?, output)
        }
        Some("run") => {
            let mut session = Session::default();
            let mut out = None;
            let mut it = args.iter().skip(1);
            while let Some(flag) = it.next() {
                match flag.as_str() {
                    "--sample" => session = Session::new(Map::sample()),
                    "--template" => {
                        let id = it.next().ok_or("--template needs an id (see `ideacraft-cli templates`)")?;
                        session = Session::new(templates::find(id).ok_or_else(|| format!("no template {id:?}"))?.build());
                    }
                    "--in" => session = Session::new(read(it.next().ok_or("--in needs a file")?)?),
                    "--out" => out = Some(it.next().ok_or("--out needs a file")?.clone()),
                    "--cmd" => {
                        let spec = it.next().ok_or("--cmd needs id={json}")?;
                        let (id, params) = spec.split_once('=').unwrap_or((spec, "{}"));
                        let params: Value = serde_json::from_str(params).map_err(|e| format!("{id}: bad JSON: {e}"))?;
                        let result = session.execute(id, &params).map_err(|e| format!("{id}: {e}"))?;
                        println!("{}", json!({"command": id, "result": result}));
                    }
                    other => return Err(format!("unknown option {other}\n{USAGE}")),
                }
            }
            match out {
                Some(path) => write(session.map(), &path),
                None => Ok(()),
            }
        }
        _ => Err(USAGE.to_string()),
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            if !e.is_empty() {
                eprintln!("{e}");
            }
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn render_picks_the_format_from_the_extension() {
        let map = Map::sample();
        assert!(render(&map, "a.md").starts_with("# IdeaCraft\n\n- Canvas"));
        assert!(render(&map, "a.OPML").contains("<opml"));
        assert!(render(&map, "a.mm").starts_with("<map"));
        assert!(render(&map, "a.ideacraft.json").contains("\"ideacraft\""));
    }

    #[test]
    fn bad_arguments_are_errors_not_panics() {
        for args in [
            vec![],
            vec!["run", "--in"],
            vec!["run", "--cmd", "node.add={"],
            vec!["run", "--cmd", "nope"],
            vec!["outline"],
            vec!["check"],
            vec!["check", "/nope.mm"],
            vec!["run", "--template", "x"],
            vec!["convert", "a"],
        ] {
            let args: Vec<String> = args.into_iter().map(String::from).collect();
            assert!(run(&args).is_err(), "{args:?}");
        }
    }

    #[test]
    fn mcp_root_flags_are_checked_before_the_server_starts() {
        let dir = std::env::temp_dir().to_string_lossy().into_owned();
        for (args, says) in [
            (vec!["mcp", "--automation-read-root", "/definitely/not/a/folder"], "read root"),
            (vec!["mcp", "--automation-write-root=/definitely/not/a/folder"], "write root"),
            (vec!["mcp", "--automation-read-root"], "needs a folder"),
            (vec!["mcp", "--automation-write-root="], "write root"),
            (vec!["mcp", "--automation-read-root", &dir, "--automation-read-root", &dir], "more than once"),
        ] {
            let args: Vec<String> = args.into_iter().map(String::from).collect();
            let e = run(&args).unwrap_err();
            assert!(e.contains(says), "{args:?}: {e}");
        }
    }
}
