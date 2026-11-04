import { readdirSync, readFileSync } from 'node:fs';
import { basename, join } from 'node:path';
import { describe, expect, it } from 'vitest';
import ts from 'typescript';

function currencyLiterals(file: string, source: string): string[] {
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const problems: string[] = [];
  function visit(node: ts.Node) {
    if (
      (ts.isStringLiteralLike(node) ||
        ts.isTemplateHead(node) ||
        ts.isTemplateMiddle(node) ||
        ts.isTemplateTail(node) ||
        ts.isJsxText(node)) &&
      /\b(EUR|RUB|USD)\b/.test(node.text)
    ) {
      const { line } = tree.getLineAndCharacterOfPosition(node.getStart(tree));
      problems.push(`${file}:${line + 1}: currency literal`);
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  return problems;
}

/** Template literals that end in "${n} <plural noun>": a count must go through plural(). */
function pluralisations(file: string, source: string): string[] {
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const problems: string[] = [];
  function visit(node: ts.Node) {
    if (
      (ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) &&
      /^ [a-z]+(?:s|ies)$/i.test(node.text) &&
      ts.isTemplateSpan(node.parent) &&
      /^(?:n|count|total|-?days|formatInt\(.*\)|.*\.(?:length|total|size))$/.test(
        node.parent.expression.getText(tree),
      )
    ) {
      const { line } = tree.getLineAndCharacterOfPosition(node.getStart(tree));
      problems.push(`${file}:${line + 1}: use plural() for a count`);
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  return problems;
}

function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    return entry.isDirectory() ? files(path) : [path];
  });
}

function checkStyles(file: string, source: string): string[] {
  return source.split('\n').flatMap((line, index) => {
    const problems: string[] = [];
    if (
      basename(file) !== 'tokens.css' &&
      /#[\da-f]{3,8}\b|\brgba?\s*\(|\bhsla?\s*\(/i.test(line)
    ) {
      problems.push('colour literal');
    }
    if (
      basename(file) !== 'legacy-tokens.css' &&
      /\bfont(?:-size)?\s*:[^;{}]*\b\d+(?:\.\d+)?px/.test(line)
    ) {
      problems.push('px font size');
    }
    if (/font-weight\s*:\s*700\b/.test(line)) problems.push('weight 700');
    return problems.map((problem) => `${file}:${index + 1}: ${problem}`);
  });
}

const src = join(process.cwd(), 'src');

describe('Ledger style guard', () => {
  it('detects currency literals in strings, templates and JSX text without matching identifiers or comments', () => {
    expect(
      currencyLiterals(
        'fixture.tsx',
        "// USD\nconst USD = account.currency;\nconst a = 'Balance EUR';\nconst b = `USD ${amount}`;\nconst c = <span>RUB</span>;",
      ),
    ).toEqual([
      'fixture.tsx:3: currency literal',
      'fixture.tsx:4: currency literal',
      'fixture.tsx:5: currency literal',
    ]);
    expect(
      currencyLiterals('fixture.tsx', "const currency = account.currency; const a = 'USDT';"),
    ).toEqual([]);
  });

  it('detects a pluralised count that does not use plural()', () => {
    expect(
      pluralisations(
        'f.tsx',
        'const a = `${n} products`; const b = `${n} days ago`; const c = `${x.length} jobs`; const d = `No ${word} accounts`;',
      ),
    ).toEqual(['f.tsx:1: use plural() for a count', 'f.tsx:1: use plural() for a count']);
  });

  it('pluralises every count with plural()', () => {
    expect(
      files(src)
        .filter(
          (file) => /\.tsx?$/.test(file) && !file.includes('.test.') && !file.includes('generated'),
        )
        .flatMap((file) => pluralisations(file, readFileSync(file, 'utf8'))),
    ).toEqual([]);
  });

  it('keeps currency literals out of production components except UI kit samples', () => {
    expect(
      files(src)
        .filter(
          (file) =>
            file.endsWith('.tsx') && !file.includes('.test.') && basename(file) !== 'UiKit.tsx',
        )
        .flatMap((file) => currencyLiterals(file, readFileSync(file, 'utf8'))),
    ).toEqual([]);
  });

  it('migrates every legacy note caller to the Ledger Note component', () => {
    expect(
      files(src)
        .filter((file) => /\.(css|tsx)$/.test(file) && !file.includes('.test.'))
        .flatMap((file) =>
          /bss-(warning|hint|inline-error)\b/.test(readFileSync(file, 'utf8')) ? [file] : [],
        ),
    ).toEqual([]);
  });
  it('reports literal colours, px fonts and weight 700 with file and line', () => {
    expect(
      checkStyles(
        'fixture.css',
        'a {\n color: #fff;\n font-size: 13px;\n font: 400 12px/16px serif;\n font-weight: 700;\n}',
      ),
    ).toEqual([
      'fixture.css:2: colour literal',
      'fixture.css:3: px font size',
      'fixture.css:4: px font size',
      'fixture.css:5: weight 700',
    ]);
    expect(
      checkStyles('fixture.css', 'a { color: rgb(1, 2, 3); }\na { color: hsl(0, 0%, 0%); }'),
    ).toEqual(['fixture.css:1: colour literal', 'fixture.css:2: colour literal']);
    expect(
      checkStyles('tokens.css', ':root { --color-bg: #fff; --type-body: 400 13px/20px serif; }'),
    ).toEqual([]);
    expect(checkStyles('legacy-tokens.css', ':root { --fs-13: 13px; }')).toEqual([]);
  });

  it('all source CSS follows the style rules', () => {
    expect(
      files(src)
        .filter((file) => file.endsWith('.css'))
        .flatMap((file) => checkStyles(file, readFileSync(file, 'utf8'))),
    ).toEqual([]);
  });

  it('removes legacy status and destructive-control compatibility from production source', () => {
    const legacyUses = files(src)
      .filter((file) => /\.(css|tsx)$/.test(file) && !file.includes('.test.'))
      .flatMap((file) =>
        readFileSync(file, 'utf8')
          .split('\n')
          .flatMap((line, index) =>
            /\b(?:Pill|toneOf|TONES)\b|danger-outline|--st-/.test(line)
              ? [`${file}:${index + 1}: ${line.trim()}`]
              : [],
          ),
      );
    expect(legacyUses).toEqual([]);
  });

  it('defines an alias for every legacy variable still used by CSS and components', () => {
    const tokens = readFileSync(join(src, 'ui/tokens.css'), 'utf8');
    const legacy = readFileSync(join(src, 'ui/legacy-tokens.css'), 'utf8');
    const definitions = (text: string) =>
      new Set(Array.from(text.matchAll(/(--[\w-]+)\s*:/g), (match) => match[1]));
    const modern = definitions(tokens);
    const aliases = definitions(legacy);
    const local = definitions(
      files(src)
        .filter((file) => file.endsWith('.css') && !file.endsWith('legacy-tokens.css'))
        .map((file) => readFileSync(file, 'utf8'))
        .join('\n'),
    );
    const missing = files(src)
      .filter((file) => /\.(css|tsx)$/.test(file))
      .flatMap((file) =>
        Array.from(
          readFileSync(file, 'utf8').matchAll(/var\((--[\w-]+)/g),
          (match) => match[1] ?? '',
        )
          .filter((name) => !modern.has(name) && !local.has(name) && !aliases.has(name))
          .map((name) => `${file}: ${name}`),
      );
    expect(missing).toEqual([]);
    expect(aliases.has('--ink')).toBe(true);
    expect(aliases.has('--fs-125')).toBe(true);
  });
});
