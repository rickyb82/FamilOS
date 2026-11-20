//! Diagram templates, built in code with stencils, positions and styled connectors.

use ideacraft_core::{Arrows, EdgeStyle, LineStyle, Map, Mode, NodeId, Point, Route, stencil};

use crate::templates::Lang;

struct D {
    map: Map,
    it: bool,
}

impl D {
    fn new(title_en: &str, title_it: &str, lang: Lang) -> Self {
        let it = lang == Lang::It;
        let mut map = Map::new(if it { title_it } else { title_en });
        map.set_mode(Mode::Diagram);
        Self { map, it }
    }

    fn t<'a>(&self, en: &'a str, it: &'a str) -> &'a str {
        if self.it { it } else { en }
    }

    /// A stencil at (x, y) with a label and properties; a failure (impossible on a fresh map)
    /// yields a placeholder id so the template stays well-formed.
    fn shape(&mut self, kind: &str, en: &str, it: &str, x: f32, y: f32, props: &[(&str, &str)]) -> NodeId {
        let label = self.t(en, it).to_string();
        let st = stencil::find(kind).unwrap_or(&stencil::ALL[0]);
        let id = self.map.add_stencil(st, Some(&label), Point::new(x, y)).unwrap_or(NodeId(0));
        for (k, v) in props {
            let _ = self.map.set_prop(id, k, v);
        }
        id
    }

    fn container(&mut self, en: &str, it: &str, x: f32, y: f32, w: f32, h: f32) -> NodeId {
        let id = self.shape("basic.container", en, it, x, y, &[]);
        let _ = self.map.set_size(id, Some(ideacraft_core::Size::new(w, h)));
        id
    }

    /// Puts shapes on a layer (created on first use).
    fn layer(&mut self, en: &str, it: &str, ids: &[NodeId]) {
        let name = self.t(en, it).to_string();
        for id in ids {
            let _ = self.map.set_node_layer(*id, Some(&name));
        }
    }

    fn link(&mut self, a: NodeId, b: NodeId, en: &str, it: &str, style: EdgeStyle) {
        let label = self.t(en, it).to_string();
        if let Ok(e) = self.map.connect(a, b, &label) {
            let _ = self.map.set_edge_style(e, style);
        }
    }
}

const WIRE: EdgeStyle = EdgeStyle {
    line: LineStyle::Solid,
    arrows: Arrows::None,
    route: Route::Orthogonal,
    color: Some(ideacraft_core::Rgb(0x6b, 0x70, 0x80)),
    width: 2.0,
};
const WIFI: EdgeStyle = EdgeStyle {
    line: LineStyle::Dotted,
    arrows: Arrows::None,
    route: Route::Straight,
    color: Some(ideacraft_core::Rgb(0x22, 0xa0, 0x6b)),
    width: 2.0,
};
const FLOW: EdgeStyle = EdgeStyle {
    line: LineStyle::Solid,
    arrows: Arrows::End,
    route: Route::Orthogonal,
    color: Some(ideacraft_core::Rgb(0x4a, 0x4f, 0x5e)),
    width: 1.8,
};
const DATA: EdgeStyle = EdgeStyle {
    line: LineStyle::Dashed,
    arrows: Arrows::End,
    route: Route::Orthogonal,
    color: Some(ideacraft_core::Rgb(0x5b, 0x5b, 0xf0)),
    width: 1.6,
};
const REPORTS: EdgeStyle = EdgeStyle {
    line: LineStyle::Solid,
    arrows: Arrows::None,
    route: Route::Orthogonal,
    color: Some(ideacraft_core::Rgb(0x8a, 0x90, 0xa3)),
    width: 1.6,
};

/// An office network: internet, firewall, core switch, servers, Wi-Fi and clients.
pub fn office_network(lang: Lang) -> Map {
    let mut d = D::new("Office network", "Rete aziendale", lang);
    let dc = d.container("Data centre", "Sala server", 600.0, 420.0, 460.0, 300.0);
    let floor = d.container("Office floor", "Uffici", 290.0, 680.0, 600.0, 300.0);
    let internet = d.shape("net.internet", "Internet", "Internet", 60.0, 60.0, &[("provider", "ISP"), ("status", "up")]);
    let fw = d.shape(
        "net.firewall",
        "Firewall",
        "Firewall",
        300.0,
        60.0,
        &[
            ("ip", "192.168.1.1/24"),
            ("interfaces", "wan=203.0.113.2/30; lan=192.168.1.1/24; vlan10=192.168.10.1/24; vlan20=192.168.20.1/24; vlan30=192.168.30.1/24"),
            ("model", "FW-200"),
            ("status", "up"),
        ],
    );
    let vpn = d.shape("net.vpn", "VPN users", "Utenti VPN", 540.0, 60.0, &[("tunnel", "IPsec")]);
    let core = d.shape(
        "net.switch",
        "Core switch",
        "Switch core",
        300.0,
        360.0,
        &[
            ("ip", "192.168.1.2/24"),
            ("gateway", "192.168.1.1"),
            ("vlans", "1,10,20,30"),
            ("interfaces", "Gi0/1 trunk 1,10,20,30; Gi0/2 vlan 10; Gi0/24 trunk 20,30"),
            ("ports", "48"),
            ("status", "up"),
        ],
    );
    let srv1 = d.shape(
        "net.server",
        "File server",
        "File server",
        480.0,
        360.0,
        &[("ip", "192.168.10.10/24"), ("gateway", "192.168.10.1"), ("vlan", "10"), ("os", "Linux"), ("status", "up")],
    );
    let srv2 = d.shape(
        "cloud.vm",
        "App server",
        "Server applicativo",
        720.0,
        360.0,
        &[("ip", "192.168.10.11/24"), ("gateway", "192.168.10.1"), ("vlan", "10"), ("status", "warning")],
    );
    let nas = d.shape(
        "net.storage",
        "NAS",
        "NAS",
        600.0,
        500.0,
        &[("ip", "192.168.10.20/24"), ("gateway", "192.168.10.1"), ("vlan", "10"), ("capacity", "24 TB"), ("status", "up")],
    );
    let office = d.shape(
        "net.switch",
        "Office switch",
        "Switch uffici",
        220.0,
        600.0,
        &[
            ("ip", "192.168.1.3/24"),
            ("gateway", "192.168.1.1"),
            ("vlans", "20,30"),
            ("interfaces", "Gi0/1 trunk 20,30; Gi0/2 vlan 30"),
            ("ports", "24"),
            ("status", "up"),
        ],
    );
    let ap = d.shape("net.wifi", "Access point", "Access point", 60.0, 600.0, &[("ssid", "Office"), ("vlan", "30"), ("status", "down")]);
    let printer =
        d.shape("net.printer", "Printer", "Stampante", 380.0, 600.0, &[("ip", "192.168.20.50/24"), ("gateway", "192.168.20.1"), ("vlan", "20")]);
    let pc1 = d.shape(
        "net.pc",
        "Workstation 1",
        "Postazione 1",
        60.0,
        760.0,
        &[("ip", "192.168.20.21/24"), ("gateway", "192.168.20.1"), ("vlan", "20"), ("user", "Anna")],
    );
    let pc2 = d.shape(
        "net.pc",
        "Workstation 2",
        "Postazione 2",
        220.0,
        760.0,
        &[("ip", "192.168.20.22/24"), ("gateway", "192.168.20.1"), ("vlan", "20"), ("user", "Luca")],
    );
    let laptop = d.shape("net.laptop", "Laptop", "Portatile", 380.0, 760.0, &[("ip", "DHCP"), ("vlan", "30")]);
    let phone = d.shape("net.phone", "Phone", "Telefono", 520.0, 760.0, &[("ip", "DHCP"), ("vlan", "30")]);
    d.layer("Perimeter", "Perimetro", &[internet, fw, vpn, core]);
    d.layer("Data centre", "Sala server", &[dc, srv1, srv2, nas]);
    d.layer("Office floor", "Uffici", &[floor, office, ap, printer, pc1, pc2, laptop, phone]);
    d.link(internet, fw, "WAN", "WAN", WIRE);
    d.link(vpn, fw, "VPN", "VPN", EdgeStyle { line: LineStyle::Dashed, ..WIRE });
    d.link(fw, core, "LAN 10 Gbit", "LAN 10 Gbit", WIRE);
    d.link(core, srv1, "VLAN 10", "VLAN 10", WIRE);
    d.link(srv1, srv2, "", "", WIRE);
    d.link(srv1, nas, "iSCSI", "iSCSI", WIRE);
    d.link(srv2, nas, "", "", WIRE);
    d.link(core, office, "VLAN 20/30", "VLAN 20/30", WIRE);
    d.link(office, ap, "VLAN 30", "VLAN 30", WIRE);
    d.link(office, printer, "", "", WIRE);
    d.link(office, pc1, "VLAN 20", "VLAN 20", WIRE);
    d.link(office, pc2, "", "", WIRE);
    d.link(ap, laptop, "Wi-Fi", "Wi-Fi", WIFI);
    d.link(ap, phone, "Wi-Fi", "Wi-Fi", WIFI);
    d.map
}

/// A classic flowchart: start, steps, a decision loop, output and end.
pub fn flowchart(lang: Lang) -> Map {
    let mut d = D::new("Flowchart", "Diagramma di flusso", lang);
    let start = d.shape("flow.start", "Start", "Inizio", 0.0, 0.0, &[]);
    let input = d.shape("flow.data", "Read request", "Leggi la richiesta", 0.0, 110.0, &[]);
    let check = d.shape("flow.decision", "Valid?", "Valida?", 0.0, 240.0, &[]);
    let fix = d.shape("flow.process", "Ask for corrections", "Chiedi correzioni", 260.0, 240.0, &[]);
    let process = d.shape("flow.process", "Process request", "Elabora la richiesta", 0.0, 380.0, &[]);
    let store = d.shape("flow.database", "Save to archive", "Salva in archivio", 260.0, 380.0, &[]);
    let doc = d.shape("flow.document", "Send confirmation", "Invia conferma", 0.0, 500.0, &[]);
    let end = d.shape("flow.start", "End", "Fine", 0.0, 610.0, &[]);
    let note = d.shape("flow.note", "Decisions have a Yes and a No branch", "Ogni decisione ha un ramo Sì e uno No", 300.0, 60.0, &[]);
    d.link(start, input, "", "", FLOW);
    d.link(input, check, "", "", FLOW);
    d.link(check, process, "Yes", "Sì", FLOW);
    d.link(check, fix, "No", "No", FLOW);
    d.link(fix, input, "", "", EdgeStyle { route: Route::Orthogonal, ..FLOW });
    d.link(process, store, "", "", DATA);
    d.link(process, doc, "", "", FLOW);
    d.link(doc, end, "", "", FLOW);
    let _ = note;
    d.map
}

/// A web application in the cloud: users, CDN, load balancer, services, queue, databases.
pub fn cloud_architecture(lang: Lang) -> Map {
    let mut d = D::new("Cloud architecture", "Architettura cloud", lang);
    d.container("Cloud region", "Regione cloud", 520.0, 260.0, 760.0, 440.0);
    d.container("Private subnet", "Subnet privata", 640.0, 330.0, 480.0, 260.0);
    let users = d.shape("people.team", "Users", "Utenti", 40.0, 120.0, &[]);
    let cdn = d.shape("cloud.cloud", "CDN", "CDN", 240.0, 120.0, &[("cache", "edge")]);
    let lb = d.shape("net.loadbalancer", "Load balancer", "Load balancer", 440.0, 120.0, &[("tls", "1.3")]);
    let api1 = d.shape("cloud.container", "API (1)", "API (1)", 520.0, 300.0, &[("replicas", "3"), ("status", "up")]);
    let api2 = d.shape("cloud.container", "API (2)", "API (2)", 680.0, 300.0, &[("status", "up")]);
    let worker = d.shape("cloud.function", "Worker", "Worker", 840.0, 300.0, &[("runtime", "serverless")]);
    let queue = d.shape("cloud.queue", "Queue", "Coda", 680.0, 420.0, &[("kind", "FIFO")]);
    let db = d.shape("cloud.database", "Database", "Database", 520.0, 420.0, &[("engine", "PostgreSQL"), ("status", "up")]);
    let storage = d.shape("cloud.storage", "Object storage", "Storage a oggetti", 840.0, 420.0, &[]);
    d.link(users, cdn, "HTTPS", "HTTPS", FLOW);
    d.link(cdn, lb, "", "", FLOW);
    d.link(lb, api1, "", "", FLOW);
    d.link(lb, api2, "", "", FLOW);
    d.link(api1, db, "SQL", "SQL", DATA);
    d.link(api2, db, "", "", DATA);
    d.link(api2, queue, "jobs", "job", DATA);
    d.link(queue, worker, "", "", DATA);
    d.link(worker, storage, "files", "file", DATA);
    d.link(cdn, storage, "static", "statici", EdgeStyle { line: LineStyle::Dotted, ..DATA });
    d.map
}

/// An organisation chart: boxes arranged top-down with orthogonal reporting lines.
pub fn org_chart(lang: Lang) -> Map {
    let mut d = D::new("Organisation chart", "Organigramma", lang);
    let ceo = d.shape("flow.process", "CEO", "Amministratore delegato", 300.0, 0.0, &[("name", "—")]);
    let cto = d.shape("flow.process", "CTO", "Direzione tecnica", 100.0, 130.0, &[]);
    let coo = d.shape("flow.process", "COO", "Direzione operativa", 300.0, 130.0, &[]);
    let cfo = d.shape("flow.process", "CFO", "Direzione finanziaria", 500.0, 130.0, &[]);
    let dev = d.shape("people.team", "Engineering", "Sviluppo", 20.0, 280.0, &[("people", "12")]);
    let ops = d.shape("people.team", "Operations", "Operazioni", 180.0, 280.0, &[("people", "6")]);
    let sales = d.shape("people.team", "Sales", "Vendite", 340.0, 280.0, &[("people", "8")]);
    let admin = d.shape("people.team", "Administration", "Amministrazione", 500.0, 280.0, &[("people", "4")]);
    for (a, b) in [(ceo, cto), (ceo, coo), (ceo, cfo), (cto, dev), (cto, ops), (coo, sales), (cfo, admin)] {
        d.link(a, b, "", "", REPORTS);
    }
    d.map
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn diagram_templates_are_well_formed_in_both_languages() {
        for build in [office_network, flowchart, cloud_architecture, org_chart] {
            for lang in [Lang::En, Lang::It] {
                let m = build(lang);
                assert_eq!(m.mode(), Mode::Diagram);
                assert!(m.node_count() >= 8 && m.edge_count() >= 6, "{}", m.title());
                assert!(m.nodes().all(|n| n.size.is_some()), "every shape has a size");
                assert!(m.nodes().all(|n| n.id != NodeId(0)));
                assert_eq!(Map::from_json(&m.to_json()).as_ref(), Ok(&m));
            }
            assert_ne!(build(Lang::En).title(), build(Lang::It).title());
        }
        let net = office_network(Lang::En);
        assert!(net.nodes().any(|n| n.status_light() == Some(ideacraft_core::Status::Down)));
        assert!(net.nodes().any(|n| n.shape.is_container()));
    }
}
