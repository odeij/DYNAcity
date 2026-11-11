"""Advance checkpointed city imports through the local Unreal MCP server.

Create/open the separate city world first. Imports run serially; a second
process must not operate the same editor import queue at the same time.
"""
import argparse
import json
import time
from pathlib import Path
from unreal_mcp_client import call


def advance(url):
    result=call(url,'tools/call',{'name':'call_tool','arguments':{
        'toolset_name':'city_tools.CityTools','tool_name':'advance','arguments':{}}},timeout=180)
    if result.get('error') or result.get('result',{}).get('isError'):
        raise RuntimeError(result)
    envelope=json.loads(result['result']['content'][0]['text'])
    return json.loads(envelope['returnValue'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8016/mcp')
    parser.add_argument('--output',required=True)
    parser.add_argument('--minutes',type=float,default=30)
    args=parser.parse_args();deadline=time.monotonic()+args.minutes*60
    previous=None;path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    while time.monotonic()<deadline:
        state=advance(args.url)
        path.write_text(json.dumps(state,indent=2),encoding='utf-8')
        progress=(len(state['completed']),state['pending']['key'] if state['pending'] else None)
        if progress!=previous:
            print(json.dumps({'completed':progress[0],'total':state['total_packages'],'pending':progress[1]}),flush=True)
            previous=progress
        if not state['pending'] and len(state['completed'])==state['total_packages']:return
        time.sleep(5)
    raise TimeoutError('Import deadline reached; progress remains checkpointed in the project')


if __name__=='__main__':main()
