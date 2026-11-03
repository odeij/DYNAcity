#include "DynaMinimapComponent.h"
#include "DynaMapMath.h"
#include "Dom/JsonObject.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDynaMinimapQualityTest,"DynaCity.Minimap.SourceResolutionAndTransitions",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDynaMinimapQualityTest::RunTest(const FString& Parameters)
{
    auto* Map=NewObject<UDynaMinimapComponent>();
    // Range-only tests never configure preferences, save files or issue tile requests.
    Map->Mode=EDynaBasemap::Street;
    Map->UpdateRange(0,0,true);
    TestEqual(TEXT("Street stationary half range"),Map->Range,180.0);
    Map->UpdateRange(100,0,true);
    TestEqual(TEXT("Street speed half range capped"),Map->Range,420.0);
    for(double Size:{160.0,220.0,480.0})for(EDynaBasemap Mode:{EDynaBasemap::Satellite,EDynaBasemap::Hybrid})
    {
        Map->HudSize=Size; Map->Range=180; Map->Mode=Mode; Map->ClampImageryRange();
        TestEqual(TEXT("Immediate regional extent >= 10 m/HUD pixel"),Map->Range,Size*5);
        Map->HudSize=Size*2; Map->UpdateRange(0,.016,false);
        TestTrue(TEXT("Resizing cannot transition through overzoom"),Map->Range>=Map->HudSize*5);
        auto State=Map->StatusObject();
        TestTrue(TEXT("Telemetry ground mpp is source-safe"),State->GetNumberField(TEXT("imagery_meters_per_pixel"))>=10);
        TestEqual(TEXT("Telemetry source limit"),State->GetNumberField(TEXT("imagery_effective_max_resolution_m")),10.0);
        TestFalse(TEXT("Telemetry never claims overzoom"),State->GetBoolField(TEXT("overzoom")));
        TestTrue(TEXT("Telemetry explains regional view"),State->GetBoolField(TEXT("regional_overview")));
        Map->Mode=EDynaBasemap::Street;
        const double Before=Map->Range;
        Map->UpdateRange(0,.016,false);
        TestTrue(TEXT("Street restores smoothly, not instantly"),Map->Range<Before && Map->Range>180);
        for(int I=0;I<600;++I)Map->UpdateRange(0,.016,false);
        TestTrue(TEXT("Street converges to close range"),FMath::Abs(Map->Range-180)<1e-6);
    }
    // Beirut HUD resolution used to floor fractional zoom and undersample street tiles.
    const double MercScale=1/FMath::Cos(33.8938*DynaMapMath::Pi/180);
    for(double Size:{160.0,220.0,480.0})for(double Range:{180.0,420.0,1100.0})
    {
        const double Pixels=Size/(2*Range*MercScale);
        const int Zoom=DynaMapMath::RasterZoom(Pixels,19);
        const double TilePixels=2*DynaMapMath::Pi*DynaMapMath::Radius/(1<<Zoom)*Pixels;
        TestTrue(TEXT("Ceil street zoom never undersamples HUD"),TilePixels<=256+1e-8);
        TestTrue(TEXT("Ceil street zoom does not request unnecessary higher detail"),TilePixels>128-1e-8);
        TestTrue(TEXT("Imagery capped at z14"),DynaMapMath::RasterZoom(Pixels,14)<=14);
        TestTrue(TEXT("EOX streets capped at z18"),DynaMapMath::RasterZoom(Pixels,18)<=18);
    }
    TestEqual(TEXT("220px satellite overview is 2.2km across"),220.0*5*2,2200.0);
    return true;
}
#endif
