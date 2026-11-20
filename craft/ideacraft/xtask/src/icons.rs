//! `cargo xtask icons`: render `assets/app-icon/ideacraft.svg` into the committed icon files.
//!
//! Outputs, next to the SVG:
//!   ideacraft-1024.png         the tile at 1024 px (release pages, Linux, previews)
//!   ideacraft-256.png          the tile at 256 px
//!   ideacraft-macos-1024.png   the tile on Apple's icon grid (824/1024 body, transparent margin);
//!                              packaging/macos/package.sh turns it into the .icns with sips/iconutil
//!   ideacraft.ico              Windows icon, 16 to 256 px, PNG-compressed entries
//!
//! The outputs are committed, so builds and packaging never need resvg.

use resvg::{tiny_skia, usvg};
use std::path::{Path, PathBuf};

const ICO_SIZES: &[u32] = &[16, 20, 24, 32, 40, 48, 64, 128, 256];
const TILE_VIEWBOX: &str = "viewBox=\"0 0 512 512\"";
/// 512 * 1024 / 824 wide, centred: the tile fills Apple's 824 px icon body of a 1024 px canvas.
const MAC_VIEWBOX: &str = "viewBox=\"-62.136 -62.136 636.272 636.272\"";

fn dir() -> PathBuf {
    crate::root().join("assets/app-icon")
}

/// Renders an SVG into a square PNG of `size` px.
fn render(svg: &str, size: u32) -> Result<Vec<u8>, String> {
    let tree = usvg::Tree::from_str(svg, &usvg::Options::default()).map_err(|e| format!("parse svg: {e}"))?;
    let mut pixmap = tiny_skia::Pixmap::new(size, size).ok_or("icon size must be positive")?;
    let bounds = tree.size();
    let scale = size as f32 / bounds.width().max(bounds.height());
    resvg::render(&tree, tiny_skia::Transform::from_scale(scale, scale), &mut pixmap.as_mut());
    pixmap.encode_png().map_err(|e| format!("encode png: {e}"))
}

/// Width and height from a PNG's IHDR chunk.
pub fn png_size(png: &[u8]) -> Result<(u32, u32), String> {
    let (Some(sig), Some(ihdr), Some(w), Some(h)) = (png.get(..8), png.get(12..16), png.get(16..20), png.get(20..24)) else {
        return Err("not a PNG file (too short)".into());
    };
    if sig != b"\x89PNG\r\n\x1a\n" || ihdr != b"IHDR" {
        return Err("not a PNG file".into());
    }
    let be = |b: &[u8]| u32::from_be_bytes([b[0], b[1], b[2], b[3]]);
    Ok((be(w), be(h)))
}

/// Packs square PNGs (at most 256 px each) into an ICO file. Windows Vista and later read
/// PNG-compressed entries, so each image is stored as is; 256 px is written as 0 per the format.
pub fn pack_ico(pngs: &[Vec<u8>]) -> Result<Vec<u8>, String> {
    let count = u16::try_from(pngs.len()).map_err(|_| "too many images".to_string())?;
    if count == 0 {
        return Err("need at least one PNG".into());
    }
    let mut out = Vec::new();
    out.extend_from_slice(&0u16.to_le_bytes()); // reserved
    out.extend_from_slice(&1u16.to_le_bytes()); // type: icon
    out.extend_from_slice(&count.to_le_bytes());
    let mut offset = 6 + 16 * pngs.len();
    for png in pngs {
        let (w, h) = png_size(png)?;
        if w != h || w == 0 || w > 256 {
            return Err(format!("icon images must be square and at most 256 px, got {w}x{h}"));
        }
        let dim = u8::try_from(w).unwrap_or(0); // 256 → 0
        out.extend_from_slice(&[dim, dim, 0, 0]); // width, height, palette size, reserved
        out.extend_from_slice(&1u16.to_le_bytes()); // colour planes
        out.extend_from_slice(&32u16.to_le_bytes()); // bits per pixel
        let len = u32::try_from(png.len()).map_err(|_| "PNG too large".to_string())?;
        let off = u32::try_from(offset).map_err(|_| "ICO too large".to_string())?;
        out.extend_from_slice(&len.to_le_bytes());
        out.extend_from_slice(&off.to_le_bytes());
        offset += png.len();
    }
    for png in pngs {
        out.extend_from_slice(png);
    }
    Ok(out)
}

fn write(path: &Path, bytes: &[u8]) -> Result<(), String> {
    std::fs::write(path, bytes).map_err(|e| format!("write {}: {e}", path.display()))?;
    println!("{} ({} bytes)", path.display(), bytes.len());
    Ok(())
}

pub fn run() -> Result<(), String> {
    let dir = dir();
    let svg_path = dir.join("ideacraft.svg");
    let svg = std::fs::read_to_string(&svg_path).map_err(|e| format!("read {}: {e}", svg_path.display()))?;
    if !svg.contains(TILE_VIEWBOX) {
        return Err(format!("{}: expected {TILE_VIEWBOX}", svg_path.display()));
    }
    let mac = svg.replacen(TILE_VIEWBOX, MAC_VIEWBOX, 1);

    write(&dir.join("ideacraft-1024.png"), &render(&svg, 1024)?)?;
    write(&dir.join("ideacraft-256.png"), &render(&svg, 256)?)?;
    write(&dir.join("ideacraft-macos-1024.png"), &render(&mac, 1024)?)?;
    let pngs = ICO_SIZES.iter().map(|&s| render(&svg, s)).collect::<Result<Vec<_>, _>>()?;
    write(&dir.join("ideacraft.ico"), &pack_ico(&pngs)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fake_png(size: u32) -> Vec<u8> {
        let mut v = b"\x89PNG\r\n\x1a\n\0\0\0\x0dIHDR".to_vec();
        v.extend_from_slice(&size.to_be_bytes());
        v.extend_from_slice(&size.to_be_bytes());
        v.extend_from_slice(&[8, 6, 0, 0, 0, 1, 2, 3, 4]);
        v
    }

    #[test]
    fn packs_directory_and_payloads() {
        let (a, b) = (fake_png(16), fake_png(256));
        let ico = pack_ico(&[a.clone(), b.clone()]).unwrap();
        assert_eq!(&ico[..6], &[0, 0, 1, 0, 2, 0]);
        assert_eq!(&ico[6..8], &[16, 16]);
        assert_eq!(u32::from_le_bytes(ico[14..18].try_into().unwrap()), a.len() as u32);
        assert_eq!(u32::from_le_bytes(ico[18..22].try_into().unwrap()), 38);
        assert_eq!(&ico[22..24], &[0, 0]);
        assert_eq!(u32::from_le_bytes(ico[34..38].try_into().unwrap()), 38 + a.len() as u32);
        assert_eq!(&ico[38..38 + a.len()], &a[..]);
        assert_eq!(&ico[38 + a.len()..], &b[..]);
    }

    #[test]
    fn rejects_bad_input() {
        assert!(pack_ico(&[]).is_err());
        assert!(pack_ico(&[fake_png(512)]).is_err());
        assert!(pack_ico(&[b"GIF89a".to_vec()]).is_err());
        assert!(png_size(b"\x89PNG").is_err());
        assert_eq!(png_size(&fake_png(48)).unwrap(), (48, 48));
    }

    #[test]
    fn committed_icons_are_valid() {
        let dir = dir();
        let ico = std::fs::read(dir.join("ideacraft.ico")).unwrap();
        assert_eq!(&ico[..4], &[0, 0, 1, 0]);
        assert_eq!(u16::from_le_bytes([ico[4], ico[5]]) as usize, ICO_SIZES.len());
        for (name, size) in [("ideacraft-1024.png", 1024), ("ideacraft-256.png", 256), ("ideacraft-macos-1024.png", 1024)] {
            let png = std::fs::read(dir.join(name)).unwrap();
            assert_eq!(png_size(&png).unwrap(), (size, size), "{name}");
        }
    }

    #[test]
    fn svg_renders() {
        let svg = std::fs::read_to_string(dir().join("ideacraft.svg")).unwrap();
        assert!(svg.contains(TILE_VIEWBOX));
        let png = render(&svg, 32).unwrap();
        assert_eq!(png_size(&png).unwrap(), (32, 32));
    }
}
