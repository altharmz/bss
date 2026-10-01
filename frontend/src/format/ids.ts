// Display ids. We have UUIDs and no subscriber numbers; the UI shows the first 8 hex
// digits, upper case (the full UUID goes in a tooltip). Collisions are possible in
// principle and irrelevant at demo scale; nothing is looked up by a short id.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function shortId(uuid: string): string {
  if (!UUID.test(uuid)) throw new RangeError(`not a UUID: ${uuid}`);
  return uuid.slice(0, 8).toUpperCase();
}

/** The billing account label of the design: "BA-1F3A9C2B". */
export function accountLabel(uuid: string): string {
  return `BA-${shortId(uuid)}`;
}

/** Search: a query matches when, spaces removed, it is a substring of the short id
 * (case-insensitive). An empty query matches nothing here; the caller decides. */
export function matchesShortId(query: string, uuid: string): boolean {
  const q = query.replace(/\s/g, '').toUpperCase();
  return q.length > 0 && shortId(uuid).includes(q);
}
