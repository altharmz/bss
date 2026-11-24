export interface DialogProps {
  open: boolean;
  onClose?: () => void;
  /** Rendered in the serif display face */
  title?: React.ReactNode;
  description?: React.ReactNode;
  /** Right-aligned action row on a page-tinted footer */
  footer?: React.ReactNode;
  /** Max width px. Default 520 */
  width?: number;
  /** Render the panel without the fixed overlay (for docs/cards) */
  inline?: boolean;
  children?: React.ReactNode;
}
export declare function Dialog(props: DialogProps): JSX.Element | null;
