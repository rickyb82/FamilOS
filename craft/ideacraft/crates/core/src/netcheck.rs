//! Network diagram validation: reads the properties of network shapes (`ip`, `mask`, `gateway`,
//! `vlan`, `interfaces`) and the labels of their links (`VLAN 10`, `Gi0/1 ↔ eth0`) and reports
//! what does not add up: bad addresses, duplicates, network/broadcast addresses, hosts on one
//! VLAN with different subnets, gateways outside the subnet, unknown or reused interfaces,
//! invalid VLAN ids, devices without an address.
//!
//! Conventions (all optional; checks only run on what is given):
//! - `ip`: `192.168.1.10`, `192.168.1.10/24`, several separated by `,`; `dhcp` is skipped.
//! - `mask` (or `netmask`, `subnet`): `255.255.255.0` or `/24`, for addresses without a prefix.
//! - `gateway` (or `gw`): an address in the device's subnet.
//! - `vlan` (or `vlans`): `10` or `10,20` (1–4094); `trunk` means every VLAN.
//! - `interfaces` (or `ports`): entries separated by `;` or new lines, each `name=addr/nn vlan 10`,
//!   `name: addr/nn`, `name trunk 10,20` or just `name`.
//! - A link's label may name a VLAN (`VLAN 10`) and the interfaces at its ends (`Gi0/1 ↔ eth0`).

use std::collections::{BTreeMap, BTreeSet};
use std::net::{IpAddr, Ipv4Addr};

use crate::{EdgeId, Map, Node, NodeId};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Severity {
    Error,
    Warning,
    Info,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Code {
    BadAddress,
    BadMask,
    DuplicateAddress,
    NetworkAddress,
    BroadcastAddress,
    SubnetMismatch,
    GatewayOutside,
    BadVlan,
    VlanMismatch,
    UnknownInterface,
    InterfaceReused,
    DuplicateInterface,
    NoAddress,
    Isolated,
}

impl Code {
    pub const fn name(self) -> &'static str {
        match self {
            Self::BadAddress => "bad-address",
            Self::BadMask => "bad-mask",
            Self::DuplicateAddress => "duplicate-address",
            Self::NetworkAddress => "network-address",
            Self::BroadcastAddress => "broadcast-address",
            Self::SubnetMismatch => "subnet-mismatch",
            Self::GatewayOutside => "gateway-outside-subnet",
            Self::BadVlan => "bad-vlan",
            Self::VlanMismatch => "vlan-mismatch",
            Self::UnknownInterface => "unknown-interface",
            Self::InterfaceReused => "interface-reused",
            Self::DuplicateInterface => "duplicate-interface",
            Self::NoAddress => "no-address",
            Self::Isolated => "isolated",
        }
    }
}

/// One thing the checker noticed.
#[derive(Clone, Debug, PartialEq)]
pub struct Finding {
    pub code: Code,
    pub severity: Severity,
    pub nodes: Vec<NodeId>,
    pub edges: Vec<EdgeId>,
    /// The value at fault (an address, a VLAN, an interface name).
    pub detail: String,
    /// Names of the nodes involved, for messages.
    pub names: Vec<String>,
}

impl Finding {
    /// A sentence for people, in English or Italian.
    pub fn message(&self, italian: bool) -> String {
        let who = self.names.join(if italian { " e " } else { " and " });
        let d = &self.detail;
        if italian {
            match self.code {
                Code::BadAddress => format!("{who}: indirizzo IP non valido «{d}»"),
                Code::BadMask => format!("{who}: maschera non valida «{d}»"),
                Code::DuplicateAddress => format!("Indirizzo {d} usato da {who}"),
                Code::NetworkAddress => format!("{who}: {d} è l'indirizzo di rete della sua subnet"),
                Code::BroadcastAddress => format!("{who}: {d} è l'indirizzo di broadcast della sua subnet"),
                Code::SubnetMismatch => format!("{who} sono sulla stessa VLAN ma in subnet diverse ({d})"),
                Code::GatewayOutside => format!("{who}: il gateway {d} è fuori dalla subnet del dispositivo"),
                Code::BadVlan => format!("{who}: VLAN non valida «{d}» (1–4094)"),
                Code::VlanMismatch => format!("{who}: il collegamento porta la VLAN {d}, che il dispositivo non ha"),
                Code::UnknownInterface => format!("{who}: l'interfaccia «{d}» del collegamento non esiste sul dispositivo"),
                Code::InterfaceReused => format!("{who}: l'interfaccia {d} è usata da più collegamenti"),
                Code::DuplicateInterface => format!("{who}: interfaccia «{d}» dichiarata due volte"),
                Code::NoAddress => format!("{who}: nessun indirizzo IP"),
                Code::Isolated => format!("{who}: non è collegato a nulla"),
            }
        } else {
            match self.code {
                Code::BadAddress => format!("{who}: invalid IP address \"{d}\""),
                Code::BadMask => format!("{who}: invalid mask \"{d}\""),
                Code::DuplicateAddress => format!("Address {d} is used by {who}"),
                Code::NetworkAddress => format!("{who}: {d} is the network address of its subnet"),
                Code::BroadcastAddress => format!("{who}: {d} is the broadcast address of its subnet"),
                Code::SubnetMismatch => format!("{who} share a VLAN but sit in different subnets ({d})"),
                Code::GatewayOutside => format!("{who}: gateway {d} is outside the device's subnet"),
                Code::BadVlan => format!("{who}: invalid VLAN \"{d}\" (1–4094)"),
                Code::VlanMismatch => format!("{who}: the link carries VLAN {d}, which the device does not have"),
                Code::UnknownInterface => format!("{who}: the link's interface \"{d}\" does not exist on the device"),
                Code::InterfaceReused => format!("{who}: interface {d} is used by more than one link"),
                Code::DuplicateInterface => format!("{who}: interface \"{d}\" is declared twice"),
                Code::NoAddress => format!("{who}: no IP address"),
                Code::Isolated => format!("{who}: not connected to anything"),
            }
        }
    }
}

/// An address with an optional prefix length.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Addr {
    ip: IpAddr,
    prefix: Option<u8>,
}

impl Addr {
    fn network(&self) -> Option<(IpAddr, u8)> {
        match (self.ip, self.prefix) {
            (IpAddr::V4(v4), Some(p)) if p <= 32 => {
                let mask = if p == 0 { 0 } else { u32::MAX << (32 - p) };
                Some((IpAddr::V4(Ipv4Addr::from(u32::from(v4) & mask)), p))
            }
            (IpAddr::V6(v6), Some(p)) if p <= 128 => {
                let mask = if p == 0 { 0 } else { u128::MAX << (128 - p) };
                Some((IpAddr::V6(std::net::Ipv6Addr::from(u128::from(v6) & mask)), p))
            }
            _ => None,
        }
    }

    fn is_network_address(&self) -> bool {
        match (self.ip, self.prefix) {
            (IpAddr::V4(v4), Some(p)) if p < 31 => {
                let mask = if p == 0 { 0 } else { u32::MAX << (32 - p) };
                u32::from(v4) & !mask == 0
            }
            _ => false,
        }
    }

    fn is_broadcast_address(&self) -> bool {
        match (self.ip, self.prefix) {
            (IpAddr::V4(v4), Some(p)) if p < 31 => {
                let mask = if p == 0 { 0 } else { u32::MAX << (32 - p) };
                u32::from(v4) & !mask == !mask
            }
            _ => false,
        }
    }

    fn contains(&self, other: IpAddr) -> bool {
        match (self.network(), Addr { ip: other, prefix: self.prefix }.network()) {
            (Some(a), Some(b)) => a == b,
            _ => false,
        }
    }
}

fn skip_value(v: &str) -> bool {
    let v = v.trim().to_ascii_lowercase();
    v.is_empty() || v == "dhcp" || v == "-" || v == "auto" || v == "n/a" || v == "none" || v == "nessuno"
}

/// Parses `a.b.c.d`, `a.b.c.d/nn`, `a.b.c.d 255.255.255.0` or an IPv6 address.
fn parse_addr(text: &str) -> Result<Addr, String> {
    let t = text.trim();
    let (ip, rest) = match t.split_once('/') {
        Some((ip, p)) => (ip.trim(), Some(p.trim())),
        None => match t.split_once(char::is_whitespace) {
            Some((ip, m)) if m.trim().contains('.') => (ip.trim(), Some(m.trim())),
            _ => (t, None),
        },
    };
    let ip: IpAddr = ip.parse().map_err(|_| t.to_string())?;
    let prefix = match rest {
        None => None,
        Some(p) => Some(parse_mask(p).ok_or_else(|| t.to_string())?),
    };
    if prefix.is_some_and(|p| ip.is_ipv4() && p > 32) {
        return Err(t.to_string());
    }
    Ok(Addr { ip, prefix })
}

/// `24`, `/24` or `255.255.255.0` → 24.
fn parse_mask(text: &str) -> Option<u8> {
    let t = text.trim().trim_start_matches('/');
    if let Ok(n) = t.parse::<u8>() {
        return (n <= 128).then_some(n);
    }
    let m: Ipv4Addr = t.parse().ok()?;
    let bits = u32::from(m);
    let ones = bits.leading_ones();
    (bits == (u32::MAX << (32 - ones)) || bits == 0).then_some(ones as u8)
}

fn split_list(text: &str) -> impl Iterator<Item = &str> {
    text.split([',', ';', '\n']).map(str::trim).filter(|s| !s.is_empty())
}

/// An interface declared on a device.
#[derive(Clone, Debug, Default)]
struct Iface {
    name: String,
    addrs: Vec<Result<Addr, String>>,
    vlans: Vec<Result<u16, String>>,
    trunk: bool,
}

fn parse_vlan(token: &str) -> Result<u16, String> {
    let t = token.trim().trim_start_matches(|c: char| !c.is_ascii_digit());
    match t.parse::<u16>() {
        Ok(v) if (1..=4094).contains(&v) => Ok(v),
        _ => Err(token.trim().to_string()),
    }
}

/// `vlan 10,20` / `vlans 10 20` / `10,20` → the VLAN tokens.
fn parse_vlans(text: &str) -> (Vec<Result<u16, String>>, bool) {
    let lower = text.trim().to_ascii_lowercase();
    if lower == "trunk" || lower == "all" || lower == "tutte" {
        return (Vec::new(), true);
    }
    let body = lower.trim_start_matches("vlans").trim_start_matches("vlan").trim_start_matches(':').trim();
    let trunk = body.contains("trunk");
    let vlans = body.replace("trunk", " ").split([',', ' ', ';']).map(str::trim).filter(|s| !s.is_empty()).map(parse_vlan).collect();
    (vlans, trunk)
}

fn parse_interface(entry: &str) -> Iface {
    let (name, rest) = match entry.split_once(['=', ':']) {
        Some((n, r)) if !n.trim().is_empty() && !n.trim().chars().all(|c| c.is_ascii_digit()) => (n.trim(), r.trim()),
        _ => match entry.split_once(char::is_whitespace) {
            Some((n, r)) => (n.trim(), r.trim()),
            None => (entry.trim(), ""),
        },
    };
    let mut iface = Iface { name: name.to_string(), ..Iface::default() };
    let lower = rest.to_ascii_lowercase();
    // "addr/nn vlan 10,20" or "trunk 10,20": VLANs come after the word, addresses before.
    let (addr_part, vlan_part) = match lower.find("vlan").or_else(|| lower.find("trunk")) {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, ""),
    };
    for a in addr_part.split([',', ' ']).map(str::trim).filter(|s| !s.is_empty() && !skip_value(s)) {
        iface.addrs.push(parse_addr(a));
    }
    if !vlan_part.is_empty() {
        let (vlans, trunk) = parse_vlans(vlan_part);
        iface.vlans = vlans;
        iface.trunk = trunk;
    }
    iface
}

fn prop<'a>(n: &'a Node, keys: &[&str]) -> Option<&'a str> {
    keys.iter().find_map(|k| n.props.iter().find(|(pk, _)| pk.eq_ignore_ascii_case(k)).map(|(_, v)| v.as_str()))
}

/// Routes between subnets: a link through one of these never requires matching subnets.
fn is_layer3(n: &Node) -> bool {
    matches!(
        n.kind.as_deref(),
        Some("net.router" | "net.firewall" | "net.internet" | "net.wan" | "net.vpn" | "net.loadbalancer" | "net.modem" | "cloud.cloud")
    )
}

/// Forwards frames without an address of its own (a switch, an access point).
fn is_layer2(n: &Node) -> bool {
    matches!(n.kind.as_deref(), Some("net.switch" | "net.wifi" | "net.rack" | "net.ups"))
}

fn is_network_device(n: &Node) -> bool {
    n.kind.as_deref().is_some_and(|k| k.starts_with("net.") || k.starts_with("cloud."))
}

/// What a device declares, parsed once.
struct Device {
    id: NodeId,
    name: String,
    addrs: Vec<Addr>,
    vlans: Vec<u16>,
    trunk: bool,
    ifaces: Vec<Iface>,
}

/// The VLAN and interface names a link label mentions.
fn parse_label(label: &str) -> (Vec<u16>, Vec<String>) {
    let mut vlans = Vec::new();
    let mut ifaces = Vec::new();
    let cleaned = label.replace(['↔', '→', '←', '—', '–'], " ").replace(" - ", " ").replace(['(', ')'], " ");
    let tokens: Vec<&str> = cleaned.split_whitespace().collect();
    let mut i = 0;
    while i < tokens.len() {
        let t = tokens[i];
        let lower = t.to_ascii_lowercase();
        if lower.starts_with("vlan") {
            let rest = lower.trim_start_matches("vlans").trim_start_matches("vlan").trim_start_matches(':');
            let list = if rest.is_empty() { tokens.get(i + 1).copied().unwrap_or_default() } else { rest };
            if rest.is_empty() {
                i += 1;
            }
            for v in list.split([',', '/']) {
                if let Ok(v) = parse_vlan(v) {
                    vlans.push(v);
                }
            }
        } else if looks_like_interface(t) {
            ifaces.push(t.trim_matches(',').to_string());
        }
        i += 1;
    }
    (vlans, ifaces)
}

/// `eth0`, `Gi0/1`, `ge-0/0/1`, `wlan0`, `port 3`-style names: letters then a digit, slashes allowed.
fn looks_like_interface(t: &str) -> bool {
    let t = t.trim_matches(',');
    let mut chars = t.chars();
    let Some(first) = chars.next() else { return false };
    first.is_ascii_alphabetic()
        && t.chars().any(|c| c.is_ascii_digit())
        && t.chars().all(|c| c.is_ascii_alphanumeric() || c == '/' || c == '-' || c == '.' || c == '_')
        && t.len() <= 24
}

fn finding(code: Code, severity: Severity, nodes: &[&Device], detail: impl Into<String>) -> Finding {
    Finding {
        code,
        severity,
        nodes: nodes.iter().map(|d| d.id).collect(),
        edges: Vec::new(),
        detail: detail.into(),
        names: nodes.iter().map(|d| d.name.clone()).collect(),
    }
}

/// Checks the network diagram. Returns nothing for maps without network properties.
/// A device on a switch or VLAN group: the node and the VLAN it was seen on.
type Member = (NodeId, Option<u16>);
/// A network: its address and prefix length.
type Network = (IpAddr, u8);

pub fn validate(map: &Map) -> Vec<Finding> {
    let mut out = Vec::new();
    let mut devices: Vec<Device> = Vec::new();
    let mut by_addr: BTreeMap<IpAddr, Vec<NodeId>> = BTreeMap::new();
    for n in map.nodes() {
        let name = if n.label.trim().is_empty() { format!("#{}", n.id.0) } else { n.label.replace('\n', " ") };
        let mut dev = Device { id: n.id, name: name.clone(), addrs: Vec::new(), vlans: Vec::new(), trunk: false, ifaces: Vec::new() };
        let mask = prop(n, &["mask", "netmask", "subnet"]).filter(|m| !skip_value(m));
        let default_prefix = match mask {
            Some(m) => match parse_mask(m) {
                Some(p) => Some(p),
                None => {
                    out.push(finding(Code::BadMask, Severity::Error, std::slice::from_ref(&&dev), m));
                    None
                }
            },
            None => None,
        };
        if let Some(ips) = prop(n, &["ip", "ips", "address", "addresses", "ipv4", "ipv6"]) {
            for raw in split_list(ips).filter(|v| !skip_value(v)) {
                match parse_addr(raw) {
                    Ok(mut a) => {
                        if a.prefix.is_none() {
                            a.prefix = default_prefix;
                        }
                        dev.addrs.push(a);
                    }
                    Err(bad) => out.push(finding(Code::BadAddress, Severity::Error, std::slice::from_ref(&&dev), bad)),
                }
            }
        }
        if let Some(v) = prop(n, &["vlan", "vlans"]).filter(|v| !skip_value(v)) {
            let (vlans, trunk) = parse_vlans(v);
            dev.trunk = trunk;
            for v in vlans {
                match v {
                    Ok(v) => dev.vlans.push(v),
                    Err(bad) => out.push(finding(Code::BadVlan, Severity::Error, std::slice::from_ref(&&dev), bad)),
                }
            }
        }
        if let Some(text) = prop(n, &["interfaces", "ports", "interfacce", "porte"]).filter(|v| !skip_value(v)) {
            let mut seen = BTreeSet::new();
            for entry in text.split([';', '\n']).map(str::trim).filter(|s| !s.is_empty()) {
                let iface = parse_interface(entry);
                if !seen.insert(iface.name.to_ascii_lowercase()) {
                    out.push(finding(Code::DuplicateInterface, Severity::Warning, std::slice::from_ref(&&dev), iface.name.clone()));
                }
                for a in &iface.addrs {
                    match a {
                        Ok(a) => {
                            let mut a = *a;
                            if a.prefix.is_none() {
                                a.prefix = default_prefix;
                            }
                            dev.addrs.push(a);
                        }
                        Err(bad) => out.push(finding(Code::BadAddress, Severity::Error, std::slice::from_ref(&&dev), bad.clone())),
                    }
                }
                for v in &iface.vlans {
                    if let Err(bad) = v {
                        out.push(finding(Code::BadVlan, Severity::Error, std::slice::from_ref(&&dev), bad.clone()));
                    }
                }
                dev.ifaces.push(iface);
            }
        }
        for a in &dev.addrs {
            if a.is_network_address() {
                out.push(finding(Code::NetworkAddress, Severity::Warning, std::slice::from_ref(&&dev), addr_text(a)));
            } else if a.is_broadcast_address() {
                out.push(finding(Code::BroadcastAddress, Severity::Warning, std::slice::from_ref(&&dev), addr_text(a)));
            }
            by_addr.entry(a.ip).or_default().push(n.id);
        }
        if let Some(gw) = prop(n, &["gateway", "gw", "default gateway"]).filter(|v| !skip_value(v)) {
            match parse_addr(gw) {
                Ok(g) => {
                    let subnets: Vec<&Addr> = dev.addrs.iter().filter(|a| a.prefix.is_some() && a.ip.is_ipv4() == g.ip.is_ipv4()).collect();
                    if !subnets.is_empty() && !subnets.iter().any(|a| a.contains(g.ip)) {
                        out.push(finding(Code::GatewayOutside, Severity::Warning, std::slice::from_ref(&&dev), gw.trim()));
                    }
                }
                Err(bad) => out.push(finding(Code::BadAddress, Severity::Error, std::slice::from_ref(&&dev), bad)),
            }
        }
        devices.push(dev);
    }
    let any_ip = devices.iter().any(|d| !d.addrs.is_empty());
    let dev = |id: NodeId| devices.iter().find(|d| d.id == id);
    // Duplicates.
    for (ip, ids) in &by_addr {
        let uniq: BTreeSet<NodeId> = ids.iter().copied().collect();
        if uniq.len() > 1 {
            let ds: Vec<&Device> = uniq.iter().filter_map(|id| dev(*id)).collect();
            out.push(finding(Code::DuplicateAddress, Severity::Error, &ds, ip.to_string()));
        }
    }
    // Links: interfaces, VLANs, subnets.
    let mut iface_use: BTreeMap<(NodeId, String), Vec<EdgeId>> = BTreeMap::new();
    let mut degree: BTreeMap<NodeId, usize> = BTreeMap::new();
    for e in map.edges() {
        *degree.entry(e.from).or_default() += 1;
        *degree.entry(e.to).or_default() += 1;
        let (Some(a), Some(b)) = (dev(e.from), dev(e.to)) else { continue };
        let (vlans, ifaces) = parse_label(&e.label);
        for name in &ifaces {
            let owner = [a, b].into_iter().find(|d| d.ifaces.iter().any(|i| i.name.eq_ignore_ascii_case(name)));
            match owner {
                Some(d) => iface_use.entry((d.id, name.to_ascii_lowercase())).or_default().push(e.id),
                None => {
                    // Only a problem when the devices declare their interfaces at all.
                    let declared: Vec<&Device> = [a, b].into_iter().filter(|d| !d.ifaces.is_empty()).collect();
                    if !declared.is_empty() {
                        let mut f = finding(Code::UnknownInterface, Severity::Warning, &declared, name.clone());
                        f.edges.push(e.id);
                        out.push(f);
                    }
                }
            }
        }
        for v in &vlans {
            for d in [a, b] {
                if map.node(d.id).is_none_or(|n| is_layer2(n) || is_layer3(n)) {
                    continue;
                }
                let has = d.trunk
                    || d.vlans.contains(v)
                    || d.ifaces.iter().any(|i| i.trunk || i.vlans.iter().any(|x| x.as_ref() == Ok(v)))
                    || (d.vlans.is_empty() && d.ifaces.iter().all(|i| i.vlans.is_empty()));
                if !has {
                    let mut f = finding(Code::VlanMismatch, Severity::Warning, &[d], v.to_string());
                    f.edges.push(e.id);
                    out.push(f);
                }
            }
        }
    }
    for ((id, name), edges) in iface_use {
        if edges.len() > 1
            && let Some(d) = dev(id)
            && !d.ifaces.iter().any(|i| i.name.eq_ignore_ascii_case(&name) && i.trunk)
        {
            let shown = d.ifaces.iter().find(|i| i.name.eq_ignore_ascii_case(&name)).map_or(name.clone(), |i| i.name.clone());
            let mut f = finding(Code::InterfaceReused, Severity::Warning, &[d], shown);
            f.edges = edges;
            out.push(f);
        }
    }
    // Hosts on the same switch (or directly linked) within one VLAN must share a subnet.
    let node_of = |id: NodeId| map.node(id);
    let host = |d: &Device| node_of(d.id).is_some_and(|n| !is_layer2(n) && !is_layer3(n));
    let primary = |d: &Device| d.addrs.iter().find(|a| a.ip.is_ipv4() && a.prefix.is_some()).copied();
    let mut groups: Vec<(String, Vec<Member>)> = Vec::new();
    for e in map.edges() {
        let (Some(a), Some(b)) = (dev(e.from), dev(e.to)) else { continue };
        let (vlans, _) = parse_label(&e.label);
        let vlan = vlans.first().copied();
        match (host(a), host(b)) {
            (true, true) => groups.push((format!("link {}", e.id.0), vec![(a.id, vlan), (b.id, vlan)])),
            (true, false) if node_of(b.id).is_some_and(is_layer2) => groups.push((b.name.clone(), vec![(a.id, vlan)])),
            (false, true) if node_of(a.id).is_some_and(is_layer2) => groups.push((a.name.clone(), vec![(b.id, vlan)])),
            _ => {}
        }
    }
    // Merge per switch.
    let mut by_switch: BTreeMap<String, Vec<(NodeId, Option<u16>)>> = BTreeMap::new();
    for (k, members) in groups {
        by_switch.entry(k).or_default().extend(members);
    }
    let mut reported: BTreeSet<(NodeId, NodeId)> = BTreeSet::new();
    for (_, members) in by_switch {
        let mut per_vlan: BTreeMap<Option<u16>, Vec<(NodeId, Network)>> = BTreeMap::new();
        for (id, vlan) in members {
            let Some(d) = dev(id) else { continue };
            let vlan = vlan.or_else(|| d.vlans.first().copied());
            if let Some(net) = primary(d).and_then(|a| a.network()) {
                per_vlan.entry(vlan).or_default().push((id, net));
            }
        }
        for (_, hosts) in per_vlan {
            for i in 0..hosts.len() {
                for j in i + 1..hosts.len() {
                    let ((x, nx), (y, ny)) = (hosts[i], hosts[j]);
                    if nx != ny && reported.insert((x.min(y), x.max(y))) {
                        let ds: Vec<&Device> = [x, y].into_iter().filter_map(dev).collect();
                        out.push(finding(Code::SubnetMismatch, Severity::Warning, &ds, format!("{}/{} vs {}/{}", nx.0, nx.1, ny.0, ny.1)));
                    }
                }
            }
        }
    }
    // Devices without an address (only when the diagram uses addresses) and isolated devices.
    for d in &devices {
        let Some(n) = node_of(d.id) else { continue };
        if !is_network_device(n) {
            continue;
        }
        if any_ip
            && d.addrs.is_empty()
            && !is_layer2(n)
            && !matches!(n.kind.as_deref(), Some("net.internet" | "net.wan" | "net.vpn" | "cloud.cloud" | "net.rack" | "net.ups"))
            && prop(n, &["ip"]).is_none_or(|v| !skip_value(v))
        {
            out.push(finding(Code::NoAddress, Severity::Info, &[d], ""));
        }
        if map.edge_count() > 0
            && degree.get(&d.id).copied().unwrap_or(0) == 0
            && n.parent.is_none()
            && !matches!(n.kind.as_deref(), Some("net.rack" | "net.ups"))
        {
            out.push(finding(Code::Isolated, Severity::Info, &[d], ""));
        }
    }
    out.sort_by_key(|f| (f.severity, f.code.name(), f.nodes.first().map(|n| n.0).unwrap_or(0)));
    out
}

fn addr_text(a: &Addr) -> String {
    match a.prefix {
        Some(p) => format!("{}/{p}", a.ip),
        None => a.ip.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Point, stencil};

    fn dev(m: &mut Map, kind: &str, label: &str, x: f32, props: &[(&str, &str)]) -> NodeId {
        let id = m.add_stencil(stencil::find(kind).unwrap(), Some(label), Point::new(x, 0.0)).unwrap();
        for (k, v) in props {
            m.set_prop(id, k, v).unwrap();
        }
        id
    }

    fn codes(f: &[Finding]) -> Vec<Code> {
        f.iter().map(|f| f.code).collect()
    }

    #[test]
    fn parses_addresses_masks_vlans_interfaces_and_labels() {
        assert_eq!(parse_addr("10.0.0.1/24").unwrap().prefix, Some(24));
        assert_eq!(parse_addr("10.0.0.1 255.255.255.0").unwrap().prefix, Some(24));
        assert!(parse_addr("10.0.0.300").is_err() && parse_addr("10.0.0.1/33").is_err());
        assert_eq!(parse_mask("255.255.254.0"), Some(23));
        assert_eq!(parse_mask("255.0.255.0"), None);
        assert_eq!(parse_addr("fe80::1/64").unwrap().prefix, Some(64));
        assert!(parse_addr("192.168.1.0/24").unwrap().is_network_address());
        assert!(parse_addr("192.168.1.255/24").unwrap().is_broadcast_address());
        assert!(!parse_addr("192.168.1.1/31").unwrap().is_network_address());
        let i = parse_interface("Gi0/1=192.168.10.1/24 vlan 10,20");
        assert_eq!((i.name.as_str(), i.addrs.len(), i.vlans.len()), ("Gi0/1", 1, 2));
        let t = parse_interface("Gi0/24 trunk 10,20,30");
        assert!(t.trunk && t.vlans.len() == 3);
        assert_eq!(parse_interface("eth0").addrs.len(), 0);
        assert_eq!(parse_label("Gi0/1 ↔ eth0 (VLAN 20)"), (vec![20], vec!["Gi0/1".into(), "eth0".into()]));
        assert_eq!(parse_label("VLAN 10,20 trunk"), (vec![10, 20], vec![]));
        assert_eq!(parse_label("1 Gbit"), (vec![], vec![]));
        assert_eq!(parse_vlans("vlan 5000").0[0], Err("5000".into()));
    }

    #[test]
    fn finds_the_classic_mistakes() {
        let mut m = Map::new("net");
        let fw = dev(&mut m, "net.firewall", "FW", 0.0, &[("ip", "192.168.1.1/24"), ("interfaces", "eth0=192.168.1.1/24; eth1=10.0.0.1/24")]);
        let sw = dev(&mut m, "net.switch", "SW", 200.0, &[("interfaces", "Gi0/1 trunk 10,20; Gi0/2 vlan 10; Gi0/3 vlan 20; Gi0/3 vlan 20")]);
        let pc1 = dev(&mut m, "net.pc", "PC1", 400.0, &[("ip", "192.168.10.10/24"), ("vlan", "10"), ("gateway", "192.168.10.1")]);
        let pc2 =
            dev(&mut m, "net.pc", "PC2", 500.0, &[("ip", "192.168.20.10"), ("mask", "255.255.255.0"), ("vlan", "10"), ("gateway", "192.168.99.1")]);
        let pc3 = dev(&mut m, "net.pc", "PC3", 600.0, &[("ip", "192.168.10.10/24"), ("vlan", "30")]);
        let srv = dev(&mut m, "net.server", "SRV", 700.0, &[("ip", "192.168.10.0/24"), ("vlan", "4095")]);
        let printer = dev(&mut m, "net.printer", "PRN", 800.0, &[("ip", "not-an-ip")]);
        let lonely = dev(&mut m, "net.storage", "NAS", 900.0, &[]);
        let _ = lonely;
        m.connect(fw, sw, "eth1 ↔ Gi0/1 VLAN 10,20").unwrap();
        m.connect(sw, pc1, "Gi0/2 VLAN 10").unwrap();
        m.connect(sw, pc2, "Gi0/3 VLAN 10").unwrap();
        m.connect(sw, pc3, "Gi0/9 VLAN 10").unwrap();
        m.connect(sw, srv, "Gi0/2").unwrap();
        m.connect(sw, printer, "").unwrap();
        let f = validate(&m);
        let c = codes(&f);
        assert!(c.contains(&Code::BadAddress), "PRN");
        assert!(c.contains(&Code::DuplicateAddress), "PC1/PC3");
        assert!(c.contains(&Code::NetworkAddress), "SRV .0");
        assert!(c.contains(&Code::BadVlan), "SRV 4095");
        assert!(c.contains(&Code::GatewayOutside), "PC2 gateway");
        assert!(c.contains(&Code::SubnetMismatch), "PC1 and PC2 on VLAN 10");
        assert!(c.contains(&Code::VlanMismatch), "PC3 is on VLAN 30 but linked as 10");
        assert!(c.contains(&Code::UnknownInterface), "Gi0/9 does not exist on SW");
        assert!(c.contains(&Code::InterfaceReused), "Gi0/2 used twice");
        assert!(c.contains(&Code::DuplicateInterface), "Gi0/3 declared twice");
        assert!(c.contains(&Code::Isolated), "NAS");
        assert!(c.contains(&Code::NoAddress), "NAS has no address");
        assert!(!f.iter().any(|x| x.code == Code::GatewayOutside && x.nodes == vec![pc1]), "PC1's gateway is fine");
        for x in &f {
            assert!(!x.message(false).is_empty() && !x.message(true).is_empty());
        }
        let first = f.first().unwrap();
        assert_eq!(first.severity, Severity::Error, "errors first");
        // A clean diagram reports nothing; a non-network map too.
        let mut clean = Map::new("ok");
        let r = dev(&mut clean, "net.router", "R", 0.0, &[("ip", "10.0.0.1/24")]);
        let p = dev(&mut clean, "net.pc", "P", 300.0, &[("ip", "10.0.0.2/24"), ("gateway", "10.0.0.1")]);
        clean.connect(r, p, "").unwrap();
        assert!(validate(&clean).is_empty(), "{:?}", validate(&clean));
        assert!(validate(&Map::sample()).is_empty());
    }
}
