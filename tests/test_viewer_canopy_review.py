import json
from pathlib import Path
import subprocess


def test_marker_layer_uses_page_lexical_state_and_ground_without_mutation():
    module = (Path(__file__).parents[1] / 'src/dynacity/viewer_canopy_review.js').read_text(encoding='utf-8')
    # Browser consts are lexical declarations, not properties on window.
    script = '''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = vm.createContext({assert});
vm.runInContext(`
 const ui = {canopyReview:false};
 const DATA = {canopy_review:{candidates:[
   {id:'a',position:[35.5,33.8],height_m:16,review_status:'pending',conflicts:[]},
   {id:'b',position:[35.51,33.81],height_m:6,review_status:'held',conflicts:[{kind:'pedestrian',source_id:'way/1'}]}
 ]}};
 const groundZ = (lon,lat) => lon+lat;
 const deck = {ScatterplotLayer:class {constructor(props){this.props=props}}};
 const before=JSON.stringify(DATA);
` + JSON.parse(process.argv[1]) + `
 assert.equal(canopyReviewLayers().length,0);
 ui.canopyReview=true;
 const p=canopyReviewLayers()[0].props;
 assert.equal(p.getRadius,5);assert.equal(p.filled,false);assert.equal(p.pickable,true);
 assert.equal(p.data.length,2);assert.equal(p.data[0].canopyCandidate,true);
 assert.equal(p.data[1].review_status,'held');
 assert.deepEqual(p.getLineColor(p.data[1]),[238,62,65]);
 assert.deepEqual(p.getLineColor(p.data[0]),[255,179,0]);
 assert.deepEqual(p.getPosition(p.data[0]),[35.5,33.8,35.5+33.8+0.35]);
 assert.equal(p.parameters.depthCompare,'less-equal');
 assert.equal(JSON.stringify(DATA),before);
 DATA.canopy_review=null;assert.equal(canopyReviewLayers().length,0);
`,context);
'''
    result = subprocess.run(['node', '-e', script, json.dumps(module)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_review_controls_toggle_select_focus_and_escape_source_text():
    template = (Path(__file__).parents[1] / 'src/dynacity/viewer_template.html').read_text(encoding='utf-8')
    controls = template.split('function setCanopyReview(on)', 1)[1].split('function renderCorridorPanel()', 1)[0]
    controls = 'function setCanopyReview(on)' + controls
    script = '''
const assert=require('node:assert/strict'),vm=require('node:vm');
const el=()=>({hidden:true,innerHTML:'',value:'',attrs:{},focused:false,
 setAttribute(k,v){this.attrs[k]=v},focus(){this.focused=true},
 closest(){return {open:false,querySelector(){return $('more-tools-summary')}}}});
const nodes=new Map();const $=(id)=>{if(!nodes.has(id))nodes.set(id,el());return nodes.get(id)};
const ui={canopyReview:false,canopyCandidate:null,selected:'existing-building'};
const DATA={canopy_review:{candidates:[
 {id:'a<script>',position:[35.5,33.8],height_m:16,review_status:'pending',conflicts:[]},
 {id:'b',position:[35.6,33.9],height_m:6,review_status:'held',conflicts:[{kind:'pedestrian',source_id:'way/1'}]}
],counts:{raw:2,pending:1,held:1},source:{name:'Meta and WRI',license:'CC BY 4.0',capture_date:null},limits:['Unknown ground']}};
let renders=0,view=null;const map={easeTo(v){view=v}};
const esc=(s)=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const context=vm.createContext({ui,DATA,$,map,esc,calm:{matches:true},render(){renders++}});
vm.runInContext(JSON.parse(process.argv[1]),context);
vm.runInContext('setCanopyReview(true)',context);
assert.equal(ui.canopyReview,true);assert.equal($('p-canopy').hidden,false);
assert.equal($('t-canopy').attrs['aria-pressed'],'true');
assert.ok($('p-canopy').innerHTML.includes('Capture date unknown'));
assert.ok($('p-canopy').innerHTML.includes('a&lt;script&gt;'));
assert.ok(!$('p-canopy').innerHTML.includes('a<script>'));
$('canopy-candidate').onchange({target:{value:'b'}});
assert.equal(ui.canopyCandidate,'b');assert.ok($('canopy-detail').innerHTML.includes('way/1'));
$('canopy-focus').onclick();assert.deepEqual(Array.from(view.center),[35.6,33.9]);assert.equal(view.duration,0);
$('canopy-close').onclick();assert.equal(ui.canopyReview,false);
assert.equal($('p-canopy').hidden,true);assert.equal($('tip').hidden,true);
assert.equal($('more-tools-summary').focused,true);assert.equal(ui.selected,'existing-building');
assert.equal(renders,2);
'''
    result = subprocess.run(['node', '-e', script, json.dumps(controls)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
