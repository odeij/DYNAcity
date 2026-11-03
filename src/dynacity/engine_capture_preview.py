"""Bounded-memory LAS 1.2 viewing subsets; source XYZ/RGB records remain exact."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import struct
import numpy as np
from .las import LASHeader
from .engine_capture_inventory import is_resident


def prepare_preview(source,output,max_points=2_000_000):
    source=Path(source).resolve();output=Path(output).resolve()
    if output.exists() or source==output:raise FileExistsError(output)
    if max_points<1:raise ValueError('max_points must be positive')
    if not is_resident(source):raise ValueError('Source must already be downloaded locally')
    header=LASHeader.read(source)
    if header.version!='1.2' or header.point_format not in (2,3):raise ValueError('Expected uncompressed RGB LAS 1.2 format 2/3')
    if not 227<=header.point_data_offset<=8*1024*1024:raise ValueError('Unsupported LAS metadata size')
    with source.open('rb') as stream:prefix=bytearray(stream.read(header.point_data_offset))
    if prefix[104]&0xc0:raise ValueError('Compressed records require a LAZ decoder')
    expected=header.point_data_offset+header.point_count*header.point_record_length
    if source.stat().st_size<expected:raise ValueError('Truncated source point records')
    stride=max(1,math.ceil(header.point_count/max_points));count=math.ceil(header.point_count/stride)
    raw=np.memmap(source,mode='r',dtype=np.uint8,offset=header.point_data_offset,
                  shape=(header.point_count,header.point_record_length))
    subset=raw[::stride];low=np.full(3,np.inf);high=np.full(3,-np.inf);returns=np.zeros(5,dtype=np.uint64)
    output.parent.mkdir(parents=True,exist_ok=True);temporary=output.with_suffix('.las.partial')
    with temporary.open('wb') as stream:
        stream.write(prefix)
        for start in range(0,count,100_000):
            block=np.ascontiguousarray(subset[start:start+100_000]);stream.write(block.tobytes())
            for axis in range(3):
                encoded=np.ndarray((len(block),),dtype='<i4',buffer=block,offset=axis*4,strides=(header.point_record_length,))
                values=encoded.astype(float)*header.scale[axis]+header.offset[axis]
                low[axis]=min(low[axis],values.min());high[axis]=max(high[axis],values.max())
            numbers=block[:,14]&7
            returns+=np.array([(numbers==i).sum() for i in range(1,6)],dtype=np.uint64)
        struct.pack_into('<I',prefix,107,count)
        struct.pack_into('<5I',prefix,111,*map(int,returns))
        struct.pack_into('<6d',prefix,179,high[0],low[0],high[1],low[1],high[2],low[2])
        prefix[58:90]=b'DynaCITY sampled viewing subset'.ljust(32,b'\0')
        stream.seek(0);stream.write(prefix)
    temporary.replace(output)
    result={'schema':'dynacity-capture-preview/1','source':str(source),'source_points':header.point_count,
            'source_bytes':source.stat().st_size,'source_mtime_ns':source.stat().st_mtime_ns,
            'output':str(output),'display_points':count,'stride':stride,
            'method':'Every N-th original point record; no generated positions or colours',
            'source_unchanged':True,'acquisition_date':None,'vertical_datum':'unverified'}
    output.with_suffix('.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True);parser.add_argument('--output',required=True);parser.add_argument('--max-points',type=int,default=2_000_000)
    args=parser.parse_args();print(json.dumps(prepare_preview(args.source,args.output,args.max_points)))


if __name__=='__main__':main()
