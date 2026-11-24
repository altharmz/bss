# Tidewell Design System

Tidewell is a fictional team-planning web app: teams lay out their week as a board of tasks grouped by status, tagged by project, and owned by people. This design system covers its brand foundations, React component library, and a click-through UI kit of the core app.

## Sources
- **GitHub:** https://github.com/VoltAgent/awesome-design-md (branch `main`) — a collection of ~85 DESIGN.md brand analyses. Tidewell is an **original** system. It borrows general ideas from two of those files, without copying either brand's identity:
  - `design-md/zapier/DESIGN.md` — warm cream neutrals in place of pure white, warm (not pure black) ink, one saturated accent, a 10–12px middle radius.
  - `design-md/resend/DESIGN.md` — an editorial serif for display moments over a utilitarian sans, mono for technical detail, hairline borders doing the job of shadows.
- Explore that repo further if you want more reference patterns for future surfaces.
- No logo, font files, screenshots, or product code were supplied.

## Index
- `styles.css`: global entry point (imports only)
- `tokens/`: `fonts.css`, `colors.css`, `typography.css`, `spacing.css`, `effects.css`, `base.css`
- `components/`: React primitives (see below), each with `.jsx`, `.d.ts` and `.prompt.md`, plus one `*.card.html` per folder
- `guidelines/`: foundation specimen cards (colors, type, spacing, radii, elevation, motion, brand)
- `ui_kits/app/`: Tidewell web app (board, list, task dialog, sign in, inbox)
- `templates/deck/`: 16:9 slide deck template (`Deck.dc.html`) with 8 layouts: title, agenda, section divider, statement, text + image, comparison, metrics, closing
- `thumbnail.html`, `SKILL.md`, `github.md`

## Components
- **actions/**: Button, IconButton
- **forms/**: Input, Select, Checkbox, Radio, Switch
- **display/**: Card, Badge, Tag, Avatar, Icon
- **navigation/**: Tabs
- **overlay/**: Dialog, Toast, Tooltip

No source defined a component inventory, so this is a standard set sized for a planning app.
**Intentional additions:** Icon (a wrapper so every glyph comes from Lucide and takes on the text color), and Avatar (every task has an owner).

---

## CONTENT FUNDAMENTALS
- **Voice:** calm and plain, like a colleague who keeps the plan. Tidewell is there to make the week feel manageable, so the copy should never sound urgent or excited.
- **Person:** address the user as "you", and say "your team". The product doesn't refer to itself as "I" or "we" in UI copy.
- **Casing:** sentence case everywhere, including buttons, titles, tabs and menus. UPPERCASE is used only for mono eyebrows ("WEEK 48", "PROJECT").
- **Buttons:** verb first and specific. "Create task", "Save changes", "Email me a sign-in link". Avoid "Submit" and "OK".
- **Status words:** Planned, In progress, Review, Done. Health is shown as On track, At risk or Blocked.
- **Empty states:** one serif line plus one plain sentence. "You're all caught up" / "Mentions and assignments land here."
- **Toasts:** past tense with the object as a subtitle. "Task created", then "Draft launch email".
- **Numbers:** mono for IDs, counts and ratios ("TW-1042", "3/5", "14 / 22 done"). Dates are short and relative: "Fri", "Next wk", "Nov 23 – 29".
- **No emoji, no exclamation marks**, and no error codes shown to users.

## VISUAL FOUNDATIONS
- **Color temperature:** everything is warm. The page is sand-50 `#fbfaf6`, cards are sand-0 `#fffefb`, and ink is sand-900 `#1b1e1c` (a green-tinged near-black). There are no cool greys.
- **Accent:** Tide 600 `#0f6b5e` (deep teal) is used for the primary button, focus ring, checked controls, the active tab underline and the in-progress icon. Use one primary button per view.
- **Secondary hues** are for data, not chrome: ember, plum, blue and amber appear only as project swatches, avatar tints and status colors. Ember `#d9622b` is the one highlight hue.
- **Type:** Newsreader (serif, weight 400, tight negative tracking, line-height 1.0–1.1) for page titles, dialog titles, empty states and the wordmark. Italic Newsreader in muted ink adds a secondary phrase ("This week *Nov 23 – 29*"). Schibsted Grotesk handles all UI text: 600 for headings and labels, 400 for body. Geist Mono covers IDs, counts, eyebrows and shortcuts.
- **Spacing:** 4px base with half-steps (2, 4, 8, 12, 16, 20, 24, 32, 48, 64, 96). Page gutters are 32px, card padding 14–20px and dialog padding 24px.
- **Radii:** 4 for checkboxes and tag internals, 6 for tags, small controls and tooltips, 10 for buttons and inputs, 14 for cards and toasts, 20 for dialogs, pill for badges and avatars. Buttons are never pill-shaped.
- **Cards:** white fill, 1px sand-200 hairline, 14px radius and no resting shadow. Interactive cards darken the border to sand-300 on hover and lift with `--shadow-pop`. The `soft` variant (sand-100, no border) is for inset regions. The `dark` variant (ink) is for one emphasis block. The dashed `outline` variant is for empty and drop zones.
- **Elevation:** hairlines come first. Only floating layers get shadows: popovers and hover use `--shadow-pop`, dialogs use `--shadow-dialog`. Shadows are tinted with warm ink and never pure black.
- **Backgrounds:** flat color only. No gradients, textures, patterns or photography in the app chrome. The sidebar is a sand-100 panel separated by a hairline.
- **Transparency & blur:** used only for the dialog scrim (ink at 36% with a 3px backdrop blur).
- **Hover:** neutral controls move up one sand step (transparent → sand-100 → sand-200). Filled buttons move one shade darker (tide-600 → tide-700). Table rows tint to sand-100.
- **Press:** the button moves down 1px. There is no scale or bounce.
- **Focus:** a 3px ring in tide at 28% opacity, and inputs switch to a tide border. It shows only for keyboard focus (focus-visible).
- **Motion:** 120ms for hover, press and focus, 200ms for toggles, cards and tooltips, 320ms for overlays. Easing is `cubic-bezier(.2,.7,.2,1)` ease-out, motion is limited to fades and 2px slides, and nothing bounces.
- **Layout:** a fixed 248px sidebar plus a fluid main column. The page header (eyebrow, serif title, actions) sits above underline Tabs, then a filter row, then content. Boards use 4 columns with a 240px minimum.
- **Imagery:** none ships with the system. If photography is added, it should be warm, natural-light and lightly desaturated, set in 14px-radius frames.

## ICONOGRAPHY
- **Set:** Lucide (https://lucide.dev), outline style with a 2px stroke on a 24px grid, loaded from CDN `lucide-static@0.460.0`. **This is a substitution:** no icon set was provided, and Lucide fits the hairline, plain aesthetic.
- **Usage:** always through the `<Icon name="…" />` component. It applies the SVG as a CSS mask, so the icon takes on `currentColor`. Sizes are 13–14 in dense metadata, 16 by default and 20–22 in empty states.
- Icons sit to the left of their labels. Icon-only controls must use `IconButton` with a `label`, and should be wrapped in a `Tooltip` where the meaning isn't obvious.
- **No emoji. No unicode glyphs as icons**, except ⌘ inside shortcut hints.
- **Logo:** none was provided. The brand is set as the word "Tidewell" in Newsreader 400 with -0.4 to -0.9px tracking. Do not draw a logo mark.

## Fonts
All three faces are loaded from Google Fonts: Newsreader, Schibsted Grotesk and Geist Mono. No font binaries are bundled. To self-host or change faces, replace the `@import` in `tokens/fonts.css` with `@font-face` rules.

## SLIDES
- 1920×1080. Title and closing slides use the Tide accent background, section dividers use ink, statement slides use sand-100, and everything else sits on the sand page.
- Titles are set in Newsreader 400: 72–80px on content slides, 128–140px on title and section slides. Above each title is a mono eyebrow ("02 · TIME") at 24px with 0.08em tracking. Body text is Schibsted Grotesk at 32–44px.
- Lists use hairline dividers, comparisons pair a white card with an ink card, and metrics are shown as large serif numbers. The only color in lists comes from project swatches.
- Images go in rounded (14px) image slots. Nothing on a slide is smaller than 24px.
