import { fireEvent, screen } from '@testing-library/react';

/** Open a Combobox input and click the option named `name` (the real pointer path). */
export function pickOption(input: HTMLElement, name: string | RegExp) {
  fireEvent.focus(input);
  fireEvent.click(screen.getByRole('option', { name }));
}
