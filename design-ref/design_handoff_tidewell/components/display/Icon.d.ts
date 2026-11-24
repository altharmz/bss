export interface IconProps {
  /** Lucide icon name, kebab-case (e.g. "calendar-days") */
  name: string;
  /** Pixel size. Default 16 */
  size?: number;
  /** CSS color. Default currentColor */
  color?: string;
  title?: string;
  style?: React.CSSProperties;
}
export declare function Icon(props: IconProps): JSX.Element;
