"""Optional GraphSAGE research model without a torch-geometric dependency."""

from __future__ import annotations

try:
    import torch
    from torch import nn
except ImportError:  # pragma: no cover - exercised only with the graph extra
    torch = None
    nn = None


if nn is not None:

    class MeanGraphSAGELayer(nn.Module):
        def __init__(self, input_dim: int, output_dim: int):
            super().__init__()
            self.linear = nn.Linear(input_dim * 2, output_dim)

        def forward(self, features, edge_index):
            source, target = edge_index
            aggregate = torch.zeros_like(features)
            aggregate.index_add_(0, target, features[source])
            degree = torch.zeros(
                features.shape[0], device=features.device, dtype=features.dtype
            )
            degree.index_add_(0, target, torch.ones_like(target, dtype=features.dtype))
            neighbor_mean = aggregate / degree.clamp_min(1.0).unsqueeze(1)
            return self.linear(torch.cat([features, neighbor_mean], dim=1))


    class GraphSAGETransitionClassifier(nn.Module):
        """Two-layer graph transition head used only after baseline validation."""

        def __init__(
            self,
            input_dim: int,
            hidden_dim: int,
            class_count: int,
            dropout: float = 0.2,
        ):
            super().__init__()
            self.first = MeanGraphSAGELayer(input_dim, hidden_dim)
            self.second = MeanGraphSAGELayer(hidden_dim, hidden_dim)
            self.classifier = nn.Linear(hidden_dim, class_count)
            self.dropout = nn.Dropout(dropout)

        def forward(self, features, edge_index):
            hidden = torch.relu(self.first(features, edge_index))
            hidden = self.dropout(hidden)
            hidden = torch.relu(self.second(hidden, edge_index))
            return self.classifier(hidden)


def require_torch() -> None:
    if torch is None:
        raise RuntimeError("GraphSAGE requires installation with: pip install -e .[graph]")

