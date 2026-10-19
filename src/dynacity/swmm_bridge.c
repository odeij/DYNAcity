/* Read-only volume accessors for the pinned, unmodified EPA SWMM 5.2.4 core.
 * Build beside that exact source tree; no hydraulic equations are changed.
 * SWMM internal volumes are ft3; UCF(VOLUME) converts to the model's SI units.
 */
#include "headers.h"
#include "consts.h"
#if VERSION != 52004
#error This bridge requires the EPA SWMM 5.2.4 source
#endif
extern TRoutingTotals FlowTotals;
extern TNodeStats *NodeStats;
extern double massbal_getStorage(char isFinalStorage);

int dynacity_bridge_version(void) { return 1; }
double dynacity_storage(void) { return massbal_getStorage(FALSE) * UCF(VOLUME); }
double dynacity_external(void) { return FlowTotals.exInflow * UCF(VOLUME); }
double dynacity_outflow(void) { return FlowTotals.outflow * UCF(VOLUME); }
double dynacity_losses(void) { return (FlowTotals.evapLoss + FlowTotals.seepLoss) * UCF(VOLUME); }
double dynacity_flooding(void) { return FlowTotals.flooding * UCF(VOLUME); }
double dynacity_node_flooding(int index) {
    if (!NodeStats || index < 0 || index >= Nobjects[NODE]) return -1;
    return NodeStats[index].volFlooded * UCF(VOLUME);
}
