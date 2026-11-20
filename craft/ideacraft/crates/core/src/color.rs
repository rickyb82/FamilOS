use serde::{Deserialize, Deserializer, Serialize, Serializer};

use crate::Error;

/// An sRGB colour, stored in files as `#rrggbb`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Rgb(pub u8, pub u8, pub u8);

impl Rgb {
    pub const WHITE: Self = Self(0xff, 0xff, 0xff);

    /// The node fill palette offered by the toolbar (original, chosen for contrast with dark text).
    pub const PALETTE: [Self; 8] = [
        Self(0xff, 0xff, 0xff),
        Self(0xff, 0xe0, 0x8a),
        Self(0xff, 0xc2, 0xa8),
        Self(0xf6, 0xb8, 0xd1),
        Self(0xd4, 0xc4, 0xfb),
        Self(0xb3, 0xd4, 0xff),
        Self(0xb1, 0xec, 0xd8),
        Self(0xd9, 0xf2, 0xa6),
    ];

    /// Colours given in turn to the main branches of a map (from [`Self::PALETTE`]).
    pub const BRANCHES: [Self; 7] = [
        Self(0xb3, 0xd4, 0xff),
        Self(0xb1, 0xec, 0xd8),
        Self(0xd4, 0xc4, 0xfb),
        Self(0xff, 0xc2, 0xa8),
        Self(0xf6, 0xb8, 0xd1),
        Self(0xd9, 0xf2, 0xa6),
        Self(0xff, 0xe0, 0x8a),
    ];

    pub fn parse(text: &str) -> crate::Result<Self> {
        let hex = text.trim().strip_prefix('#').unwrap_or(text.trim());
        let bad = || Error::Color(text.chars().take(32).collect());
        if hex.len() != 6 || !hex.is_ascii() {
            return Err(bad());
        }
        let byte = |i: usize| hex.get(i..i + 2).and_then(|h| u8::from_str_radix(h, 16).ok()).ok_or_else(bad);
        Ok(Self(byte(0)?, byte(2)?, byte(4)?))
    }

    pub fn hex(self) -> String {
        format!("#{:02x}{:02x}{:02x}", self.0, self.1, self.2)
    }
}

impl Default for Rgb {
    fn default() -> Self {
        Self::WHITE
    }
}

impl Serialize for Rgb {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str(&self.hex())
    }
}

impl<'de> Deserialize<'de> for Rgb {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        let text = String::deserialize(d)?;
        Self::parse(&text).map_err(serde::de::Error::custom)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_and_prints_hex() {
        assert_eq!(Rgb::parse("#ffe08a"), Ok(Rgb(0xff, 0xe0, 0x8a)));
        assert_eq!(Rgb::parse("B3D4FF").map(Rgb::hex), Ok("#b3d4ff".to_string()));
        for bad in ["", "#fff", "#gggggg", "#ffé0aa", "#1234567"] {
            assert!(Rgb::parse(bad).is_err(), "{bad}");
        }
    }
}
