"""Capture a native viewport at a reproducible world-space camera."""
import argparse
import base64
from pathlib import Path
from unreal_tool_session import UnrealToolSession

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--location', nargs=3, type=float, required=True)
    parser.add_argument('--rotation', nargs=3, type=float, default=[0, 0, 0])
    parser.add_argument('--set-only', action='store_true')
    args = parser.parse_args()
    view = {'location': dict(zip(('x','y','z'), args.location)),
            'rotation': dict(zip(('pitch','yaw','roll'), args.rotation)),
            'scale': {'x':1,'y':1,'z':1}}
    session = UnrealToolSession()
    group = 'EditorToolset.EditorAppToolset'
    if args.set_only:
        print(session.call(group, 'SetCameraTransform', {'transform':view}))
    else:
        result = session.call(group, 'CaptureViewport', {'captureTransform':view,
                                                        'annotations':None,'bShowUI':False})
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(result['image']['data']))
        print(path)
