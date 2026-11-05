import { useEffect, useRef, useState, type CSSProperties } from 'react';

/**
 * The sticky inspector starts below the page header, so a `max-height` of the whole viewport
 * pushed its footer (Activate / Close) below the fold. This returns the element's ref and a
 * `--inspector-top` style holding its distance from the viewport top, kept current on scroll
 * and resize; the CSS subtracts it from the height.
 */
export function useInspectorFit<T extends HTMLElement>(active = true) {
  const ref = useRef<T>(null);
  const [top, setTop] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!active || !element) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      setTop(Math.max(0, Math.round(element.getBoundingClientRect().top)));
    };
    const schedule = () => {
      if (frame === 0) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    return () => {
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      if (frame !== 0) cancelAnimationFrame(frame);
    };
  }, [active]);
  const style = { '--inspector-top': `${top}px` } as CSSProperties;
  return [ref, style] as const;
}
