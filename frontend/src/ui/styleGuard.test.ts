import { readdirSync, readFileSync } from 'node:fs';
import { basename, join } from 'node:path';
import { describe, expect, it } from 'vitest';

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
