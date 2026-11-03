#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "DynaMinimapComponent.generated.h"

class AHUD;
class UCanvas;
class APawn;
class UTexture2D;
class IHttpRequest;
class FJsonObject;
struct FDynaMapSegment;
struct FDynaTileData;

UENUM(BlueprintType)
enum class EDynaBasemap : uint8 { Street, Satellite, Hybrid };

// Owns only presentation and on-demand imagery. Never ticks or modifies traffic.
UCLASS(ClassGroup=(DynaCity))
class DYNACITYDRIVE_API UDynaMinimapComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    UDynaMinimapComponent();
    void ConfigureGeography(const FString& Json);
    UFUNCTION(BlueprintPure, Category="Beirut|Minimap") FString Status() const;
    TSharedRef<FJsonObject> StatusObject() const;
    void Draw(AHUD* HUD, UCanvas* Canvas, const APawn* Player, float X, float Y, float Size,
        const TArray<FDynaMapSegment>& Roads, const TMap<FIntPoint,TArray<int32>>& Grid);
    UFUNCTION(BlueprintCallable, Category="Beirut|Minimap") bool SetPreferences(const FString& ModeName, bool bHeadingUp);
    void CycleMode();
    void ToggleOrientation();
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut|Minimap") EDynaBasemap Mode = EDynaBasemap::Street;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Beirut|Minimap") bool HeadingUp = true;
private:
    friend class FDynaMinimapStatusTest;
    friend class FDynaMinimapPreferencesTest;
    friend class FDynaMinimapQualityTest;
    FString PreferencesPath() const;
    void LoadPreferences();
    void ClampImageryRange();
    void UpdateRange(double Speed,double Dt,bool Snap);
    // Only automation friends can redirect preferences away from the user's settings.
    FString PreferencesPathOverride, PreferencesError;
    bool PreferencesLoaded = false;
    double HudSize = 220;
    struct FTileState
    {
        double FreshUntil = 0, RetryAt = 0, LastUsed = 0;
        bool Pending = false, Failed = false, MustRevalidate = false;
    };
    void LoadTile(const FString& Key, const FString& Url);
    void FetchTile(const FString& Key, const FString& Url, TSharedPtr<FDynaTileData,ESPMode::ThreadSafe> Cached);
    void FinishTile(const FString& Key, TSharedPtr<FDynaTileData,ESPMode::ThreadSafe> Data, bool Success);
    void InstallTile(const FString& Key, const FDynaTileData& Data);
    FVector2D Mercator(const FVector2D& Local) const;
    void TrimCache();
    UPROPERTY(Transient) TMap<FString,TObjectPtr<UTexture2D>> Textures;
    TMap<FString,FTileState> Tiles;
    TMap<FString,TSharedPtr<IHttpRequest,ESPMode::ThreadSafe>> Requests;
    TSet<FString> Visible;
    FVector UtmOrigin = FVector::ZeroVector;
    FString GeographyError = TEXT("Geography not configured");
    FString ReferenceCrs = TEXT("EPSG:32636");
    bool CrsDeclared = false;
    FVector2D Centre = FVector2D::ZeroVector, LastPlayer = FVector2D::ZeroVector;
    double Heading = 0, ActualHeading = 0, MapRotation = 0, Range = 180, LastDraw = 0, ProviderRetryAt[2] = {0,0};
    int32 ActiveLoads = 0, DrawnTiles = 0, MissingTiles = 0;
    int64 FailedLoads = 0;
    bool Georeferenced = false, Initialized = false, Stopped = false;
};
