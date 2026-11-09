#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "DynaRoutePlanner.h"
#include "DynaMobilityWorld.generated.h"

class UInstancedStaticMeshComponent;
class UStaticMesh;
class UDynaMinimapComponent;
class AHUD;
class UCanvas;
class UDynaVehicleLights;
class UMaterialInterface;
struct FDynaMapSegment { FVector2D A, B; int32 Road = INDEX_NONE; };
struct FDynaTrafficVehicle { FString Id; FString TypeId; FVector Position; double Heading = 0; int32 LightBits = -1; FLinearColor Paint = FLinearColor(0,0,0,0); };
struct FDynaTrafficFrame { double Time = 0; TArray<FDynaTrafficVehicle> Vehicles; };

// Rendering adapter only. SUMO owns trajectories; these instances never drive physics.
UCLASS()
class DYNACITYDRIVE_API ADynaMobilityWorld : public AActor
{
    GENERATED_BODY()
public:
    ADynaMobilityWorld();
    virtual void Tick(float DeltaSeconds) override;
    UFUNCTION(BlueprintCallable, Category="Beirut") void Configure(const FString& MapJson,
        FVector Origin, FVector East, FVector North, FVector Up, UStaticMesh* VehicleMesh);
    // Additional, already-prepared meshes. The configured driving mesh remains
    // the default and missing candidates are ignored. Selection is stable by ID.
    UFUNCTION(BlueprintCallable, Category="Beirut") void ConfigureTrafficFleet(const TArray<UStaticMesh*>& AdditionalMeshes);
    // Map SUMO type IDs to groups (0 is the default mesh). Unmapped typed
    // vehicles are omitted when a mapping is active instead of misrepresented.
    UFUNCTION(BlueprintCallable, Category="Beirut") void ConfigureTrafficTypes(const TMap<FString, int32>& TypeGroups);
    UFUNCTION(BlueprintCallable, Category="Beirut") bool AppendFrames(const FString& Json);
    UFUNCTION(BlueprintCallable, Category="Beirut") void ClearTraffic();
    UFUNCTION(BlueprintCallable, Category="Beirut") void ConfigureLights(const TArray<UMaterialInterface*>& Materials);
    UPROPERTY(VisibleAnywhere, Category="Beirut|Atmosphere") TObjectPtr<UDynaVehicleLights> VehicleLights;
    UFUNCTION(BlueprintPure, Category="Beirut") FString Status() const;
    void DrawMinimap(AHUD* HUD, const APawn* Player, float X, float Y, float Size, UCanvas* Canvas);
    FVector2D Local(const FVector& Position) const;
    // Name of the mapped road under Position, or empty when the nearest road is unnamed or farther
    // than MaxDistanceM. Keeps the previous street near junctions so the label does not flicker.
    FString StreetAt(const FVector& Position, double MaxDistanceM = 20.0);
    // GPS waypoint in local metres (east/north of the export origin). Plans the fastest route
    // from the player at the current in-game hour and returns RouteStatus(), or {"error":...}.
    UFUNCTION(BlueprintCallable, Category="Beirut|Route") FString SetWaypoint(double X, double Y);
    UFUNCTION(BlueprintCallable, Category="Beirut|Route") void ClearWaypoint();
    // Route geometry, progress, next instructions and travel-time estimate as JSON.
    UFUNCTION(BlueprintPure, Category="Beirut|Route") FString RouteStatus() const;
    UFUNCTION(BlueprintPure, Category="Beirut|Route") bool HasRoute() const { return RouteActive; }
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut|Minimap") TObjectPtr<UDynaMinimapComponent> Minimap;
    UPROPERTY(VisibleAnywhere, Category="Beirut") TObjectPtr<UInstancedStaticMeshComponent> Vehicles;
    UPROPERTY(Transient) TArray<TObjectPtr<UInstancedStaticMeshComponent>> AdditionalVehicles;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut") int32 MaxVisibleVehicles = 750;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut") float VisibilityRadiusM = 750;
private:
    FVector BasisOrigin, BasisEast, BasisNorth, BasisUp;
    TArray<FDynaMapSegment> Segments;
    TArray<FString> RoadNames;
    TArray<TArray<float>> RoadSpeeds;
    void UpdateRoute(double Dt);
    bool Replan(const FVector2D& From, FString& Error);
    int32 RouteHour() const;
    double RemainingSeconds() const;
    static constexpr float CardWidth = 300;
    void DrawRouteCard(AHUD* HUD, float X, float Y, float Height) const;
    FDynaRoutePlanner Planner;
    FDynaRoute Route;
    FVector2D Waypoint = FVector2D::ZeroVector;
    bool RouteActive = false;
    double RouteAlong = 0, RouteOff = 0, OffRouteSeconds = 0, ReplanCooldown = 0, ArrivedSeconds = 0;
    // Distance the car was from the route when it was planned (e.g. parked off-road);
    // shrinks as the car joins the road so being off the network never loops replanning.
    double RouteSlack = 0;
    int32 RouteHint = 0, Replans = 0, RouteVersion = 0;
    FString RouteMessage;
    int32 LastStreetRoad = INDEX_NONE;
    TMap<FIntPoint, TArray<int32>> RoadGrid;
    TArray<FDynaTrafficFrame> Frames;
    TMap<FString, int32> TrafficTypeGroups;
    double Clock = -1;
    int32 VisibleCars = 0;
    int32 RoadCount = 0;
};
