#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "DynaVehicleLights.generated.h"

class UInstancedStaticMeshComponent;
class UMaterialInterface;

// SUMO signal bits: right=1, left=2, hazards=4, brake=8. Missing is -1.
// Emissive fixtures only: no per-vehicle light sources or shadows.
UCLASS()
class DYNACITYDRIVE_API UDynaVehicleLights : public UActorComponent
{
    GENERATED_BODY()
public:
    void Configure(const TArray<UMaterialInterface*>& Materials);
    void Reset();
    void AddVehicle(const FTransform& Transform, const FBox& Bounds, int32 Signals, double Time, bool Night);
    void Flush();
    virtual void OnUnregister() override;
    UPROPERTY(Transient) TArray<TObjectPtr<UInstancedStaticMeshComponent>> Lamps;
private:
    TArray<TArray<FTransform>> Pending;
};
