import base64

import numpy as np
from shapely.geometry import box

from dynacity.engine_city_clip import boundary_mask


def test_mask_marks_cells_inside_the_boundary_in_unreal_frame():
    # 100 m square north-east of the origin: Unreal X east, Y south, centimetres.
    mask = boundary_mask(box(1000, 2000, 1100, 2100), [1000, 2000, 0], cell_m=10)
    bits = np.unpackbits(np.frombuffer(base64.b64decode(mask['mask_b64']), np.uint8))[:mask['width']*mask['height']]
    grid = bits.reshape(mask['height'], mask['width'])
    def inside(x_cm, y_cm):
        return bool(grid[int((y_cm-mask['y0'])//mask['cell_cm']), int((x_cm-mask['x0'])//mask['cell_cm'])])
    assert inside(5000, -5000)        # 50 m east, 50 m north
    assert not inside(-500, -5000)    # 5 m west of the square is outside
    assert grid.sum() == 100          # ten 10 m cells by ten
