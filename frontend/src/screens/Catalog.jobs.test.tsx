// #498: the import and export jobs around a refusal, a closed dialog and a download.
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { json, tmfError, type Handler } from '../test/fetch';
import { CAT, backend, open } from '../test/catalogKit';

const JOBS = `GET ${CAT}/importJob?offset=0&limit=100`;
const EXPORTS = `GET ${CAT}/exportJob?offset=0&limit=100`;
const DOC = '{"@type":"BssCatalogDocument","version":1}';
const DOC_URL =
  'data:application/json;base64,eyJAdHlwZSI6IkJzc0NhdGFsb2dEb2N1bWVudCIsInZlcnNpb24iOjF9';
const IMPORT = {
  id: 'i1',
  status: 'Failed',
  creationDate: '2026-10-20T09:00:00Z',
  errorLog: 'category X: parents form a cycle',
};
const EXPORT = {
  id: 'e1',
  status: 'Succeeded',
  creationDate: '2026-10-21T09:00:00Z',
  url: DOC_URL,
};

function jobs(more: Record<string, Handler> = {}, exports: unknown[] = [EXPORT]) {
  return backend({
    [JOBS]: () => json([IMPORT], { total: 1 }),
    [EXPORTS]: () => json(exports, { total: exports.length }),
    [`GET ${CAT}/productCatalog?offset=0&limit=100`]: () => json([], { total: 0 }),
    ...more,
  });
}
async function openJobs(count: string) {
  await open();
  fireEvent.click(screen.getByRole('tab', { name: 'Import / export' }));
  await screen.findByText(count);
}
const reads = (calls: string[]) => calls.filter((c) => c === EXPORTS).length;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('catalog jobs around a write', () => {
  it('reads the list again when a dialog that created a job closes', async () => {
    const created = { ...EXPORT, id: 'e9', creationDate: '2026-10-25T09:00:00Z' };
    let exports: unknown[] = [EXPORT];
    const { calls } = jobs({
      [EXPORTS]: () => json(exports, { total: exports.length }),
      [`POST ${CAT}/exportJob`]: () => {
        exports = [created, EXPORT];
        return json(created, { status: 201 });
      },
    });
    await openJobs('2 jobs');
    // a dialog that created nothing closes without a read
    fireEvent.click(screen.getByRole('button', { name: 'Export...' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(reads(calls)).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'Export...' }));
    const dialog = screen.getByRole('dialog', { name: 'Export the catalog' });
    const run = within(dialog).getByRole('button', { name: 'Export' }) as HTMLButtonElement;
    await waitFor(() => expect(run.disabled).toBe(false));
    fireEvent.click(run);
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Close' }));
    expect(await screen.findByText('3 jobs')).toBeTruthy();
    expect(reads(calls)).toBe(2);
  });

  it('shows a refused export and keeps Export offered', async () => {
    jobs({ [`POST ${CAT}/exportJob`]: () => tmfError(503, 'export queue is full') });
    await openJobs('2 jobs');
    fireEvent.click(screen.getByRole('button', { name: 'Export...' }));
    const dialog = screen.getByRole('dialog', { name: 'Export the catalog' });
    const run = within(dialog).getByRole('button', { name: 'Export' }) as HTMLButtonElement;
    await waitFor(() => expect(run.disabled).toBe(false));
    fireEvent.click(run);
    expect(await within(dialog).findByText(/export queue is full/)).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Export' })).toBe(run);
    expect(run.disabled).toBe(false);
    expect(within(dialog).queryByRole('button', { name: 'Download' })).toBeNull();
  });

  it('says when a chosen file cannot be read and keeps the box empty', async () => {
    class BrokenReader {
      result: string | null = null;
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      readAsText() {
        queueMicrotask(() => this.onerror?.());
      }
    }
    vi.stubGlobal('FileReader', BrokenReader);
    jobs();
    await openJobs('2 jobs');
    fireEvent.click(screen.getByRole('button', { name: 'Import...' }));
    const dialog = screen.getByRole('dialog', { name: 'Import a catalog document' });
    fireEvent.change(within(dialog).getByLabelText('JSON file'), {
      target: { files: [new File([DOC], 'catalog.json')] },
    });
    expect(await within(dialog).findByText('The file could not be read')).toBeTruthy();
    expect(
      (within(dialog).getByLabelText(/paste the document/i) as HTMLTextAreaElement).value,
    ).toBe('');
    expect(
      (within(dialog).getByRole('button', { name: 'Import' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('shows a refused import and keeps the document to send again', async () => {
    jobs({ [`POST ${CAT}/importJob`]: () => tmfError(413, 'document too large for the server') });
    await openJobs('2 jobs');
    fireEvent.click(screen.getByRole('button', { name: 'Import...' }));
    const dialog = screen.getByRole('dialog', { name: 'Import a catalog document' });
    const box = within(dialog).getByLabelText(/paste the document/i) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: DOC } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Import' }));
    expect(await within(dialog).findByText(/document too large for the server/)).toBeTruthy();
    expect(box.value).toBe(DOC);
    expect(
      (within(dialog).getByRole('button', { name: 'Import' }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it('answers No to deleting a job, then shows a refused delete', async () => {
    const { fetchMock } = jobs({
      [`DELETE ${CAT}/importJob/i1`]: () => tmfError(404, 'importJob i1 not found'),
    });
    await openJobs('2 jobs');
    fireEvent.click(screen.getByText('category X: parents form a cycle'));
    const dialog = screen.getByRole('dialog', { name: 'Import job' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'No' }));
    expect(within(dialog).queryByText(/Nothing it imported is undone/)).toBeNull();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Yes' }));
    expect(await within(dialog).findByText(/importJob i1 not found/)).toBeTruthy();
    // the question is gone and the dialog stays with Delete offered again
    expect(within(dialog).queryByRole('button', { name: 'Yes' })).toBeNull();
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeTruthy();
    expect(screen.getByRole('dialog', { name: 'Import job' })).toBe(dialog);
  });

  it('downloads the document of a finished export from its job dialog', async () => {
    const blobs: Blob[] = [];
    const { createObjectURL, revokeObjectURL } = URL;
    URL.createObjectURL = (blob: Blob | MediaSource) => {
      blobs.push(blob as Blob);
      return 'blob:x';
    };
    URL.revokeObjectURL = () => undefined;
    const names: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      names.push(this.download);
    });
    try {
      jobs();
      await openJobs('2 jobs');
      const exportRow = screen
        .getAllByRole('row')
        .find((r) => within(r).queryAllByRole('cell')[0]?.textContent === 'Export') as HTMLElement;
      fireEvent.click(exportRow);
      const dialog = screen.getByRole('dialog', { name: 'Export job' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Download' }));
      expect(names).toEqual(['bss-catalog-2026-10-21.json']);
      expect(await blobs[0]?.text()).toBe(DOC);
    } finally {
      click.mockRestore();
      URL.createObjectURL = createObjectURL;
      URL.revokeObjectURL = revokeObjectURL;
    }
  });
});
