#include "DynaMinimapComponent.h"
#include "DynaMobilityWorld.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "Misc/AutomationTest.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDynaMinimapStatusTest,"DynaCity.Minimap.StatusAndGeographyContract",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDynaMinimapStatusTest::RunTest(const FString& Parameters)
{
    // Isolated world: never opens the city, starts PIE, draws HUD, or requests tiles.
    UWorld* World=UWorld::CreateWorld(EWorldType::Game,false);
    if(!TestNotNull(TEXT("Test world"),World))return false;
    ADynaMobilityWorld* Mobility=World->SpawnActor<ADynaMobilityWorld>();
    if(!TestNotNull(TEXT("Mobility actor"),Mobility)) { World->DestroyWorld(false); return false; }
    UDynaMinimapComponent* Map=Mobility->Minimap;
    const FString Temp=FPaths::Combine(FPlatformProcess::UserTempDir(),TEXT("DynaCity-Minimap-" )+FGuid::NewGuid().ToString());
    Map->PreferencesPathOverride=FPaths::Combine(Temp,TEXT("map-preferences.json"));
    auto Parse=[&](const FString& Text)
    {
        TSharedPtr<FJsonObject> Json;
        TestTrue(TEXT("Status is valid JSON"),FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Json));
        return Json.IsValid()?Json:MakeShared<FJsonObject>();
    };
    auto Inspect=[&]() { return Parse(Map->Status()); };
    TestNotNull(TEXT("Component status is reflected for Blueprint/Python"),Map->FindFunction(TEXT("Status")));
    TestNotNull(TEXT("Mobility status remains reflected"),Mobility->FindFunction(TEXT("Status")));
    auto State=Inspect();
    TestFalse(TEXT("Not georeferenced before configuration"),State->GetBoolField(TEXT("georeferenced")));
    TestEqual(TEXT("Default mode"),State->GetStringField(TEXT("mode")),FString(TEXT("street")));
    TestTrue(TEXT("Default heading-up"),State->GetBoolField(TEXT("heading_up")));
    const FString Origin=TEXT("\"origin_utm_m\":[731350.9384492891,3753198.5857285904,12.5]");
    const FString Roads=TEXT("\"roads\":[{\"path\":[[0,0,0],[10,10,0]]}]");
    auto Configure=[&](const FString& Reference)
    {
        const FString Json=TEXT("{")+Origin+TEXT(",")+Roads+(Reference.IsEmpty()?TEXT(""):TEXT(",\"map_reference\":")+Reference)+TEXT("}");
        Mobility->Configure(Json,FVector::ZeroVector,FVector(100,0,0),FVector(0,100,0),FVector(0,0,100),nullptr);
    };
    Configure(TEXT("{\"crs\":\"EPSG:32636\",\"origin_lonlat\":[0,0]}"));
    State=Inspect();
    TestTrue(TEXT("Accept authoritative UTM36N contract"),State->GetBoolField(TEXT("georeferenced")));
    TestTrue(TEXT("CRS declaration recorded"),State->GetBoolField(TEXT("crs_declared")));
    TestTrue(TEXT("Valid geography has no error"),State->GetStringField(TEXT("geography_error")).IsEmpty());
    const auto& Utm=State->GetArrayField(TEXT("origin_utm_m"));
    TestEqual(TEXT("Origin keeps source altitude"),Utm[2]->AsNumber(),12.5);
    const auto& LL=State->GetArrayField(TEXT("origin_lonlat"));
    TestTrue(TEXT("Authoritative UTM takes priority over advisory lonlat"),FMath::Abs(LL[0]->AsNumber()-35.5018)<2e-7 && FMath::Abs(LL[1]->AsNumber()-33.8938)<2e-7);
    TestTrue(TEXT("Center is explicitly unknown before first HUD draw"),State->GetObjectField(TEXT("center"))->HasTypedField<EJson::Null>(TEXT("position_m")));
    TestEqual(TEXT("Geographic center CRS"),State->GetObjectField(TEXT("center"))->GetStringField(TEXT("crs")),FString(TEXT("EPSG:3857")));
    Map->CycleMode(); TestEqual(TEXT("Cycle to satellite"),Inspect()->GetStringField(TEXT("mode")),FString(TEXT("satellite")));
    Map->CycleMode(); TestEqual(TEXT("Cycle to hybrid"),Inspect()->GetStringField(TEXT("mode")),FString(TEXT("hybrid")));
    Map->CycleMode(); Map->ToggleOrientation(); State=Inspect();
    TestEqual(TEXT("Cycle back to street"),State->GetStringField(TEXT("mode")),FString(TEXT("street")));
    TestFalse(TEXT("Orientation status updates without drawing"),State->GetBoolField(TEXT("heading_up")));
    const auto Combined=Parse(Mobility->Status());
    TestEqual(TEXT("Existing road status preserved"),Combined->GetNumberField(TEXT("roads")),1.0);
    TestEqual(TEXT("Existing traffic clock preserved"),Combined->GetNumberField(TEXT("time_s")),-1.0);
    TestTrue(TEXT("Existing traffic status fields preserved"),Combined->HasField(TEXT("visible_vehicles")) && Combined->HasField(TEXT("buffer_s")));
    TestEqual(TEXT("Mobility embeds identical minimap snapshot"),Combined->GetObjectField(TEXT("minimap"))->GetStringField(TEXT("mode")),State->GetStringField(TEXT("mode")));
    for(const TCHAR* Field:{TEXT("tiles"),TEXT("pending"),TEXT("errors"),TEXT("drawn_tiles"),TEXT("failed_loads_total")})
        TestEqual(FString::Printf(TEXT("Inspection does not create tile activity: %s"),Field),State->GetNumberField(Field),0.0);
    for(const FString Reference:{FString(TEXT("{\"crs\":\"EPSG:4326\"}")),FString(TEXT("{\"crs\":32636}")),FString(TEXT("null"))})
    {
        Configure(Reference); State=Inspect();
        TestFalse(TEXT("Reject unsupported or malformed map reference"),State->GetBoolField(TEXT("georeferenced")));
        TestFalse(TEXT("Explain geography rejection"),State->GetStringField(TEXT("geography_error")).IsEmpty());
        TestTrue(TEXT("Rejected configuration clears old origin"),State->HasTypedField<EJson::Null>(TEXT("origin_utm_m")));
        TestEqual(TEXT("Rejected geography does not remove local roads"),Parse(Mobility->Status())->GetNumberField(TEXT("roads")),1.0);
    }
    Configure(TEXT("{\"crs\":\" epsg:32636 \"}"));
    TestTrue(TEXT("Accept normalized CRS spelling"),Inspect()->GetBoolField(TEXT("georeferenced")));
    Configure(TEXT("")); State=Inspect();
    TestTrue(TEXT("Legacy payload without map_reference still works"),State->GetBoolField(TEXT("georeferenced")));
    TestFalse(TEXT("Legacy CRS is assumed, not declared"),State->GetBoolField(TEXT("crs_declared")));
    for(const FString Json:{FString(TEXT("{}")),FString(TEXT("{\"origin_utm_m\":[500000,3750000,\"bad\"]}")),FString(TEXT("not json"))})
    {
        Map->ConfigureGeography(Json); State=Inspect();
        TestFalse(TEXT("Reject missing/malformed origin"),State->GetBoolField(TEXT("georeferenced")));
        TestTrue(TEXT("No stale origin after invalid reconfigure"),State->HasTypedField<EJson::Null>(TEXT("origin_utm_m")));
    }
    World->DestroyWorld(false);
    IFileManager::Get().DeleteDirectory(*Temp,false,true);
    return true;
}
#endif
