import qrcode from 'qrcode-generator';

/** A QR code drawn as one SVG path, black on white (a scanner needs the contrast in both
 * themes). Nothing is sent anywhere: the code is encoded in the browser. */
export function QrCode({ text, label }: { text: string; label: string }) {
  const code = qrcode(0, 'M');
  code.addData(text);
  code.make();
  const size = code.getModuleCount();
  const quiet = 4;
  let path = '';
  for (let row = 0; row < size; row += 1)
    for (let col = 0; col < size; col += 1)
      if (code.isDark(row, col)) path += `M${col + quiet} ${row + quiet}h1v1h-1z`;
  const side = size + quiet * 2;
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${side} ${side}`}
      width={192}
      height={192}
      shapeRendering="crispEdges"
      data-ui="ui.sims.qr"
    >
      <rect width={side} height={side} fill="#fff" />
      <path d={path} fill="#000" />
    </svg>
  );
}
