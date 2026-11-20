//! Provider autoconfiguration: from an e-mail address to IMAP/SMTP settings. Known providers
//! come from their public documentation; unknown domains get the conventional guesses, which
//! the engine probes in order.

use crate::account::{Security, Server, domain_of, is_email};
use crate::{Error, Result};

/// How sure the suggestion is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Confidence {
    /// A provider's documented settings.
    Known,
    /// Conventional host names; the engine should try them.
    Guess,
}

/// Settings suggested for an address.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Suggestion {
    pub provider: Option<&'static str>,
    pub confidence: Confidence,
    pub imap: Server,
    pub smtp: Server,
    /// Login name to use (the full address for almost everyone).
    pub username: String,
    /// Other combinations worth trying, best first (after `imap`/`smtp`).
    pub alternatives: Vec<(Server, Server)>,
    /// Things the person must know (app passwords, OAuth, bridges…), in the asked language.
    pub notes: Vec<String>,
    /// The OAuth provider to sign in with (`google`, `microsoft`) when the address belongs to
    /// one; a password (an app password) stays possible.
    pub oauth: Option<&'static str>,
}

struct Provider {
    name: &'static str,
    /// Exact domains, or `prefix.*` for any top-level domain.
    domains: &'static [&'static str],
    imap: (&'static str, u16, Security),
    smtp: (&'static str, u16, Security),
    /// Another SMTP option (port 587 where 465 is the default, or the reverse).
    smtp_alt: Option<(&'static str, u16, Security)>,
    note: &'static str,
    note_it: &'static str,
}

const T: Security = Security::Tls;
const S: Security = Security::StartTls;

const APP_PW: &str = "Use an app password (made in the provider's security settings), not the account password; two-step verification must be on.";
const APP_PW_IT: &str = "Usa una password per le app (creata nelle impostazioni di sicurezza del provider), non quella dell'account; la verifica in due passaggi deve essere attiva.";

const PROVIDERS: &[Provider] = &[
    Provider {
        name: "Gmail",
        domains: &["gmail.com", "googlemail.com"],
        imap: ("imap.gmail.com", 993, T),
        smtp: ("smtp.gmail.com", 465, T),
        smtp_alt: Some(("smtp.gmail.com", 587, S)),
        note: "Gmail: sign in with Google (OAuth, like Thunderbird and Outlook do) or use an app password (Google account › Security › 2-Step Verification › App passwords); IMAP must be enabled in Gmail settings.",
        note_it: "Gmail: accedi con Google (OAuth, come fanno Thunderbird e Outlook) oppure usa una password per le app (Account Google › Sicurezza › Verifica in due passaggi › Password per le app); IMAP deve essere attivo nelle impostazioni di Gmail.",
    },
    Provider {
        name: "Outlook.com",
        domains: &["outlook.*", "hotmail.*", "live.*", "msn.com"],
        imap: ("outlook.office365.com", 993, T),
        smtp: ("smtp-mail.outlook.com", 587, S),
        smtp_alt: None,
        note: "Outlook.com: Microsoft personal accounts need OAuth2 (modern authentication); a plain password may be refused. An app password works only when two-step verification is on.",
        note_it: "Outlook.com: gli account personali Microsoft richiedono OAuth2 (autenticazione moderna); una password semplice può essere rifiutata. Una password per le app funziona solo con la verifica in due passaggi attiva.",
    },
    Provider {
        name: "Yahoo Mail",
        domains: &["yahoo.*", "ymail.com", "rocketmail.com"],
        imap: ("imap.mail.yahoo.com", 993, T),
        smtp: ("smtp.mail.yahoo.com", 465, T),
        smtp_alt: Some(("smtp.mail.yahoo.com", 587, S)),
        note: APP_PW,
        note_it: APP_PW_IT,
    },
    Provider {
        name: "iCloud Mail",
        domains: &["icloud.com", "me.com", "mac.com"],
        imap: ("imap.mail.me.com", 993, T),
        smtp: ("smtp.mail.me.com", 587, S),
        smtp_alt: None,
        note: "iCloud: use an app-specific password from appleid.apple.com; the login is the full address.",
        note_it: "iCloud: usa una password specifica per le app da appleid.apple.com; il login è l'indirizzo completo.",
    },
    Provider {
        name: "AOL Mail",
        domains: &["aol.com", "aim.com"],
        imap: ("imap.aol.com", 993, T),
        smtp: ("smtp.aol.com", 465, T),
        smtp_alt: None,
        note: APP_PW,
        note_it: APP_PW_IT,
    },
    Provider {
        name: "Aruba",
        domains: &["aruba.it"],
        imap: ("imaps.aruba.it", 993, T),
        smtp: ("smtps.aruba.it", 465, T),
        smtp_alt: None,
        note: "Aruba: the login is the full address; domains hosted by Aruba use the same servers.",
        note_it: "Aruba: il login è l'indirizzo completo; i domini ospitati su Aruba usano gli stessi server.",
    },
    Provider {
        name: "Aruba PEC",
        domains: &["pec.it", "arubapec.it", "pec.aruba.it", "mypec.eu", "pecimprese.it"],
        imap: ("imaps.pec.aruba.it", 993, T),
        smtp: ("smtps.pec.aruba.it", 465, T),
        smtp_alt: None,
        note: "Aruba PEC (certified e-mail): the login is the full address.",
        note_it: "PEC Aruba: il login è l'indirizzo completo.",
    },
    Provider {
        name: "Legalmail (InfoCert PEC)",
        domains: &["legalmail.it"],
        imap: ("mbox.cert.legalmail.it", 993, T),
        smtp: ("sendm.cert.legalmail.it", 465, T),
        smtp_alt: None,
        note: "Legalmail: the login is the full address.",
        note_it: "Legalmail: il login è l'indirizzo completo.",
    },
    Provider {
        name: "Libero",
        domains: &["libero.it", "iol.it", "inwind.it", "blu.it", "giallo.it"],
        imap: ("imapmail.libero.it", 993, T),
        smtp: ("smtp.libero.it", 465, T),
        smtp_alt: None,
        note: "Libero: IMAP must be enabled in the Libero Mail settings.",
        note_it: "Libero: IMAP va attivato nelle impostazioni di Libero Mail.",
    },
    Provider {
        name: "Virgilio",
        domains: &["virgilio.it"],
        imap: ("in.virgilio.it", 993, T),
        smtp: ("out.virgilio.it", 465, T),
        smtp_alt: None,
        note: "",
        note_it: "",
    },
    Provider {
        name: "Tiscali",
        domains: &["tiscali.it"],
        imap: ("imap.tiscali.it", 993, T),
        smtp: ("smtp.tiscali.it", 465, T),
        smtp_alt: None,
        note: "",
        note_it: "",
    },
    Provider {
        name: "TIM Mail",
        domains: &["tim.it", "alice.it", "telecomitalia.it"],
        imap: ("imap.tim.it", 993, T),
        smtp: ("smtp.tim.it", 465, T),
        smtp_alt: None,
        note: "",
        note_it: "",
    },
    Provider {
        name: "Fastweb",
        domains: &["fastwebnet.it"],
        imap: ("imap.fastwebnet.it", 993, T),
        smtp: ("smtp.fastwebnet.it", 465, T),
        smtp_alt: None,
        note: "",
        note_it: "",
    },
    Provider {
        name: "GMX",
        domains: &["gmx.com", "gmx.us", "gmx.co.uk", "gmx.fr", "gmx.es", "gmx.it"],
        imap: ("imap.gmx.com", 993, T),
        smtp: ("mail.gmx.com", 587, S),
        smtp_alt: Some(("mail.gmx.com", 465, T)),
        note: "GMX: IMAP must be enabled in the GMX settings.",
        note_it: "GMX: IMAP va attivato nelle impostazioni GMX.",
    },
    Provider {
        name: "GMX (Germany)",
        domains: &["gmx.net", "gmx.de", "gmx.at", "gmx.ch"],
        imap: ("imap.gmx.net", 993, T),
        smtp: ("mail.gmx.net", 587, S),
        smtp_alt: None,
        note: "GMX: IMAP must be enabled in the GMX settings.",
        note_it: "GMX: IMAP va attivato nelle impostazioni GMX.",
    },
    Provider {
        name: "WEB.DE",
        domains: &["web.de"],
        imap: ("imap.web.de", 993, T),
        smtp: ("smtp.web.de", 587, S),
        smtp_alt: None,
        note: "",
        note_it: "",
    },
    Provider {
        name: "mail.com",
        domains: &["mail.com", "email.com", "usa.com", "europe.com"],
        imap: ("imap.mail.com", 993, T),
        smtp: ("smtp.mail.com", 587, S),
        smtp_alt: None,
        note: "",
        note_it: "",
    },
    Provider {
        name: "Zoho Mail",
        domains: &["zoho.com", "zohomail.com"],
        imap: ("imap.zoho.com", 993, T),
        smtp: ("smtp.zoho.com", 465, T),
        smtp_alt: Some(("smtp.zoho.com", 587, S)),
        note: "Zoho: IMAP access must be enabled in Zoho Mail settings; EU accounts use imap.zoho.eu / smtp.zoho.eu.",
        note_it: "Zoho: l'accesso IMAP va attivato nelle impostazioni di Zoho Mail; gli account EU usano imap.zoho.eu / smtp.zoho.eu.",
    },
    Provider {
        name: "Zoho Mail (EU)",
        domains: &["zoho.eu", "zohomail.eu"],
        imap: ("imap.zoho.eu", 993, T),
        smtp: ("smtp.zoho.eu", 465, T),
        smtp_alt: None,
        note: "",
        note_it: "",
    },
    Provider {
        name: "Fastmail",
        domains: &["fastmail.com", "fastmail.fm"],
        imap: ("imap.fastmail.com", 993, T),
        smtp: ("smtp.fastmail.com", 465, T),
        smtp_alt: None,
        note: APP_PW,
        note_it: APP_PW_IT,
    },
    Provider {
        name: "Proton Mail (Bridge)",
        domains: &["proton.me", "protonmail.com", "protonmail.ch", "pm.me"],
        imap: ("127.0.0.1", 1143, S),
        smtp: ("127.0.0.1", 1025, S),
        smtp_alt: None,
        note: "Proton Mail has no public IMAP: run Proton Mail Bridge on this computer, use the login and password the Bridge shows, and accept its local certificate (trust invalid certificates).",
        note_it: "Proton Mail non ha IMAP pubblico: avvia Proton Mail Bridge su questo computer, usa login e password mostrati dal Bridge e accetta il suo certificato locale (fidati dei certificati non validi).",
    },
    Provider {
        name: "Posteo",
        domains: &["posteo.de", "posteo.net", "posteo.eu", "posteo.org"],
        imap: ("posteo.de", 993, T),
        smtp: ("posteo.de", 587, S),
        smtp_alt: Some(("posteo.de", 465, T)),
        note: "",
        note_it: "",
    },
    Provider {
        name: "mailbox.org",
        domains: &["mailbox.org"],
        imap: ("imap.mailbox.org", 993, T),
        smtp: ("smtp.mailbox.org", 465, T),
        smtp_alt: None,
        note: "",
        note_it: "",
    },
    Provider {
        name: "Yandex Mail",
        domains: &["yandex.com", "yandex.ru", "ya.ru"],
        imap: ("imap.yandex.com", 993, T),
        smtp: ("smtp.yandex.com", 465, T),
        smtp_alt: None,
        note: APP_PW,
        note_it: APP_PW_IT,
    },
    Provider {
        name: "Infomaniak",
        domains: &["ik.me", "ikmail.com", "etik.com", "infomaniak.com"],
        imap: ("mail.infomaniak.com", 993, T),
        smtp: ("mail.infomaniak.com", 465, T),
        smtp_alt: None,
        note: "",
        note_it: "",
    },
];

/// Hosting providers whose servers are shared by the domains they host (tried for unknown
/// domains after the conventional names).
/// (host, port, security) of a server.
type Endpoint = (&'static str, u16, Security);

const HOSTED: &[(&str, Endpoint, Endpoint)] = &[
    ("Aruba (hosted domain)", ("imaps.aruba.it", 993, T), ("smtps.aruba.it", 465, T)),
    ("Microsoft 365", ("outlook.office365.com", 993, T), ("smtp.office365.com", 587, S)),
    ("Google Workspace", ("imap.gmail.com", 993, T), ("smtp.gmail.com", 465, T)),
    ("OVH", ("ssl0.ovh.net", 993, T), ("ssl0.ovh.net", 465, T)),
    ("Register.it", ("imaps.register.it", 993, T), ("smtps.register.it", 465, T)),
];

fn matches(pattern: &str, domain: &str) -> bool {
    match pattern.strip_suffix(".*") {
        Some(prefix) => domain.strip_prefix(prefix).is_some_and(|rest| rest.starts_with('.') && rest.len() > 1),
        None => pattern == domain,
    }
}

fn find_provider(domain: &str) -> Option<&'static Provider> {
    PROVIDERS.iter().find(|p| p.domains.iter().any(|d| matches(d, domain)))
}

fn server(s: (&str, u16, Security)) -> Server {
    Server::new(s.0, s.1, s.2)
}

/// Suggests settings for an address (notes in Italian when `italian`).
pub fn suggest(email: &str, italian: bool) -> Result<Suggestion> {
    if !is_email(email) {
        return Err(Error::Config(format!("`{email}` is not an e-mail address")));
    }
    let domain = domain_of(email).unwrap_or_default();
    let username = email.trim().to_string();
    if let Some(p) = find_provider(&domain) {
        let mut alternatives = Vec::new();
        if let Some(alt) = p.smtp_alt {
            alternatives.push((server(p.imap), server(alt)));
        }
        let note = if italian { p.note_it } else { p.note };
        let oauth = crate::oauth::provider_for_email(email);
        let mut notes = if note.is_empty() { Vec::new() } else { vec![note.to_string()] };
        if let Some(o) = oauth {
            notes.push(if italian { o.note_it.to_string() } else { o.note.to_string() });
        }
        return Ok(Suggestion {
            provider: Some(p.name),
            confidence: Confidence::Known,
            imap: server(p.imap),
            smtp: server(p.smtp),
            username,
            alternatives,
            notes,
            oauth: oauth.map(|o| o.id),
        });
    }
    // Conventional names first, then the big hosting providers.
    let imaps = [
        Server::new(&format!("imap.{domain}"), 993, T),
        Server::new(&format!("mail.{domain}"), 993, T),
        Server::new(&domain, 993, T),
        Server::new(&format!("imap.{domain}"), 143, S),
        Server::new(&format!("mail.{domain}"), 143, S),
    ];
    let smtps = [
        Server::new(&format!("smtp.{domain}"), 587, S),
        Server::new(&format!("mail.{domain}"), 587, S),
        Server::new(&format!("smtp.{domain}"), 465, T),
        Server::new(&format!("mail.{domain}"), 465, T),
        Server::new(&domain, 587, S),
    ];
    let mut alternatives: Vec<(Server, Server)> = Vec::new();
    for (i, imap) in imaps.iter().enumerate() {
        let smtp = smtps.get(i.min(smtps.len() - 1)).cloned().unwrap_or_default();
        alternatives.push((imap.clone(), smtp));
    }
    for (_, i, s) in HOSTED {
        alternatives.push((server(*i), server(*s)));
    }
    let (imap, smtp) = alternatives.remove(0);
    let note = if italian {
        format!(
            "`{domain}` non è un provider noto: MailCraft prova i nomi convenzionali (imap./mail./smtp.{domain}) e poi i server dei grandi hosting (Aruba, Microsoft 365, Google Workspace, OVH, Register.it). Se non funziona, chiedi i server IMAP e SMTP a chi gestisce il dominio."
        )
    } else {
        format!(
            "`{domain}` is not a known provider: MailCraft tries the conventional names (imap./mail./smtp.{domain}) and then the big hosting providers (Aruba, Microsoft 365, Google Workspace, OVH, Register.it). If none works, ask whoever runs the domain for the IMAP and SMTP servers."
        )
    };
    Ok(Suggestion { provider: None, confidence: Confidence::Guess, imap, smtp, username, alternatives, notes: vec![note], oauth: None })
}

/// Every known provider: name and the first domain, for documentation and the MCP tool.
pub fn providers() -> Vec<(&'static str, &'static str)> {
    PROVIDERS.iter().map(|p| (p.name, p.domains.first().copied().unwrap_or_default())).collect()
}

/// The name of the hosting provider whose servers these are, if any.
pub fn hosted_name(imap: &Server) -> Option<&'static str> {
    HOSTED.iter().find(|(_, i, _)| i.0 == imap.host).map(|(n, _, _)| *n)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_providers_and_wildcards() {
        let g = suggest("anna@gmail.com", true).unwrap();
        assert_eq!(g.provider, Some("Gmail"));
        assert_eq!(g.imap, Server::new("imap.gmail.com", 993, Security::Tls));
        assert_eq!(g.smtp.port, 465);
        assert_eq!(g.alternatives.len(), 1);
        assert!(g.notes[0].contains("password per le app"));
        let h = suggest("luca@hotmail.it", false).unwrap();
        assert_eq!(h.provider, Some("Outlook.com"));
        assert_eq!(h.smtp.security, Security::StartTls);
        assert_eq!(suggest("x@pec.it", false).unwrap().imap.host, "imaps.pec.aruba.it");
        assert_eq!(suggest("x@arubapec.it", false).unwrap().provider, Some("Aruba PEC"));
        assert_eq!(suggest("x@libero.it", false).unwrap().imap.host, "imapmail.libero.it");
        assert_eq!(suggest("x@proton.me", false).unwrap().imap.port, 1143);
        assert!(!matches("hotmail.*", "hotmail"));
        assert!(matches("hotmail.*", "hotmail.co.uk"));
    }

    #[test]
    fn unknown_domains_get_guesses() {
        let s = suggest("Mario@Sirius-Example.IT", true).unwrap();
        assert_eq!(s.provider, None);
        assert_eq!(s.confidence, Confidence::Guess);
        assert_eq!(s.imap.host, "imap.sirius-example.it");
        assert_eq!(s.smtp, Server::new("smtp.sirius-example.it", 587, Security::StartTls));
        assert!(s.alternatives.len() >= 8);
        assert!(s.alternatives.iter().any(|(i, _)| i.host == "imaps.aruba.it"));
        assert_eq!(hosted_name(&Server::new("imaps.aruba.it", 993, T)), Some("Aruba (hosted domain)"));
        assert!(suggest("not an address", false).is_err());
        assert!(providers().len() > 20);
    }
}
