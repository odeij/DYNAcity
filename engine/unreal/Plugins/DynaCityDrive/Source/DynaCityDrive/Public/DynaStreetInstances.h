#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "DynaStreetInstances.generated.h"

class UHierarchicalInstancedStaticMeshComponent;
class UStaticMesh;

/** Reusable mapped street fixtures; source identity follows instance order. */
UCLASS()
class DYNACITYDRIVE_API ADynaStreetInstances : public AActor
{
    GENERATED_BODY()
public:
    ADynaStreetInstances();
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut") TObjectPtr<UHierarchicalInstancedStaticMeshComponent> Instances;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut") FString LayerKind;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut") FString ProductionArea;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut") FString SourceManifest;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Beirut") TArray<FString> SourceIds;
    UFUNCTION(BlueprintCallable, Category="Beirut") void SetStreetMesh(UStaticMesh* Mesh);
    UFUNCTION(BlueprintCallable, Category="Beirut") int32 AddStreetInstance(const FTransform& Transform, const FString& SourceId);
    UFUNCTION(BlueprintCallable, Category="Beirut") void ClearStreetInstances();
    UFUNCTION(BlueprintCallable, Category="Beirut") void SetStreetCollision(bool Enabled);
    UFUNCTION(BlueprintCallable, Category="Beirut") void SetStreetCullDistance(int32 StartCm, int32 EndCm);
    UFUNCTION(BlueprintPure, Category="Beirut") int32 GetStreetInstanceCount() const;
};
