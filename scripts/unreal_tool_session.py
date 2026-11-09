"""One serialized local MCP session for a bounded series of editor operations."""
import json

from unreal_mcp_client import request


class UnrealToolSession:
    def __init__(self, url='http://127.0.0.1:8016/mcp'):
        self.url = url
        result, self.session = request(url,'initialize',{'protocolVersion':'2025-03-26',
            'capabilities':{},'clientInfo':{'name':'dynacity-street-finish','version':'1'}})
        if result.get('error'):
            raise RuntimeError(result)
        request(url,'notifications/initialized',{},self.session,identifier=None)
        self.identifier = 1

    def call(self, group, name, arguments):
        self.identifier += 1
        if not group.startswith(('editor_toolset.', 'EditorToolset.', 'SlateInspectorToolset.')):
            group = 'editor_toolset.toolsets.' + group
        response, _ = request(self.url,'tools/call',{'name':'call_tool','arguments':{
            'toolset_name':group,'tool_name':name,'arguments':arguments}},
            self.session,identifier=self.identifier,timeout=120)
        if response.get('error') or response.get('result',{}).get('isError'):
            raise RuntimeError(response)
        result = response['result']
        if 'structuredContent' in result:
            return result['structuredContent']['returnValue']
        for item in result.get('content',[]):
            if item['type'] == 'text':
                value = json.loads(item['text'])
                if 'returnValue' in value:
                    return value['returnValue']
        raise RuntimeError('Unrecognized editor tool response')
