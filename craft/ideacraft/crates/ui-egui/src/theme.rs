//! Colours and the egui style. Panels and the canvas read colours from here, never as literals.

use egui::{Color32, CornerRadius, FontFamily, FontId, Stroke, TextStyle, Visuals};

#[derive(Clone, Copy, Debug)]
pub struct Tokens {
    pub dark: bool,
    // Canvas.
    pub canvas: Color32,
    pub grid_dot: Color32,
    /// Text on ideas (their fills are always light).
    pub text: Color32,
    pub node_border: Color32,
    pub selection: Color32,
    pub branch: Color32,
    pub link: Color32,
    pub link_label_bg: Color32,
    // Chrome.
    pub chrome: Color32,
    pub panel: Color32,
    pub border: Color32,
    pub ui_text: Color32,
    pub muted: Color32,
    pub hover: Color32,
    pub active: Color32,
    pub accent: Color32,
    pub on_accent: Color32,
    pub card: Color32,
}

impl Tokens {
    pub const LIGHT: Self = Self {
        dark: false,
        canvas: Color32::from_rgb(0xf3, 0xf4, 0xf8),
        grid_dot: Color32::from_rgb(0xd6, 0xd9, 0xe3),
        text: Color32::from_rgb(0x1f, 0x22, 0x2b),
        node_border: Color32::from_rgb(0xb4, 0xb9, 0xc6),
        selection: Color32::from_rgb(0x5b, 0x5b, 0xf0),
        branch: Color32::from_rgb(0x8a, 0x90, 0xa3),
        link: Color32::from_rgb(0xe0, 0x6a, 0x2c),
        link_label_bg: Color32::from_rgb(0xff, 0xff, 0xff),
        chrome: Color32::from_rgb(0xff, 0xff, 0xff),
        panel: Color32::from_rgb(0xfa, 0xfa, 0xfc),
        border: Color32::from_rgb(0xe3, 0xe5, 0xec),
        ui_text: Color32::from_rgb(0x22, 0x25, 0x30),
        muted: Color32::from_rgb(0x6b, 0x70, 0x80),
        hover: Color32::from_rgb(0xee, 0xef, 0xf5),
        active: Color32::from_rgb(0xe6, 0xe6, 0xfd),
        accent: Color32::from_rgb(0x5b, 0x5b, 0xf0),
        on_accent: Color32::WHITE,
        card: Color32::WHITE,
    };

    pub const DARK: Self = Self {
        dark: true,
        canvas: Color32::from_rgb(0x1a, 0x1b, 0x21),
        grid_dot: Color32::from_rgb(0x33, 0x36, 0x40),
        text: Color32::from_rgb(0x1f, 0x22, 0x2b),
        node_border: Color32::from_rgb(0x55, 0x5a, 0x69),
        selection: Color32::from_rgb(0x8f, 0x8f, 0xff),
        branch: Color32::from_rgb(0x70, 0x76, 0x88),
        link: Color32::from_rgb(0xff, 0x9a, 0x5c),
        link_label_bg: Color32::from_rgb(0x2a, 0x2c, 0x35),
        chrome: Color32::from_rgb(0x22, 0x23, 0x2a),
        panel: Color32::from_rgb(0x26, 0x27, 0x2f),
        border: Color32::from_rgb(0x36, 0x38, 0x43),
        ui_text: Color32::from_rgb(0xe8, 0xe9, 0xee),
        muted: Color32::from_rgb(0xa0, 0xa5, 0xb4),
        hover: Color32::from_rgb(0x32, 0x34, 0x3e),
        active: Color32::from_rgb(0x38, 0x38, 0x66),
        accent: Color32::from_rgb(0x7b, 0x7b, 0xff),
        on_accent: Color32::WHITE,
        card: Color32::from_rgb(0x2c, 0x2e, 0x37),
    };

    pub fn for_dark_mode(dark: bool) -> Self {
        if dark { Self::DARK } else { Self::LIGHT }
    }
}

/// Applies IdeaCraft's look to egui: rounder corners, roomier spacing, calmer widgets.
pub fn apply(ctx: &egui::Context, t: &Tokens) {
    let mut visuals = if t.dark { Visuals::dark() } else { Visuals::light() };
    visuals.panel_fill = t.chrome;
    visuals.window_fill = t.card;
    visuals.extreme_bg_color = t.card;
    visuals.faint_bg_color = t.panel;
    visuals.window_stroke = Stroke::new(1.0, t.border);
    visuals.window_corner_radius = CornerRadius::same(12);
    visuals.menu_corner_radius = CornerRadius::same(10);
    visuals.selection.bg_fill = t.active;
    visuals.selection.stroke = Stroke::new(1.0, t.accent);
    visuals.hyperlink_color = t.accent;
    let radius = CornerRadius::same(8);
    for w in [
        &mut visuals.widgets.noninteractive,
        &mut visuals.widgets.inactive,
        &mut visuals.widgets.hovered,
        &mut visuals.widgets.active,
        &mut visuals.widgets.open,
    ] {
        w.corner_radius = radius;
    }
    visuals.widgets.noninteractive.bg_stroke = Stroke::new(1.0, t.border);
    visuals.widgets.noninteractive.fg_stroke = Stroke::new(1.0, t.ui_text);
    visuals.widgets.inactive.weak_bg_fill = Color32::TRANSPARENT;
    visuals.widgets.inactive.bg_fill = t.panel;
    visuals.widgets.inactive.bg_stroke = Stroke::NONE;
    visuals.widgets.inactive.fg_stroke = Stroke::new(1.0, t.ui_text);
    visuals.widgets.hovered.weak_bg_fill = t.hover;
    visuals.widgets.hovered.bg_fill = t.hover;
    visuals.widgets.hovered.bg_stroke = Stroke::NONE;
    visuals.widgets.hovered.fg_stroke = Stroke::new(1.0, t.ui_text);
    visuals.widgets.active.weak_bg_fill = t.active;
    visuals.widgets.active.bg_fill = t.active;
    visuals.widgets.active.bg_stroke = Stroke::NONE;
    visuals.widgets.active.fg_stroke = Stroke::new(1.0, t.ui_text);
    visuals.widgets.open.weak_bg_fill = t.active;
    let theme = if t.dark { egui::Theme::Dark } else { egui::Theme::Light };
    ctx.set_theme(theme);
    ctx.style_mut_of(theme, |s| s.visuals = visuals);
    ctx.all_styles_mut(|s| {
        s.spacing.item_spacing = egui::vec2(8.0, 6.0);
        s.spacing.button_padding = egui::vec2(10.0, 5.0);
        s.spacing.interact_size.y = 30.0;
        s.spacing.menu_margin = egui::Margin::same(8);
        s.text_styles.insert(TextStyle::Body, FontId::new(14.0, FontFamily::Proportional));
        s.text_styles.insert(TextStyle::Button, FontId::new(14.0, FontFamily::Proportional));
        s.text_styles.insert(TextStyle::Small, FontId::new(12.0, FontFamily::Proportional));
        s.text_styles.insert(TextStyle::Heading, FontId::new(18.0, FontFamily::Proportional));
    });
}
