export interface AvatarProps {
  /** Full name — initials and tint derive from it */
  name: string;
  src?: string;
  /** Default 28 */
  size?: number;
  /** White ring for overlapping stacks */
  ring?: boolean;
  style?: React.CSSProperties;
}
export declare function Avatar(props: AvatarProps): JSX.Element;
