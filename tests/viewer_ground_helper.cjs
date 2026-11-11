// Tests that load slices of viewer_template.html into a vm context need the page's ground helpers too
// (`groundZ`, `groundPath`, `onGround`, ...). Requiring this file adds the template's "ground" block to
// every vm context before its first script runs, with no terrain grid loaded: the flat plane, z = 0.
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`, 'utf8');
const block = html.slice(html.indexOf('// ---- ground ----'), html.indexOf('let deckgl = null'));
const runInContext = vm.runInContext, runInNewContext = vm.runInNewContext, seen = new WeakSet();
function withGround(context) {
  if (context && typeof context === 'object' && !seen.has(context)) {
    seen.add(context);
    if (!('groundZ' in context)) runInNewContext(block, context);
  }
  return context;
}
vm.runInContext = (code, context, ...rest) => runInContext(code, withGround(context), ...rest);
vm.runInNewContext = (code, context, ...rest) => runInNewContext(code, context === undefined ? undefined : withGround(context), ...rest);
module.exports = { block, withGround };
