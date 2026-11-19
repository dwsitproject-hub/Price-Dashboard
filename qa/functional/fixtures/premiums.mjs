// Synthetic "Premiums" tab of the Market Price Report (same layout as backend/tests/test_sheets.py::_premiums_grid).
export function premiumsGrid(days = 30) {
  const products = ['CPO', 'RPO', 'ROL', 'RPS', 'PFAD'];
  const prodRow = ['', ''];
  const head = ['', 'Date'];
  for (const p of products) {
    prodRow.push(p, p, ...Array(9).fill(''));
    head.push('Reuters Indo', 'Reuters Indo (Smooth)', `${p} Reuters Indo Premium`, 'Reuters Msia', 'R Msia (Smooth)',
      `${p} Reuters Msia Premium`, 'Tradable', `${p} Tradable (Smooth)`, `${p} Tradable Premium`, 'Levy + Duty',
      `${p} Local Tradable`);
  }
  const grid = [['', '', ...Array(head.length - 2).fill('CI')], prodRow, head];
  const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  for (let i = 0; i < days; i++) {
    const d = new Date(Date.UTC(2026, 0, 1 + i));
    const row = [String(739 + i), `${d.getUTCDate()} ${mon[d.getUTCMonth()]} ${d.getUTCFullYear()}`];
    products.forEach((_, j) => {
      const base = 1000 + 20 * j + i;
      const f = (v) => v.toLocaleString('en-US', { maximumFractionDigits: 0 });
      row.push(f(base), f(base), '', f(base + 10), f(base + 10), '', f(base + 30), f(base + 30), '', '257', '800');
    });
    grid.push(row);
  }
  return grid;
}
