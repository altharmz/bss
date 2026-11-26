import { act, fireEvent, render } from '@testing-library/react';
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

  it('follows scrolling in one frame per burst and drops a pending frame on unmount', () => {
    const rect = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockReturnValue({ top: 120 } as DOMRect);
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal(
      'requestAnimationFrame',
      vi.fn((callback: FrameRequestCallback) => frames.push(callback)),
    );
    const cancel = vi.fn();
    vi.stubGlobal('cancelAnimationFrame', cancel);
    const { getByTestId, unmount } = render(<Probe />);
    const inspector = getByTestId('inspector');
    expect(inspector.style.getPropertyValue('--inspector-top')).toBe('120px');
    rect.mockReturnValue({ top: -40 } as DOMRect);
    fireEvent.scroll(window);
    fireEvent.scroll(window);
    fireEvent(window, new Event('resize'));
    expect(frames).toHaveLength(1);
    act(() => frames[0]?.(0));
    // scrolled past the top: never a negative offset
    expect(inspector.style.getPropertyValue('--inspector-top')).toBe('0px');
    fireEvent.scroll(window);
    expect(frames).toHaveLength(2);
    unmount();
    expect(cancel).toHaveBeenCalledExactlyOnceWith(2);
    vi.restoreAllMocks();
  });
});
