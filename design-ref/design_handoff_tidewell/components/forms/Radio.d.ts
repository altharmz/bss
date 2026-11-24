export interface RadioProps {
  checked?: boolean;
  /** Called with this radio's value */
  onChange?: (value: string) => void;
  value: string;
  label?: React.ReactNode;
  disabled?: boolean;
  style?: React.CSSProperties;
}
export declare function Radio(props: RadioProps): JSX.Element;
