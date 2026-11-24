export interface InputProps {
  label?: string;
  hint?: string;
  /** Error message — turns border red and replaces hint */
  error?: string;
  /** Leading Lucide icon name */
  icon?: string;
  id?: string;
  size?: "sm" | "md";
  type?: string;
  placeholder?: string;
  value?: string;
  defaultValue?: string;
  disabled?: boolean;
  onChange?: (e: React.ChangeEvent<HTMLInputElement>) => void;
  style?: React.CSSProperties;
}
export declare function Input(props: InputProps): JSX.Element;
