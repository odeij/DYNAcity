#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "DynaAmbientSky.generated.h"
class UStaticMeshComponent;
class UVolumetricCloudComponent;
class UMaterialInterface;
class UMaterialInstanceDynamic;

// Engine volumetric clouds when the stock cloud material loads; otherwise one panning cloud shell, updated at 5 Hz.
UCLASS()
class DYNACITYDRIVE_API ADynaAmbientSky : public AActor
{
    GENERATED_BODY()
public:
    ADynaAmbientSky();
    void Configure(UMaterialInterface* Material,FVector Up);
    virtual void Tick(float DeltaSeconds) override;
    void SetHour(float Hour);
    void SetClouds(bool Enabled);
    UPROPERTY(VisibleAnywhere, Category="Beirut|Atmosphere") TObjectPtr<UStaticMeshComponent> Clouds;
    UPROPERTY(VisibleAnywhere, Category="Beirut|Atmosphere") TObjectPtr<UVolumetricCloudComponent> Volumetric;
    /** Use Unreal's volumetric clouds instead of the cheap dome. */
    UPROPERTY(EditAnywhere, Category="Beirut|Atmosphere") bool bUseVolumetricClouds = true;
private:
    bool VolumetricActive = false;
    UPROPERTY(Transient) TObjectPtr<UMaterialInstanceDynamic> CloudMaterial;
};
