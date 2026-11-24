import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Icon, ICON_NAMES } from './Icon';

describe('Icon', () => {
  it('renders a decorative Lucide outline in the current text colour', () => {
    const { container } = render(<Icon name="calendar" />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('aria-hidden')).toBe('true');
    expect(svg?.getAttribute('role')).toBeNull();
    expect(svg?.getAttribute('stroke')).toBe('currentColor');
    expect(svg?.getAttribute('stroke-width')).toBe('2');
    expect(svg?.getAttribute('width')).toBe('16');
    expect(svg?.classList.contains('bss-icon')).toBe(true);
    expect(svg?.getAttribute('data-icon')).toBe('calendar');
  });

  it('names a meaningful icon and takes a size and class', () => {
    const { getByRole } = render(
      <Icon name="triangle-alert" size={14} title="Warning" className="extra" />,
    );
    const svg = getByRole('img', { name: 'Warning' });
    expect(svg.getAttribute('aria-hidden')).toBeNull();
    expect(svg.getAttribute('width')).toBe('14');
    expect(svg.classList.contains('extra')).toBe(true);
  });

  it('renders every registered icon', () => {
    for (const name of ICON_NAMES) {
      const { container, unmount } = render(<Icon name={name} />);
      expect(
        container.querySelector(
          `svg[data-icon="${name}"] path, svg[data-icon="${name}"] circle, svg[data-icon="${name}"] polygon, svg[data-icon="${name}"] rect`,
        ),
      ).not.toBeNull();
      unmount();
    }
  });
});
