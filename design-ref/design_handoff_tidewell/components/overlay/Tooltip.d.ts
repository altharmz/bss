export interface TooltipProps {
  label: React.ReactNode;
  side?: "top" | "bottom";
  /** Force visible (docs) */
  open?: boolean;
  children: React.ReactNode;
}
export declare function Tooltip(props: TooltipProps): JSX.Element;
