// Lists the UI's data-ui markers for the progress tracker (docs/progress/scope.yaml, make
// progress): every literal `data-ui="..."` JSX attribute, and every literal `ui="..."`
// prop of <Dialog> (the kit renders it as data-ui), in src/ outside tests. Parsed with the
// TypeScript compiler API, not a text search, so comments and strings never count.
// Prints JSON to stdout: [{ "marker", "file", "line" }], sorted.
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import ts from 'typescript';

const ROOT = new URL('..', import.meta.url).pathname;
const SRC = join(ROOT, 'src');

function* sources(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== 'generated') yield* sources(path);
    } else if (entry.name.endsWith('.tsx') && !entry.name.endsWith('.test.tsx')) {
      yield path;
    }
  }
}

function literal(attribute) {
  const init = attribute.initializer;
  return init && ts.isStringLiteral(init) ? init.text : null;
}

const markers = [];
for (const file of sources(SRC)) {
  const text = readFileSync(file, 'utf8');
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const visit = (node) => {
    if (ts.isJsxAttribute(node)) {
      const name = node.name.getText(source);
      const tag = node.parent.parent.tagName?.getText(source);
      const value =
        name === 'data-ui' || (name === 'ui' && tag === 'Dialog') ? literal(node) : null;
      if (value !== null) {
        const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
        markers.push({ marker: value, file: relative(ROOT, file), line: line + 1 });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
}
markers.sort((a, b) => a.marker.localeCompare(b.marker) || a.file.localeCompare(b.file));
process.stdout.write(JSON.stringify(markers, null, 2) + '\n');
