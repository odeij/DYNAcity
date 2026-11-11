"""Independent byte fixtures and actual generated-JavaScript checks for asset preview."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess
import pytest

spec = importlib.util.spec_from_file_location('asset_preview_tested', Path(__file__).parents[1]/'scripts/build_corridor_asset_preview.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def glb(path, *, node=None, nonfinite=False):
    points=[(0.,0.,0.),(1.,0.,0.),(0.,1.,float('nan') if nonfinite else 0.)]
    binary=b''.join(struct.pack('<fff',*v) for v in points)+struct.pack('<HHH',0,1,2)+b'\0\0'
    doc={'asset':{'version':'2.0'},'scene':0,'scenes':[{'nodes':[0]}],
      'nodes':[dict({'mesh':0},**(node or {}))],
      'meshes':[{'primitives':[{'attributes':{'POSITION':0},'indices':1,'material':m} for m in [0,1]]}],
      'materials':[{'pbrMetallicRoughness':{'baseColorFactor':[.2,.3,.4,a]},'doubleSided':True} for a in [1,0]],
      'buffers':[{'byteLength':len(binary)}],
      'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':36},{'buffer':0,'byteOffset':36,'byteLength':6}],
      'accessors':[{'bufferView':0,'componentType':5126,'count':3,'type':'VEC3'},
                   {'bufferView':1,'componentType':5123,'count':3,'type':'SCALAR'}]}
    js=json.dumps(doc).encode();js+=b' '*((-len(js))%4)
    raw=struct.pack('<III',0x46546c67,2,12+8+len(js)+8+len(binary))+struct.pack('<I4s',len(js),b'JSON')+js+struct.pack('<I4s',len(binary),b'BIN\0')+binary
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);return path


@pytest.fixture
def bundle(tmp_path):
    root=tmp_path/'bundle';candidates=[]
    for key,uid in [('tower70','bde30f76-7868-4187-92a7-a18270c58815'),('granite91','106849cb-0a30-45f7-ba11-dc219083a1c1')]:
        p=glb(root/'assets'/f'{key}.glb')
        candidates.append({'key':key,'source_building_uuid':'beirut001:'+uid,'geometry_kind':'optional_additive',
          'gltf_bounds_yup':{'min':[0,0,0],'max':[1,1,0]},'local_enu_bounds':{'min':[0,0,0],'max':[1,0,1]},
          'glb_asset':{'path':f'assets/{key}.glb','sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size_bytes':p.stat().st_size},
          'material_parameters':[],'status':'ESTIMATED_OPTIONAL_NOT_SCENE_APPLIED'})
    manifest={'schema':'dynacity-corridor-appearance-bundle/1','timestamp':'fixture','origin_utm_m':[1,2,3],
              'coordinate_system':{'units':'metres'},'candidates':candidates,'status':'ESTIMATED_OPTIONAL_NOT_SCENE_APPLIED'}
    (root/'manifest.json').write_text(json.dumps(manifest));return root,manifest


def test_real_schema_generates_script_and_material_assignments(bundle,tmp_path):
    root,manifest=bundle;out=tmp_path/'out';result=preview.build_preview(root,out)
    assert result['assets_processed']==2 and result['total_triangles']==4
    html=(out/'index.html').read_text();data=json.loads(re.search(r'const ASSET_DATA = (.*);',html).group(1))
    assert [t['material'] for t in data[0]['triangles']]==[0,1]
    assert data[0]['materials'][1]['baseColor'][3]==0
    assert data[0]['triangles'][0]['v2']==[0,0,1]
    for asset in data:assert (out/asset['glb_file']).is_file()
    script=re.search(r'<script>(.*?)</script>',html,re.S).group(1);js=tmp_path/'preview.js';js.write_text(script)
    check=subprocess.run(['node','--check',str(js)],capture_output=True,text=True)
    assert check.returncode==0,check.stderr


def test_hash_mismatch_refused(bundle):
    root,m=bundle;m['candidates'][0]['glb_asset']['sha256']='0'*64
    with pytest.raises(ValueError,match='Hash mismatch'):preview.verify_glb_asset(m['candidates'][0],root)


def test_traversal_refused(bundle):
    root,m=bundle;m['candidates'][0]['glb_asset']['path']='../escape.glb'
    with pytest.raises(ValueError,match='escapes'):preview.verify_glb_asset(m['candidates'][0],root)


def test_output_overwrite_refused(bundle,tmp_path):
    root,m=bundle;out=tmp_path/'out';preview.build_preview(root,out)
    with pytest.raises(ValueError,match='refusing overwrite'):preview.build_preview(root,out)


def test_glb_truncated_header_refused(tmp_path):
    p=tmp_path/'bad.glb';p.write_bytes(b'bad')
    with pytest.raises(preview.GLBParseError):preview.GLBAsset('bad',p)


def test_nonfinite_vertices_refused(tmp_path):
    p=glb(tmp_path/'nan.glb',nonfinite=True)
    with pytest.raises(preview.GLBParseError,match='nonfinite'):preview.GLBAsset('nan',p)


def test_node_translation_bounds(tmp_path):
    p=glb(tmp_path/'translated.glb',node={'translation':[2,3,4]})
    a=preview.GLBAsset('translated',p);assert a.bounds_local==((2,3,4),(3,4,4))


def test_nested_nodes_explicitly_refused(tmp_path):
    p=glb(tmp_path/'nested.glb',node={'children':[0]})
    with pytest.raises(preview.GLBParseError,match='hierarchy'):preview.GLBAsset('nested',p)


def test_linear_gltf_color_is_encoded_for_canvas_srgb(bundle,tmp_path):
    root,_=bundle;out=tmp_path/'color';preview.build_preview(root,out)
    html=(out/'index.html').read_text()
    function=re.search(r'function rgbaColor\(mat\) \{.*?\n    \}',html,re.S).group(0)
    js=tmp_path/'color.js';js.write_text(function+'\nconsole.log(rgbaColor({baseColor:[0.18,0.18,0.18,0]}));')
    result=subprocess.run(['node',str(js)],capture_output=True,text=True,check=True)
    assert result.stdout.strip()=='rgba(118, 118, 118, 0)'


@pytest.fixture
def context_bundle(bundle,tmp_path):
    root,manifest=bundle
    manifest['horizontal_crs']='EPSG:32636'; manifest['coordinate_system']['horizontal_crs']='EPSG:32636'
    (root/'manifest.json').write_text(json.dumps(manifest))
    directory=tmp_path/'context';asset=glb(directory/'assets/context.glb')
    data={'schema':'dynacity-offline-corridor-context/1','status':'OFFLINE_CONTEXT_UNVERIFIED',
      'context_asset':{'path':'assets/context.glb','sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),'size_bytes':asset.stat().st_size},
      'source_building_ids':[c['source_building_uuid'] for c in manifest['candidates']],
      'gltf_bounds_yup':{'min':[0,0,0],'max':[1,1,0]},'local_enu_bounds':{'min':[0,0,0],'max':[1,0,1]},
      'origin_utm_m':manifest['origin_utm_m'],'horizontal_crs':'EPSG:32636','vertical_datum':'UNKNOWN',
      'gltf_axes':'East,Up,South','units':'metres','material_parameters':[],
      'native_snapshot_geometry_hash_prefix':'different','matches_native_snapshot':False,
      'source_geometry_license':'UNKNOWN - keep private; no external upload'}
    path=directory/'context.json';path.write_text(json.dumps(data));return root,path,data


def test_optional_context_build_has_real_markup_and_portable_provenance(context_bundle,tmp_path):
    root,path,data=context_bundle;out=tmp_path/'context-out';result=preview.build_preview(root,out,path)
    assert result['assets_processed']==3 and result['total_triangles']==6
    html=(out/'index.html').read_text()
    for name in ['canvas','coordDisplay','infoPanel','showGeometry','showContext','showAxes']:
        assert f'id="{name}"' in html
    assert 'DIFFERENT native geometry version' in html
    manifest=json.loads((out/'manifest.json').read_text());assert manifest['schema']=='dynacity-corridor-asset-preview/1'
    assert manifest['context']['matches_native_snapshot'] is False
    copied=json.loads((out/'context.json').read_text())
    assert (out/copied['context_asset']['path']).read_bytes()==(path.parent/data['context_asset']['path']).read_bytes()


@pytest.mark.parametrize('change', ['origin','hash','escape','duplicate','axes','native_claim'])
def test_context_guards_before_output_write(context_bundle,tmp_path,change):
    root,path,data=context_bundle
    if change=='origin':data['origin_utm_m']=[999,2,3]
    if change=='hash':data['context_asset']['sha256']='0'*64
    if change=='escape':data['context_asset']['path']='../escape.glb'
    if change=='duplicate':data['source_building_ids']*=2
    if change=='axes':data['gltf_axes']='East,North,Up'
    if change=='native_claim':data['matches_native_snapshot']=True
    path.write_text(json.dumps(data));out=tmp_path/'rejected'
    with pytest.raises((ValueError,FileNotFoundError)):preview.build_preview(root,out,path)
    assert not out.exists()


def test_local_js_unit_candidate_toggle_keeps_context(context_bundle,tmp_path):
    root,path,data=context_bundle;out=tmp_path/'render-unit';preview.build_preview(root,out,path)
    html=(out/'index.html').read_text();source=re.search(r'<script>(.*?)</script>',html,re.S).group(1)
    ids=re.findall(r'id="([^"]+)"',html)
    runner="""const vm=require('node:vm');
const source=SOURCE, ids=IDS;
const graphics={count:0,clearRect(){this.count=0},fillRect(){},beginPath(){},moveTo(){},lineTo(){},closePath(){},fill(){this.count++},stroke(){},fillText(){}};
const elements=Object.fromEntries(ids.map(id=>[id,{checked:true,addEventListener(){},classList:{toggle(){}},getBoundingClientRect(){return {left:0,top:0}}}]));
elements.showWireframe.checked=false; elements.showAxes.checked=false;
elements.canvas={...elements.canvas,width:1200,height:600,clientWidth:1200,clientHeight:600,parentElement:{},getContext:()=>graphics};
const document={getElementById:id=>elements[id],querySelectorAll:()=>[]};
const env={document,window:{addEventListener(){}},location:{search:''},URLSearchParams,setTimeout(){},ResizeObserver:class{observe(){}}};
vm.createContext(env);vm.runInContext(source,env);const counts=[graphics.count];
elements.showGeometry.checked=false;vm.runInContext('redraw()',env);counts.push(graphics.count);
elements.showContext.checked=false;vm.runInContext('redraw()',env);counts.push(graphics.count);
elements.showGeometry.checked=true;vm.runInContext('redraw()',env);counts.push(graphics.count);
console.log(JSON.stringify(counts));"""
    runner=runner.replace('SOURCE',json.dumps(source)).replace('IDS',json.dumps(ids))
    js=tmp_path/'local-render-unit.js';js.write_text(runner)
    result=subprocess.run(['node',str(js)],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)==[6,2,0,4]


def test_responsive_css_static_constraints(context_bundle,tmp_path):
    """Static layout regression: verify responsive CSS breakpoints and wrapping rules in generated HTML."""
    root,path,data=context_bundle;out=tmp_path/'layout-check';preview.build_preview(root,out,path)
    html=(out/'index.html').read_text()

    # CSS media query breakpoints present
    assert '@media (max-width: 1000px)' in html, 'Desktop->tablet breakpoint missing'
    assert '@media (max-width: 480px)' in html, 'Mobile breakpoint (390px class) missing'

    # Container padding reduced at narrow widths (mobile 12px)
    assert '.container { padding: 12px; }' in html

    # Critical wrapping/overflow controls
    assert 'word-break: break-word' in html or 'overflow-wrap: anywhere' in html
    assert 'word-break: break-all' in html

    # Flex wrap on controls
    assert 'flex-wrap: wrap' in html

    # Grid minmax(0, ...) to prevent overflow
    assert 'minmax(0, 1fr)' in html

    # min-width: 0 on grid children
    assert 'min-width: 0' in html

    # Viewport meta tag present
    assert 'name="viewport"' in html and 'width=device-width' in html

    # Flex shrink on axis colors
    assert 'flex-shrink: 0' in html or 'flex-shrink:0' in html.replace(' ', '')

    # Canvas sized to container (width: 100%)
    assert 'width: 100%' in html

    # Font sizes scale down at mobile
    assert 'font-size: 0.8rem' in html or 'font-size:0.8rem' in html.replace(' ', '')

    # Download link wrapping
    assert 'word-break: break-all' in html or 'overflow-wrap: anywhere' in html

    # Badge nowrap for short labels
    assert 'white-space: nowrap' in html or 'white-space:nowrap' in html.replace(' ', '')

    # Asset info grid collapses to single column at mobile
    assert 'grid-template-columns: 1fr' in html

    # Media queries placed after base rules (override ordering)
    media_pos = html.find('@media (max-width: 480px)')
    canvas_base = html.find('canvas { display: block;')
    assert media_pos > canvas_base, 'Media queries must follow base rules for override precedence'
