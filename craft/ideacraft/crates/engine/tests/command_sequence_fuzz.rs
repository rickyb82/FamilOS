//! Random command sequences must never panic, and must keep undo/redo and the file format exact.
//! Soak with `IDEACRAFT_FUZZ_SEEDS`/`IDEACRAFT_FUZZ_STEPS`; replay one seed with `IDEACRAFT_FUZZ_SEED`.

use ideacraft_engine::core::Map;
use ideacraft_engine::{COMMANDS, Session, command};
use serde_json::{Value, json};

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
    fn below(&mut self, n: u64) -> u64 {
        self.next() % n.max(1)
    }
}

fn env(name: &str, default: u64) -> u64 {
    std::env::var(name).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

fn random_params(r: &mut Rng, s: &Session) -> Value {
    let ids: Vec<u64> = s.map().nodes().map(|n| n.id.0).collect();
    let pick = |r: &mut Rng| if ids.is_empty() || r.below(10) == 0 { r.below(1000) } else { ids[r.below(ids.len() as u64) as usize] };
    let a = pick(r);
    let b = pick(r);
    let colors = ["#ffe08a", "#B3D4FF", "nope", ""];
    let shapes = ["rounded", "rect", "ellipse", "pill", "star"];
    let kinds = ["net.router", "flow.decision", "basic.container", "nope"];
    let edges = ["solid", "dashed", "orthogonal", "both", "x"];
    match r.below(6) {
        0 => json!({}),
        4 => {
            json!({"id": a, "ids": [a, b], "kind": kinds[r.below(4) as usize], "w": r.below(500) as f64 - 10.0, "h": r.below(300) as f64, "props": {"ip": "10.0.0.1", "status": "down", "": "x"}, "edge": "left", "axis": "x", "mode": "diagram", "z": "front"})
        }
        5 => {
            json!({"id": a, "line": edges[r.below(5) as usize], "arrows": edges[r.below(5) as usize], "route": edges[r.below(5) as usize], "color": null, "width": r.below(40) as f64 - 5.0, "grid": r.below(60) as f64, "dx": 5, "dy": 5})
        }
        1 => json!({"id": a, "parent": b, "from": a, "to": b, "label": "x", "x": r.below(800) as f64 - 400.0, "y": r.below(800) as f64}),
        2 => json!({"ids": [a, b], "color": colors[r.below(4) as usize], "shape": shapes[r.below(5) as usize], "dx": 3, "dy": -2}),
        _ => json!({"id": a, "title": "t", "parent": null, "collapsed": r.below(2) == 0}),
    }
}

#[test]
fn random_command_sequences_keep_invariants() {
    let seeds: Vec<u64> = match std::env::var("IDEACRAFT_FUZZ_SEED").ok().and_then(|v| v.parse().ok()) {
        Some(one) => vec![one],
        None => (1..=env("IDEACRAFT_FUZZ_SEEDS", 24)).collect(),
    };
    let steps = env("IDEACRAFT_FUZZ_STEPS", 300);
    for seed in seeds {
        let mut r = Rng(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1);
        let mut s = Session::new(Map::sample());
        for step in 0..steps {
            let cmd = COMMANDS[r.below(COMMANDS.len() as u64) as usize].id;
            if cmd == "map.new" && r.below(20) != 0 {
                continue;
            }
            let params = random_params(&mut r, &s);
            let before = s.map().clone();
            let result = s.execute(cmd, &params);
            if let Err(e) = &result {
                assert_eq!(s.map(), &before, "seed {seed} step {step}: failed {cmd} ({e}) changed the map");
            }
            let after = s.map().clone();
            assert_eq!(Map::from_json(&after.to_json()).as_ref(), Ok(&after), "seed {seed} step {step}: {cmd} broke the round-trip");
            let undoable = command(cmd).is_some_and(|c| c.undoable);
            if result.is_ok() && undoable && after != before {
                s.execute("edit.undo", &json!({})).unwrap();
                assert_eq!(s.map(), &before, "seed {seed} step {step}: undo of {cmd}");
                s.execute("edit.redo", &json!({})).unwrap();
                assert_eq!(s.map(), &after, "seed {seed} step {step}: redo of {cmd}");
            }
        }
    }
}
