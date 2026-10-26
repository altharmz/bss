import { shortId } from '../format/ids';
import { useToast } from './kit';

export const ID_PREFIXES = ['BA-', 'ORD-', 'BL-', 'CMD-', 'PAY-', 'RF-', 'PRD-', 'BAL-'] as const;

/** Display a shortened identifier; copy the original UUID without activating its row. */
export function Id({ id, prefix }: { id: string; prefix: (typeof ID_PREFIXES)[number] }) {
  const toast = useToast();
  return (
    <button
      type="button"
      className="bss-id"
      aria-label={`Copy ${id}`}
      title={id}
      onClick={async (event) => {
        event.stopPropagation();
        try {
          await navigator.clipboard.writeText(id);
          toast('Copied');
        } catch {
          toast('Copy failed');
        }
      }}
    >
      <span className="id-prefix">{prefix}</span>
      <span className="id-body">{shortId(id)}</span>
    </button>
  );
}
