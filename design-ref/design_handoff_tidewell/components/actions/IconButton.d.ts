export interface IconButtonProps {
  /** Lucide icon name */
  icon: string;
  /** Accessible label + tooltip text (required) */
  label: string;
  variant?: "ghost" | "outline" | "primary";
  size?: "sm" | "md" | "lg";
  /** Toggled-on state (tide tint) */
  active?: boolean;
  disabled?: boolean;
  onClick?: (e: React.MouseEvent) => void;
  style?: React.CSSProperties;
}
export declare function IconButton(props: IconButtonProps): JSX.Element;
