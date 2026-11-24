/**
 * @startingPoint section="Surfaces" subtitle="Hairline card, soft and dark variants" viewport="700x360"
 */
export interface CardProps {
  variant?: "default" | "soft" | "dark" | "outline";
  /** Inner padding in px. Default 20 */
  padding?: number | string;
  /** Hover lifts with pop shadow */
  interactive?: boolean;
  onClick?: () => void;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}
export declare function Card(props: CardProps): JSX.Element;
