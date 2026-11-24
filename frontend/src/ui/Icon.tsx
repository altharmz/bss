import {
  ArrowLeft,
  Calendar,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsUpDown,
  CircleCheck,
  Copy,
  Ellipsis,
  Info,
  OctagonAlert,
  Plus,
  Search,
  TriangleAlert,
  X,
  type LucideIcon,
} from 'lucide-react';

/** The Lucide icons the UI uses, by their Lucide name. Tidewell T4: every glyph comes from
 * Lucide through this one primitive, bundled (no CDN); add an icon here before using it,
 * so the bundle carries only these. */
const ICONS = {
  'arrow-left': ArrowLeft,
  calendar: Calendar,
  check: Check,
  'chevron-down': ChevronDown,
  'chevron-right': ChevronRight,
  'chevrons-up-down': ChevronsUpDown,
  'circle-check': CircleCheck,
  copy: Copy,
  ellipsis: Ellipsis,
  info: Info,
  'octagon-alert': OctagonAlert,
  plus: Plus,
  search: Search,
  'triangle-alert': TriangleAlert,
  x: X,
} satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof ICONS;

export const ICON_NAMES = Object.keys(ICONS) as IconName[];

/** A Lucide outline icon (2 px stroke on a 24 px grid) in the current text colour. Sizes:
 * 13-14 in dense rows, 16 by default, 20-22 in empty states. Decorative unless `title`
 * names it. */
export function Icon({
  name,
  size = 16,
  title,
  className,
}: {
  name: IconName;
  size?: number;
  title?: string;
  className?: string;
}) {
  const Glyph = ICONS[name];
  return (
    <Glyph
      size={size}
      strokeWidth={2}
      className={`bss-icon${className ? ` ${className}` : ''}`}
      data-icon={name}
      {...(title ? { role: 'img', 'aria-label': title } : { 'aria-hidden': true })}
    />
  );
}
