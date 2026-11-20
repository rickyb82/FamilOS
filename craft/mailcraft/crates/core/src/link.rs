//! Links handed to the system's browser or mail program (an Unsubscribe link from a message,
//! a sign-in page): only web and mail addresses, as one plain token — no spaces, quotes or
//! control characters — so nothing on the way can read part of one as a command or an option.

/// Longest link opened.
pub const MAX_LINK: usize = 8 * 1024;

/// The link, trimmed, when it is an `http:`, `https:` or `mailto:` address safe to pass as a
/// single program argument; `None` otherwise.
pub fn openable(link: &str) -> Option<&str> {
    let link = link.trim();
    let head = link.get(..8).unwrap_or(link).to_ascii_lowercase();
    let scheme = ["http://", "https://", "mailto:"].into_iter().find(|s| head.starts_with(s))?;
    let plain = link.chars().all(|c| !c.is_control() && !c.is_whitespace() && c != '"');
    (plain && link.len() > scheme.len() && link.len() <= MAX_LINK).then_some(link)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_web_and_mail_links_in_one_piece() {
        for ok in [
            "https://example.org/unsubscribe?u=1&list=2",
            " HTTP://Example.org/x|y^z%20 ",
            "mailto:list@example.org?subject=unsubscribe",
            "https://example.org/caffè",
        ] {
            assert_eq!(openable(ok), Some(ok.trim()), "{ok}");
        }
        for bad in [
            "",
            "https://",
            "mailto:",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "C:\\Windows\\System32\\calc.exe",
            "--help https://example.org",
            "https://example.org/\" & calc.exe",
            "https://example.org/a b",
            "https://example.org/\ncalc",
            "https://example.org/\u{7}",
            "https://example.org/\u{2028}x",
        ] {
            assert_eq!(openable(bad), None, "{bad:?}");
        }
        assert_eq!(openable(&format!("https://example.org/{}", "a".repeat(MAX_LINK))), None);
    }
}
