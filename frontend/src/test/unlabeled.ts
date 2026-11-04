// B27: every form control carries a visible label or an aria name.
export function unlabeledControls(root: ParentNode = document): string[] {
  return Array.from(
    root.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
      'input, select, textarea',
    ),
  )
    .filter((control) => !(control instanceof HTMLInputElement && control.type === 'hidden'))
    .filter(
      (control) =>
        (control.labels?.length ?? 0) === 0 &&
        (control.getAttribute('aria-label') ?? '') === '' &&
        (control.getAttribute('aria-labelledby') ?? '') === '' &&
        (control.getAttribute('title') ?? '') === '',
    )
    .map((control) => control.outerHTML.slice(0, 120));
}
