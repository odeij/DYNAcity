"""Resume mapped cover imports through Unreal's local MCP, one package at a time."""
import argparse
import json
from pathlib import Path
import time
from unreal_mcp_client import call


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--url',default='http://127.0.0.1:8016/mcp')
    args=parser.parse_args()
    def run(name,arguments):
        result=call(args.url,'tools/call',{'name':'call_tool','arguments':{
            'toolset_name':'street_tools.StreetTools','tool_name':name,'arguments':arguments}})
        if result.get('error') or result.get('result',{}).get('isError'):raise RuntimeError(result)
        return json.loads(json.loads(result['result']['content'][0]['text'])['returnValue'])
    print(run('prepare_covers',{'filename':str(Path(args.manifest).resolve())}),flush=True)
    while True:
        result=run('advance_covers',{})
        if not result.get('busy'):
            print(result,flush=True)
            if result['completed']==result['total'] and result['pending'] is None:break
        time.sleep(1)


if __name__=='__main__':main()
