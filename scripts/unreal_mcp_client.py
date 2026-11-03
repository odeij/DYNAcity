"""Small local HTTP MCP client for the prototype's built-in Unreal server.

Sequential requests only: Unreal executes tool calls on its game thread.
This does not install a third-party server or modify global agent settings.
"""
import argparse
import json
from pathlib import Path
import urllib.request


def request(url,method,params,session=None,identifier=1,timeout=180):
    message={'jsonrpc':'2.0','method':method,'params':params}
    if identifier is not None:message['id']=identifier
    headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
    if session:headers['Mcp-Session-Id']=session
    req=urllib.request.Request(url,data=json.dumps(message).encode(),headers=headers)
    with urllib.request.urlopen(req,timeout=timeout) as response:
        session=response.headers.get('Mcp-Session-Id') or session
        if response.status in (202,204):return None,session
        if 'text/event-stream' in response.headers.get('Content-Type',''):
            for line in response:
                if line.startswith(b'data:'):
                    result=json.loads(line[5:])
                    if result.get('id')==identifier:return result,session
            raise RuntimeError('MCP stream ended without the requested response')
        return json.load(response),session


def call(url,method,params,timeout=180):
    initialized,session=request(url,'initialize',{'protocolVersion':'2025-03-26','capabilities':{},
        'clientInfo':{'name':'dynacity-prototype','version':'0.1'}},timeout=timeout)
    if 'error' in initialized:raise RuntimeError(initialized['error'])
    request(url,'notifications/initialized',{},session,identifier=None,timeout=timeout)
    result,_=request(url,method,params,session,identifier=2,timeout=timeout)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8016/mcp')
    parser.add_argument('--method',default='tools/list')
    parser.add_argument('--params-file')
    parser.add_argument('--output',required=True)
    parser.add_argument('--timeout',type=int,default=180)
    args=parser.parse_args()
    params=json.loads(Path(args.params_file).read_text(encoding='utf-8')) if args.params_file else {}
    result=call(args.url,args.method,params,args.timeout)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'error':result.get('error'),
                      'result_keys':list(result.get('result',{}))}))


if __name__=='__main__':main()
