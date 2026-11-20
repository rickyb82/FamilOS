//! Built-in templates: original content and palettes, defined here in code.
//!
//! Each template is an indented outline (two spaces per level), a palette for its first-level
//! branches, a layout and optional labelled links between ideas (matched by label).

use ideacraft_core::layout::{self, Layout};
use ideacraft_core::{Map, Point, Rgb, Shape};

use crate::build::{self, Link, Topic};
use crate::markdown::{indent, rank, tree};

/// Template languages.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Lang {
    #[default]
    En,
    It,
}

impl Lang {
    pub fn parse(code: &str) -> Option<Self> {
        match code.trim().split(['-', '_']).next()?.to_ascii_lowercase().as_str() {
            "en" => Some(Self::En),
            "it" => Some(Self::It),
            _ => None,
        }
    }
}

/// A template's text in one language.
pub struct Text {
    pub name: &'static str,
    pub description: &'static str,
    /// For galleries: a short category.
    pub category: &'static str,
    outline: &'static str,
    links: &'static [(&'static str, &'static str, &'static str)],
}

pub struct Template {
    pub id: &'static str,
    pub name: &'static str,
    pub description: &'static str,
    /// For galleries: a short category.
    pub category: &'static str,
    outline: &'static str,
    links: &'static [(&'static str, &'static str, &'static str)],
    /// Italian text (links name ideas by their Italian labels).
    it: Text,
    palette: &'static [Rgb],
    root: Rgb,
    root_shape: Shape,
    leaf_shape: Shape,
    layout: Layout,
    /// Built in code instead of from the outline (diagram templates).
    diagram: Option<fn(Lang) -> Map>,
}

const fn rgb(hex: u32) -> Rgb {
    Rgb((hex >> 16) as u8, (hex >> 8) as u8, hex as u8)
}

const PASTEL: &[Rgb] = &[rgb(0xa9cdff), rgb(0xa8ead2), rgb(0xd0c2fc), rgb(0xffc0a6), rgb(0xf7b5cf), rgb(0xd6f0a0), rgb(0xffde85)];
const OCEAN: &[Rgb] = &[rgb(0x9fd8f0), rgb(0xa9e4dc), rgb(0xb9cdf5), rgb(0xc8eef7), rgb(0x9cc9e8)];
const SUNSET: &[Rgb] = &[rgb(0xffcf9e), rgb(0xffb3a3), rgb(0xf6a9c3), rgb(0xe2b4ef), rgb(0xffe39b)];
const FOREST: &[Rgb] = &[rgb(0xb8e3b0), rgb(0xd8ecaa), rgb(0xa9d8c3), rgb(0xe6dcae), rgb(0xc4e7cf)];
const SWOT: &[Rgb] = &[rgb(0xb3e8b8), rgb(0xffbfb3), rgb(0xa9cdff), rgb(0xffd99a)];

pub const ALL: &[Template] = &[
    Template {
        id: "brainstorm",
        name: "Brainstorming",
        description: "A central question with idea clusters around it, laid out in a circle.",
        category: "Ideas",
        outline: "What could we try next?
  Quick wins
    Small experiment
    Ask our users
  Big bets
    New audience
    Partnership
  Wild ideas
    Do the opposite
    Combine two products
  Questions
    What would make this easy?
    What is stopping us?
  Inspiration
    Other industries
    Nature",
        links: &[("Ask our users", "New audience", "could reveal")],
        it: Text {
            name: "Brainstorming",
            description: "Una domanda centrale con gruppi di idee intorno, disposti in cerchio.",
            category: "Idee",
            outline: "Cosa potremmo provare adesso?
  Risultati rapidi
    Piccolo esperimento
    Chiedere agli utenti
  Grandi scommesse
    Nuovo pubblico
    Partnership
  Idee folli
    Fare il contrario
    Unire due prodotti
  Domande
    Cosa lo renderebbe facile?
    Cosa ci blocca?
  Ispirazione
    Altri settori
    Natura",
            links: &[("Chiedere agli utenti", "Nuovo pubblico", "potrebbe rivelare")],
        },
        palette: PASTEL,
        root: rgb(0xffde85),
        root_shape: Shape::Ellipse,
        leaf_shape: Shape::Pill,
        layout: Layout::Radial,
        diagram: None,
    },
    Template {
        id: "concept-map",
        name: "Concept map",
        description: "A study concept map: concepts joined by labelled links (the water cycle as an example).",
        category: "Learning",
        outline: "Water cycle
  Evaporation
    Sun heats water
  Condensation
    Clouds form
  Precipitation
    Rain
    Snow
  Collection
    Rivers
    Oceans
    Groundwater",
        links: &[
            ("Evaporation", "Condensation", "leads to"),
            ("Condensation", "Precipitation", "leads to"),
            ("Precipitation", "Collection", "feeds"),
            ("Collection", "Evaporation", "starts again with"),
        ],
        it: Text {
            name: "Mappa concettuale",
            description: "Una mappa concettuale per studiare: concetti uniti da collegamenti etichettati (con il ciclo dell’acqua come esempio).",
            category: "Studio",
            outline: "Ciclo dell’acqua
  Evaporazione
    Il sole scalda l’acqua
  Condensazione
    Si formano le nuvole
  Precipitazioni
    Pioggia
    Neve
  Raccolta
    Fiumi
    Oceani
    Falde acquifere",
            links: &[
                ("Evaporazione", "Condensazione", "porta a"),
                ("Condensazione", "Precipitazioni", "porta a"),
                ("Precipitazioni", "Raccolta", "alimenta"),
                ("Raccolta", "Evaporazione", "ricomincia con"),
            ],
        },
        palette: OCEAN,
        root: rgb(0xffffff),
        root_shape: Shape::Ellipse,
        leaf_shape: Shape::Rounded,
        layout: Layout::Radial,
        diagram: None,
    },
    Template {
        id: "project-plan",
        name: "Project plan",
        description: "Goals, scope, timeline, team, budget and risks of a project.",
        category: "Work",
        outline: "Project
  Goals
    Main outcome
    How we measure success
  Scope
    In scope
    Out of scope
  Timeline
    Kick-off
    Milestone 1
    Milestone 2
    Launch
  Team
    Owner
    Contributors
    Stakeholders
  Budget
    People
    Tools
  Risks
    Risk and mitigation",
        links: &[("Milestone 2", "Risk and mitigation", "watch")],
        it: Text {
            name: "Piano di progetto",
            description: "Obiettivi, ambito, tempi, team, budget e rischi di un progetto.",
            category: "Lavoro",
            outline: "Progetto
  Obiettivi
    Risultato principale
    Come misuriamo il successo
  Ambito
    Incluso
    Escluso
  Tempi
    Avvio
    Traguardo 1
    Traguardo 2
    Lancio
  Team
    Responsabile
    Collaboratori
    Stakeholder
  Budget
    Persone
    Strumenti
  Rischi
    Rischio e contromisura",
            links: &[("Traguardo 2", "Rischio e contromisura", "da monitorare")],
        },
        palette: PASTEL,
        root: rgb(0xffde85),
        root_shape: Shape::Pill,
        leaf_shape: Shape::Rounded,
        layout: Layout::Tree,
        diagram: None,
    },
    Template {
        id: "swot",
        name: "SWOT analysis",
        description: "Strengths, weaknesses, opportunities and threats, each in its own colour.",
        category: "Strategy",
        outline: "SWOT
  Strengths
    What we do best
    Unique resources
  Weaknesses
    What to improve
    Where we lack skills
  Opportunities
    Trends to use
    Unmet needs
  Threats
    Competition
    Changes outside our control",
        links: &[("What we do best", "Unmet needs", "can serve")],
        it: Text {
            name: "Analisi SWOT",
            description: "Punti di forza, debolezze, opportunità e minacce, ciascuno con il suo colore.",
            category: "Strategia",
            outline: "SWOT
  Punti di forza
    Cosa facciamo meglio
    Risorse uniche
  Debolezze
    Cosa migliorare
    Competenze che mancano
  Opportunità
    Tendenze da sfruttare
    Bisogni non soddisfatti
  Minacce
    Concorrenza
    Cambiamenti fuori dal nostro controllo",
            links: &[("Cosa facciamo meglio", "Bisogni non soddisfatti", "può soddisfare")],
        },
        palette: SWOT,
        root: rgb(0xffffff),
        root_shape: Shape::Ellipse,
        leaf_shape: Shape::Rounded,
        layout: Layout::Radial,
        diagram: None,
    },
    Template {
        id: "decision",
        name: "Decision making",
        description: "Compare options side by side with pros, cons and cost.",
        category: "Strategy",
        outline: "Decision to make
  Option A
    Pros
    Cons
    Cost
  Option B
    Pros
    Cons
    Cost
  Option C
    Pros
    Cons
    Cost
  Criteria
    Must have
    Nice to have",
        links: &[],
        it: Text {
            name: "Prendere una decisione",
            description: "Confronta le opzioni fianco a fianco con pro, contro e costi.",
            category: "Strategia",
            outline: "Decisione da prendere
  Opzione A
    Pro
    Contro
    Costo
  Opzione B
    Pro
    Contro
    Costo
  Opzione C
    Pro
    Contro
    Costo
  Criteri
    Indispensabile
    Desiderabile",
            links: &[],
        },
        palette: SUNSET,
        root: rgb(0xffde85),
        root_shape: Shape::Pill,
        leaf_shape: Shape::Pill,
        layout: Layout::Tree,
        diagram: None,
    },
    Template {
        id: "meeting",
        name: "Meeting notes",
        description: "Agenda, discussion, decisions and action items with owners.",
        category: "Work",
        outline: "Meeting
  Agenda
    Topic 1
    Topic 2
  Attendees
  Discussion
    Key points
    Open questions
  Decisions
  Action items
    Task, owner, date
  Next meeting",
        links: &[("Open questions", "Next meeting", "carry over to")],
        it: Text {
            name: "Verbale di riunione",
            description: "Ordine del giorno, discussione, decisioni e azioni con i responsabili.",
            category: "Lavoro",
            outline: "Riunione
  Ordine del giorno
    Punto 1
    Punto 2
  Partecipanti
  Discussione
    Punti chiave
    Domande aperte
  Decisioni
  Azioni
    Attività, responsabile, scadenza
  Prossima riunione",
            links: &[("Domande aperte", "Prossima riunione", "rimandate a")],
        },
        palette: OCEAN,
        root: rgb(0xc8eef7),
        root_shape: Shape::Pill,
        leaf_shape: Shape::Rounded,
        layout: Layout::Tree,
        diagram: None,
    },
    Template {
        id: "book",
        name: "Book summary",
        description: "Characters, plot, themes and favourite quotes of a book.",
        category: "Learning",
        outline: "Book title
  Author
  Characters
    Main character
    Supporting characters
  Plot
    Beginning
    Turning point
    Ending
  Themes
  Quotes
  My opinion",
        links: &[("Main character", "Turning point", "changes at")],
        it: Text {
            name: "Riassunto di un libro",
            description: "Personaggi, trama, temi e citazioni preferite di un libro.",
            category: "Studio",
            outline: "Titolo del libro
  Autore
  Personaggi
    Protagonista
    Personaggi secondari
  Trama
    Inizio
    Svolta
    Finale
  Temi
  Citazioni
  La mia opinione",
            links: &[("Protagonista", "Svolta", "cambia con")],
        },
        palette: SUNSET,
        root: rgb(0xffe39b),
        root_shape: Shape::Rect,
        leaf_shape: Shape::Rounded,
        layout: Layout::Tree,
        diagram: None,
    },
    Template {
        id: "fishbone",
        name: "Cause and effect",
        description: "Find the root causes of a problem, grouped by category.",
        category: "Strategy",
        outline: "Problem
  People
    Training
    Workload
  Process
    Unclear steps
    Handoffs
  Tools
    Outdated software
  Materials
    Supply delays
  Environment
    Noise
  Measurement
    Wrong metric",
        links: &[("Workload", "Handoffs", "worsens")],
        it: Text {
            name: "Causa ed effetto",
            description: "Trova le cause profonde di un problema, raggruppate per categoria.",
            category: "Strategia",
            outline: "Problema
  Persone
    Formazione
    Carico di lavoro
  Processo
    Passaggi poco chiari
    Passaggi di consegne
  Strumenti
    Software obsoleto
  Materiali
    Ritardi nelle forniture
  Ambiente
    Rumore
  Misurazione
    Indicatore sbagliato",
            links: &[("Carico di lavoro", "Passaggi di consegne", "peggiora")],
        },
        palette: FOREST,
        root: rgb(0xffb3a3),
        root_shape: Shape::Rect,
        leaf_shape: Shape::Rounded,
        layout: Layout::Tree,
        diagram: None,
    },
    Template {
        id: "weekly",
        name: "Weekly planner",
        description: "One branch per day, with a focus for the week.",
        category: "Personal",
        outline: "This week
  Focus
  Monday
  Tuesday
  Wednesday
  Thursday
  Friday
  Weekend
    Rest
    Family and friends",
        links: &[],
        it: Text {
            name: "Planner settimanale",
            description: "Un ramo per ogni giorno, con un obiettivo per la settimana.",
            category: "Personale",
            outline: "Questa settimana
  Obiettivo
  Lunedì
  Martedì
  Mercoledì
  Giovedì
  Venerdì
  Fine settimana
    Riposo
    Famiglia e amici",
            links: &[],
        },
        palette: PASTEL,
        root: rgb(0xffde85),
        root_shape: Shape::Ellipse,
        leaf_shape: Shape::Pill,
        layout: Layout::Radial,
        diagram: None,
    },
    Template {
        id: "roadmap",
        name: "Product roadmap",
        description: "Now, next and later, with dependencies between items.",
        category: "Work",
        outline: "Roadmap
  Now
    Fix onboarding
    Faster search
  Next
    Mobile app
    Sharing
  Later
    Offline mode
    Integrations",
        links: &[("Faster search", "Offline mode", "needed for"), ("Sharing", "Integrations", "opens")],
        it: Text {
            name: "Roadmap di prodotto",
            description: "Adesso, dopo e più avanti, con le dipendenze tra le attività.",
            category: "Lavoro",
            outline: "Roadmap
  Adesso
    Migliorare l’onboarding
    Ricerca più veloce
  Dopo
    App mobile
    Condivisione
  Più avanti
    Modalità offline
    Integrazioni",
            links: &[("Ricerca più veloce", "Modalità offline", "serve per"), ("Condivisione", "Integrazioni", "apre la strada a")],
        },
        palette: OCEAN,
        root: rgb(0xd0c2fc),
        root_shape: Shape::Pill,
        leaf_shape: Shape::Rounded,
        layout: Layout::Tree,
        diagram: None,
    },
    Template {
        id: "journey",
        name: "Customer journey",
        description: "The stages a customer goes through, with what they do and feel.",
        category: "Strategy",
        outline: "Customer journey
  Discover
    Sees a post
  Consider
    Compares options
  Buy
    Checks out
  Use
    First success
  Recommend
    Tells a friend",
        links: &[("Discover", "Consider", "then"), ("Consider", "Buy", "then"), ("Buy", "Use", "then"), ("Use", "Recommend", "then")],
        it: Text {
            name: "Customer journey",
            description: "Le fasi che attraversa un cliente, con cosa fa e cosa prova.",
            category: "Strategia",
            outline: "Customer journey
  Scoperta
    Vede un post
  Valutazione
    Confronta le opzioni
  Acquisto
    Completa l’ordine
  Utilizzo
    Primo successo
  Passaparola
    Lo consiglia a un amico",
            links: &[
                ("Scoperta", "Valutazione", "poi"),
                ("Valutazione", "Acquisto", "poi"),
                ("Acquisto", "Utilizzo", "poi"),
                ("Utilizzo", "Passaparola", "poi"),
            ],
        },
        palette: SUNSET,
        root: rgb(0xffffff),
        root_shape: Shape::Pill,
        leaf_shape: Shape::Rounded,
        layout: Layout::Tree,
        diagram: None,
    },
    Template {
        id: "lesson",
        name: "Lesson plan",
        description: "Objectives, materials, activities and assessment for a class.",
        category: "Learning",
        outline: "Lesson
  Objectives
    Students will be able to…
  Materials
  Activities
    Warm-up
    Main activity
    Wrap-up
  Assessment
  Homework",
        links: &[("Objectives", "Assessment", "checked by")],
        it: Text {
            name: "Piano di lezione",
            description: "Obiettivi, materiali, attività e verifica per una lezione.",
            category: "Studio",
            outline: "Lezione
  Obiettivi
    Gli studenti sapranno…
  Materiali
  Attività
    Riscaldamento
    Attività principale
    Conclusione
  Verifica
  Compiti",
            links: &[("Obiettivi", "Verifica", "verificati con")],
        },
        palette: FOREST,
        root: rgb(0xd6f0a0),
        root_shape: Shape::Ellipse,
        leaf_shape: Shape::Rounded,
        layout: Layout::Tree,
        diagram: None,
    },
    Template {
        id: "network",
        name: "Office network",
        description: "Internet, firewall, core switch, servers, Wi-Fi and clients, with IP addresses and status lights.",
        category: "Diagrams",
        outline: "",
        links: &[],
        it: Text {
            name: "Rete aziendale",
            description: "Internet, firewall, switch, server, Wi-Fi e postazioni, con indirizzi IP e stato.",
            category: "Diagrammi",
            outline: "",
            links: &[],
        },
        palette: PASTEL,
        root: rgb(0xffffff),
        root_shape: Shape::Rounded,
        leaf_shape: Shape::Rounded,
        layout: Layout::Tree,
        diagram: Some(crate::diagrams::office_network),
    },
    Template {
        id: "flowchart",
        name: "Flowchart",
        description: "Start, steps, a decision with Yes/No branches, data, document and end.",
        category: "Diagrams",
        outline: "",
        links: &[],
        it: Text {
            name: "Diagramma di flusso",
            description: "Inizio, passaggi, una decisione con rami Sì/No, dati, documento e fine.",
            category: "Diagrammi",
            outline: "",
            links: &[],
        },
        palette: PASTEL,
        root: rgb(0xffffff),
        root_shape: Shape::Rounded,
        leaf_shape: Shape::Rounded,
        layout: Layout::Tree,
        diagram: Some(crate::diagrams::flowchart),
    },
    Template {
        id: "cloud",
        name: "Cloud architecture",
        description: "Users, CDN, load balancer, services, queue, database and storage in a cloud region.",
        category: "Diagrams",
        outline: "",
        links: &[],
        it: Text {
            name: "Architettura cloud",
            description: "Utenti, CDN, load balancer, servizi, coda, database e storage in una regione cloud.",
            category: "Diagrammi",
            outline: "",
            links: &[],
        },
        palette: PASTEL,
        root: rgb(0xffffff),
        root_shape: Shape::Rounded,
        leaf_shape: Shape::Rounded,
        layout: Layout::Tree,
        diagram: Some(crate::diagrams::cloud_architecture),
    },
    Template {
        id: "org-chart",
        name: "Organisation chart",
        description: "Who reports to whom, top-down with orthogonal lines.",
        category: "Diagrams",
        outline: "",
        links: &[],
        it: Text {
            name: "Organigramma",
            description: "Chi riporta a chi, dall'alto in basso con linee ortogonali.",
            category: "Diagrammi",
            outline: "",
            links: &[],
        },
        palette: PASTEL,
        root: rgb(0xffffff),
        root_shape: Shape::Rounded,
        leaf_shape: Shape::Rounded,
        layout: Layout::Tree,
        diagram: Some(crate::diagrams::org_chart),
    },
];

pub fn find(id: &str) -> Option<&'static Template> {
    ALL.iter().find(|t| t.id.eq_ignore_ascii_case(id.trim()))
}

impl Template {
    pub fn name(&self, lang: Lang) -> &'static str {
        if lang == Lang::It { self.it.name } else { self.name }
    }

    pub fn description(&self, lang: Lang) -> &'static str {
        if lang == Lang::It { self.it.description } else { self.description }
    }

    pub fn category(&self, lang: Lang) -> &'static str {
        if lang == Lang::It { self.it.category } else { self.category }
    }

    /// A fresh map from this template, in English.
    pub fn build(&self) -> Map {
        self.build_in(Lang::En)
    }

    /// Built in code with shapes and connectors (diagram mode).
    pub fn is_diagram(&self) -> bool {
        self.diagram.is_some()
    }

    /// A fresh map from this template, in `lang`.
    pub fn build_in(&self, lang: Lang) -> Map {
        if let Some(build) = self.diagram {
            return build(lang);
        }
        let (outline, links, name) =
            if lang == Lang::It { (self.it.outline, self.it.links, self.it.name) } else { (self.outline, self.links, self.name) };
        let mut widths = Vec::new();
        let items: Vec<(usize, Topic)> =
            outline.lines().filter(|l| !l.trim().is_empty()).map(|l| (rank(&mut widths, indent(l)), Topic::new(l.trim()))).collect();
        let mut roots = tree(items);
        for root in &mut roots {
            root.color = Some(self.root);
            root.shape = Some(self.root_shape);
            root.pos = None;
            for (i, branch) in root.children.iter_mut().enumerate() {
                let color = self.palette.get(i % self.palette.len().max(1)).copied().unwrap_or(Rgb::WHITE);
                let mut stack = vec![&mut *branch];
                while let Some(t) = stack.pop() {
                    t.color = Some(color);
                    t.shape = Some(if t.children.is_empty() { self.leaf_shape } else { Shape::Rounded });
                    stack.extend(t.children.iter_mut());
                }
            }
        }
        let links: Vec<Link> = links.iter().map(|(a, b, l)| Link { from: (*a).into(), to: (*b).into(), label: (*l).into() }).collect();
        let title = roots.first().map(|r| r.label.clone()).unwrap_or_else(|| name.into());
        let mut map = build::build(&title, roots, &links);
        map.set_title(name);
        if let Some(root) = map.roots().first().copied() {
            let _ = map.set_pos(root, Point::default());
        }
        layout::apply(&mut map, self.layout);
        map
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_template_builds_with_its_links() {
        for (i, t) in ALL.iter().enumerate() {
            assert!(ALL.iter().take(i).all(|o| o.id != t.id), "duplicate {}", t.id);
            let m = t.build();
            assert!(m.node_count() >= 8, "{} is too small", t.id);
            if !t.is_diagram() {
                assert_eq!(m.roots().len(), 1, "{}", t.id);
                assert_eq!(m.edge_count(), t.links.len(), "{}: a link names a missing idea", t.id);
            }
            assert_eq!(Map::from_json(&m.to_json()).as_ref(), Ok(&m));
            let it = t.build_in(Lang::It);
            assert_eq!(it.node_count(), m.node_count(), "{}: the Italian outline has the same shape", t.id);
            if !t.is_diagram() {
                assert_eq!(it.edge_count(), t.it.links.len(), "{}: an Italian link names a missing idea", t.id);
            }
            assert_eq!(it.title(), t.it.name);
            assert_ne!(it.to_json(), m.to_json(), "{} is translated", t.id);
        }
        assert_eq!(find("swot").unwrap().name(Lang::It), "Analisi SWOT");
        assert_eq!(Lang::parse("it-IT"), Some(Lang::It));
        assert_eq!(Lang::parse("fr"), None);
        assert!(find("SWOT").is_some());
        assert!(find("nope").is_none());
    }
}
