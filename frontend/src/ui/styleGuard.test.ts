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

function undefinedVariables(
  file: string,
  source: string,
  tokens: Set<string>,
  runtime: Set<string>,
): string[] {
  return source.split('\n').flatMap((line, index) =>
    Array.from(line.matchAll(/var\((--[\w-]+)/g), (match) => match[1] ?? '')
      .filter((name) => !tokens.has(name) && !runtime.has(name))
      .map((name) => `${file}:${index + 1}: ${name}`),
  );
}

function inlineStyles(file: string, source: string): string[] {
  const problems: string[] = [];
  for (const match of source.matchAll(/style=\{\{(?!\s*['"]--)/g)) {
    const line = source.slice(0, match.index).split('\n').length;
    problems.push(`${file}:${line}: inline style`);
  }
  return problems;
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

  it('uses only custom properties that tokens.css or a runtime style prop defines (d)', () => {
    expect(
      undefinedVariables(
        'fixture.css',
        'a { color: var(--ink); width: var(--progress); gap: var(--space-sm); }',
        new Set(['--space-sm']),
        new Set(['--progress']),
      ),
    ).toEqual(['fixture.css:1: --ink']);
    const tokens = readFileSync(join(src, 'ui/tokens.css'), 'utf8');
    const defined = new Set(
      Array.from(tokens.matchAll(/(--[\w-]+)\s*:/g), (match) => match[1] ?? ''),
    );
    const runtime = new Set(
      files(src)
        .filter((file) => /\.(css|tsx)$/.test(file) && !file.includes('.test.'))
        .flatMap((file) =>
          Array.from(
            readFileSync(file, 'utf8').matchAll(/['"](--[\w-]+)['"]\s*:|^\s*(--[\w-]+)\s*:/gm),
            (match) => match[1] ?? match[2] ?? '',
          ),
        ),
    );
    expect(
      files(src)
        .filter((file) => /\.(css|tsx)$/.test(file) && !file.includes('.test.'))
        .flatMap((file) => undefinedVariables(file, readFileSync(file, 'utf8'), defined, runtime)),
    ).toEqual([]);
  });

  it('keeps inline styles to runtime custom properties (e)', () => {
    expect(
      inlineStyles(
        'fixture.tsx',
        "<a style={{ color: 'red' }} />\n<b style={{ '--progress': '4%' }} />\n<c style={{\n '--x': 1 }} />",
      ),
    ).toEqual(['fixture.tsx:1: inline style']);
    expect(
      files(src)
        .filter((file) => file.endsWith('.tsx') && !file.includes('.test.'))
        .flatMap((file) => inlineStyles(file, readFileSync(file, 'utf8'))),
    ).toEqual([]);
  });
});
