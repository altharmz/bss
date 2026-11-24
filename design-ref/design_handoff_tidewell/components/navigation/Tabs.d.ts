export interface TabItem { id: string; label: string; count?: number }
export interface TabsProps {
  items: TabItem[];
  value: string;
  onChange?: (id: string) => void;
  /** underline: page-level views. segmented: compact in-panel toggles */
  variant?: "underline" | "segmented";
  style?: React.CSSProperties;
}
export declare function Tabs(props: TabsProps): JSX.Element;
