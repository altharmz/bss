export interface TagProps {
  /** Swatch color (project / label color) */
  color?: string;
  /** Shows a remove (x) affordance */
  onRemove?: () => void;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}
export declare function Tag(props: TagProps): JSX.Element;
