"""Close the owned integration run before repairing its blocked surface."""
import verify_efasim_mobility
if verify_efasim_mobility._run is not None:
    verify_efasim_mobility._cleanup('Captured visual actor collision obstructs the source street; repairing before retry.')
