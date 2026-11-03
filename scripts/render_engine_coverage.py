"""Render the private district/capture manifests as an exportable coverage map."""
import argparse
import json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from pyproj import Transformer
from shapely.geometry import shape,box
from shapely.ops import transform,unary_union


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--districts',required=True);parser.add_argument('--captures',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    districts=json.loads(Path(args.districts).read_text());captures=json.loads(Path(args.captures).read_text())
    project=Transformer.from_crs(4326,32636,always_xy=True).transform
    features=districts['groups']['features'];polygons=[transform(project,shape(f['geometry'])) for f in features]
    west,south,east,north=unary_union(polygons).bounds
    margin=200;west-=margin;east+=margin;south-=margin;north+=margin
    width,height=1500,1080;left,top,map_width,map_height=55,165,930,800
    scale=min(map_width/(east-west),map_height/(north-south))
    def xy(x,y):return left+(x-west)*scale,top+(north-y)*scale
    image=Image.new('RGB',(width,height),'#10242b');draw=ImageDraw.Draw(image)
    def font(size,bold=False):
        return ImageFont.truetype('C:/Windows/Fonts/segoeuib.ttf' if bold else 'C:/Windows/Fonts/segoeui.ttf',size)
    draw.text((55,35),'BEIRUT  /  CITY FOUNDATION',font=font(16,True),fill='#75c7ba')
    draw.text((55,65),'Named areas. Visible coverage. One connected city.',font=font(34,True),fill='#f1f5ef')
    draw.text((55,118),'Production plan from source sector polygons and local capture headers · 25 October 2026',font=font(18),fill='#afc3c6')
    for polygon in polygons:
        parts=list(polygon.geoms) if hasattr(polygon,'geoms') else [polygon]
        for part in parts:
            if part.geom_type!='Polygon':continue
            draw.polygon([xy(x,y) for x,y in part.exterior.coords],fill='#28454c',outline='#92aaa9',width=2)
            for ring in part.interiors:draw.polygon([xy(x,y) for x,y in ring.coords],fill='#10242b')
    for feature in captures['footprints']['features']:
        polygon=transform(project,shape(feature['geometry']))
        draw.line([xy(x,y) for x,y in polygon.exterior.coords],fill='#8fe077',width=4)
    cx,cy=Transformer.from_crs(4326,32636,always_xy=True).transform(35.49525,33.87908)
    pilot=box(cx-350,cy-350,cx+350,cy+350)
    draw.line([xy(x,y) for x,y in pilot.exterior.coords],fill='#ffbc6e',width=4)
    for index,polygon in enumerate(polygons,1):
        p=polygon.representative_point();x,y=xy(p.x,p.y)
        draw.ellipse((x-15,y-15,x+15,y+15),fill='#e8f0eb')
        draw.text((x,y),str(index),font=font(15,True),fill='#183239',anchor='mm')
    x,y=xy(west+130,north-130)
    draw.line((x,y+45,x,y),fill='#d5e4df',width=3);draw.polygon([(x,y-5),(x-5,y+7),(x+5,y+7)],fill='#d5e4df')
    draw.text((x,y-15),'N',font=font(16,True),fill='#d5e4df',anchor='ms')
    x,y=xy(west+130,south+130)
    draw.line((x,y,x+500*scale,y),fill='#d5e4df',width=4)
    draw.text((x,y+8),'500 m',font=font(15),fill='#afc3c6')
    panel_x=1050
    draw.text((panel_x,165),'12 production areas',font=font(25,True),fill='#f1f5ef')
    draw.text((panel_x,205),'Source labels retained',font=font(16),fill='#afc3c6')
    for i,feature in enumerate(features,1):
        y=248+(i-1)*43;p=feature['properties']
        draw.text((panel_x,y),f'{i:02}',font=font(17,True),fill='#75c7ba')
        draw.text((panel_x+38,y),p['name'],font=font(18),fill='#e8f0eb')
        draw.text((1445,y),f"{p['building_count']:,}",font=font(17),fill='#afc3c6',anchor='ra')
    y=800
    draw.line((panel_x,y,1445,y),fill='#496167',width=1)
    draw.rectangle((panel_x,y+25,panel_x+17,y+42),outline='#8fe077',width=3)
    draw.text((panel_x+29,y+20),'3 resident cloud bounds',font=font(18),fill='#e8f0eb')
    draw.rectangle((panel_x,y+63,panel_x+17,y+80),outline='#ffbc6e',width=3)
    draw.text((panel_x+29,y+58),'Current Unreal prototype',font=font(18),fill='#e8f0eb')
    draw.text((panel_x,y+100),'149 buildings remain unassigned.',font=font(17),fill='#ffbc6e')
    draw.text((55,984),'17,569 source buildings  ·  10,817 roads  ·  367 roads cross area boundaries',font=font(20,True),fill='#f1f5ef')
    draw.text((55,1020),'Coverage bounds do not guarantee dense capture. Alignment and acquisition dates require review.',font=font(16),fill='#afc3c6')
    draw.text((55,1045),'Draft Achrafiye/Achrafieh alias merges two source spellings. Production groups are not verified legal boundaries.',font=font(15),fill='#afc3c6')
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True);image.save(output)
    print(output)


if __name__=='__main__':main()
