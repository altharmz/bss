export interface ToastProps {
  tone?: "neutral" | "success" | "warning" | "danger";
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: { label: string; onClick?: () => void };
  onClose?: () => void;
  style?: React.CSSProperties;
}
export declare function Toast(props: ToastProps): JSX.Element;
