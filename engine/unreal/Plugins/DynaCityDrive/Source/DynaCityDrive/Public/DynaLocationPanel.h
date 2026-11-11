#pragma once
#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "DynaLocationPanel.generated.h"

class SDynaLocationSearch;
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FDynaLocationQuery, const FString&, Query);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FDynaLocationSelection, int32, Index);

/** Native search UI. The current editor host supplies the offline OSM search adapter. */
UCLASS(BlueprintType)
class DYNACITYDRIVE_API UDynaLocationPanel : public UObject
{
    GENERATED_BODY()
public:
    UPROPERTY(BlueprintAssignable, Category="Beirut|Location") FDynaLocationQuery QueryRequested;
    UPROPERTY(BlueprintAssignable, Category="Beirut|Location") FDynaLocationSelection ResultSelected;
    UFUNCTION(BlueprintCallable, Category="Beirut|Location") void Show();
    UFUNCTION(BlueprintCallable, Category="Beirut|Location") void SetResults(const FString& ResultsJson);
    UFUNCTION(BlueprintCallable, Category="Beirut|Location") void SetStatus(const FString& Message);
    UFUNCTION(BlueprintCallable, Category="Beirut|Location") static void ShowRegisteredPanel();
private:
    TWeakPtr<SDynaLocationSearch> Widget;
};

