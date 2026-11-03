"""Execute a saved Python file through the observed Unreal command field."""
import argparse
from pathlib import Path
from unreal_tool_session import UnrealToolSession

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('path')
    p.add_argument('--ref', default='tb1')
    args = p.parse_args()
    path = Path(args.path).resolve()
    if not path.is_file() or path.suffix != '.py' or '"' in str(path):
        raise ValueError('Expected an existing Python source file')
    session = UnrealToolSession()
    print(session.call('SlateInspectorToolset.SlateInspectorToolset', 'Type',
                       {'ref': args.ref, 'text': 'py "' + path.as_posix() + '"', 'submit': True}))
