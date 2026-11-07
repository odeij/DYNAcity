#pragma once
#include "CoreMinimal.h"

struct FDynaMapSegment;

// One instruction along a planned route. Turn: -3 U-turn left, -2 sharp left, -1 left,
// 0 continue, 1 right, 2 sharp right, 3 U-turn right; Arrive marks the destination.
struct FDynaRouteManeuver
{
    double AlongM = 0;
    int32 Turn = 0;
    bool Arrive = false;
    FString Road;
};

// A planned route in local metres (east/north of the export origin).
struct DYNACITYDRIVE_API FDynaRoute
{
    TArray<FVector2D> Points;
    TArray<double> Along;        // cumulative metres at each point
    TArray<int32> Roads;         // road index of the leg Points[i] -> Points[i+1]
    TArray<FDynaRouteManeuver> Maneuvers;
    double Length = 0;
    double Seconds = 0;          // at the hourly speeds used when planning
    int32 Hour = 0;
    int32 SlowRoad = INDEX_NONE; // longest-delay stretch: slowest road with at least 100 m on route
    double SlowKmh = 0;
    FString DestinationRoad;
    bool IsValid() const { return Points.Num() >= 2; }
    void Reset() { Points.Reset(); Along.Reset(); Roads.Reset(); Maneuvers.Reset(); Length = Seconds = SlowKmh = 0; SlowRoad = INDEX_NONE; DestinationRoad.Reset(); }
    // Distance along the route of the closest point to P, searching legs near Hint first.
    double Project(const FVector2D& P, int32& Hint, double& OffRouteM) const;
};

// Fastest path over the exported road centrelines using each road's Tari'ak median speed
// for the given hour. The network carries no one-way or turn-ban data, so this is a
// travel-time shortest path, not a legal driving route.
class DYNACITYDRIVE_API FDynaRoutePlanner
{
public:
    // Speeds: 24 km/h values per road (hour 0..23); an empty row uses DefaultKmh.
    void Build(const TArray<FDynaMapSegment>& Segments, const TArray<TArray<float>>& Speeds);
    bool IsBuilt() const { return Built; }
    bool Snap(const FVector2D& P, double MaxDistanceM, int32& Segment, FVector2D& At) const;
    bool Plan(const FVector2D& From, const FVector2D& To, int32 Hour, const TArray<FString>& RoadNames, FDynaRoute& Out, FString& Error) const;
    double SpeedKmh(int32 Road, int32 Hour) const;
    static constexpr double DefaultKmh = 25;
    int32 NodeCount() const { return Nodes.Num(); }
    int32 JunctionLinks() const { return Links; }
    static FString RoadLabel(const TArray<FString>& RoadNames, int32 Road);
private:
    struct FEdge { int32 To; double Length; int32 Road; };
    TArray<TArray<float>> RoadSpeeds;
    double MaxKmh = DefaultKmh;
    TArray<FVector2D> Nodes;
    TArray<TArray<FEdge>> Adjacent;
    TArray<int32> SegA, SegB, SegRoad;
    TMap<FIntPoint, TArray<int32>> Grid;   // 100 m cells -> segment indices
    int32 Links = 0;
    bool Built = false;
    void AddEdge(int32 A, int32 B, double Cost, int32 Road);
};
