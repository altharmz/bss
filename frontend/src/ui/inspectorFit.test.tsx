import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useInspectorFit } from './inspectorFit';

function Probe() {
  const [ref, style] = useInspectorFit<HTMLElement>();
  return <aside ref={ref} style={style} data-testid="inspector" />;
}

describe('useInspectorFit', () => {
  it('publishes the inspector top so its height stops below the fold', () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      top: 212.4,
    } as DOMRect);
    const { getByTestId } = render(<Probe />);
    expect(getByTestId('inspector').style.getPropertyValue('--inspector-top')).toBe('212px');
  });
});
