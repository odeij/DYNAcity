#include "DynaMinimapComponent.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#if PLATFORM_WINDOWS
#include "Windows/WindowsHWrapper.h"
#endif

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDynaMinimapPreferencesTest,"DynaCity.Minimap.PersistentPreferences",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDynaMinimapPreferencesTest::RunTest(const FString& Parameters)
{
    const FString Temp=FPaths::Combine(FPlatformProcess::UserTempDir(),TEXT("DynaCity-Preferences-")+FGuid::NewGuid().ToString());
    const FString Path=FPaths::Combine(Temp,TEXT("map-preferences.json"));
    auto Create=[&]()
    {
        auto* Map=NewObject<UDynaMinimapComponent>();
        Map->PreferencesPathOverride=Path;
        return Map;
    };
    auto* Map=Create();
    TestNotNull(TEXT("SetPreferences is exposed for UI Blueprint/Python callers"),Map->FindFunction(TEXT("SetPreferences")));
    Map->ConfigureGeography(TEXT("{}"));
    TestEqual(TEXT("Missing file defaults to Street"),Map->Mode,EDynaBasemap::Street);
    TestTrue(TEXT("Missing file defaults heading up"),Map->HeadingUp);
    TestTrue(TEXT("First run has no preference error"),Map->PreferencesError.IsEmpty());
    TestFalse(TEXT("Loading missing settings does not create settings"),IFileManager::Get().FileExists(*Path));
    TestTrue(TEXT("Save explicit hybrid / north up"),Map->SetPreferences(TEXT("hybrid"),false));
    TestTrue(TEXT("Clamp immediately even before HUD draws"),Map->Range>=1100);
    FString Saved; TestTrue(TEXT("Read saved JSON"),FFileHelper::LoadFileToString(Saved,*Path));
    TSharedPtr<FJsonObject> Json;
    TestTrue(TEXT("Saved JSON parses"),FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Saved),Json));
    if(Json)
    {
        TestEqual(TEXT("Python-compatible schema version"),Json->GetNumberField(TEXT("version")),1.0);
        TestEqual(TEXT("Persisted mode"),Json->GetStringField(TEXT("mode")),FString(TEXT("hybrid")));
        TestFalse(TEXT("Persisted orientation"),Json->GetBoolField(TEXT("heading_up")));
        TestEqual(TEXT("Version 1 writes only the shared schema"),Json->Values.Num(),3);
    }
    auto* Reload=Create(); Reload->ConfigureGeography(TEXT("{}"));
    TestEqual(TEXT("Fresh instance restores mode"),Reload->Mode,EDynaBasemap::Hybrid);
    TestFalse(TEXT("Fresh instance restores orientation"),Reload->HeadingUp);
    TestEqual(TEXT("Status identifies exact settings path"),Reload->StatusObject()->GetStringField(TEXT("preferences_path")),Path);
    for(const FString Name:{FString(TEXT("unknown")),FString(TEXT("")),FString(TEXT("Street"))})
    {
        TestFalse(TEXT("Reject unknown mode without fallback"),Map->SetPreferences(Name,true));
        TestEqual(TEXT("Invalid input preserves live mode"),Map->Mode,EDynaBasemap::Hybrid);
        TestFalse(TEXT("Invalid input preserves live heading"),Map->HeadingUp);
        FString Actual; FFileHelper::LoadFileToString(Actual,*Path);
        TestEqual(TEXT("Invalid input preserves saved bytes"),Actual,Saved);
        TestFalse(TEXT("Invalid input is readable in status"),Map->StatusObject()->GetStringField(TEXT("preferences_error")).IsEmpty());
    }
#if PLATFORM_WINDOWS
    // Hold the existing destination without delete-sharing to force replacement
    // failure after the temporary file was successfully written.
    HANDLE Locked=::CreateFileW(*Path,GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
    TestTrue(TEXT("Lock fixture"),Locked!=INVALID_HANDLE_VALUE);
    if(Locked!=INVALID_HANDLE_VALUE)
    {
        TestFalse(TEXT("Atomic replacement failure is reported"),Map->SetPreferences(TEXT("street"),true));
        TestEqual(TEXT("Failed replacement preserves live mode"),Map->Mode,EDynaBasemap::Hybrid);
        TestFalse(TEXT("Failed replacement preserves heading"),Map->HeadingUp);
        FString Actual; FFileHelper::LoadFileToString(Actual,*Path);
        TestEqual(TEXT("Failed replacement preserves original file"),Actual,Saved);
        ::CloseHandle(Locked);
    }
#endif
    Map->ToggleOrientation();
    TestTrue(TEXT("B persists orientation"),Map->HeadingUp);
    Map->CycleMode();
    TestEqual(TEXT("M persists next mode"),Map->Mode,EDynaBasemap::Street);
    Reload=Create(); Reload->ConfigureGeography(TEXT("{}"));
    TestEqual(TEXT("Reload sees M mode"),Reload->Mode,EDynaBasemap::Street);
    TestTrue(TEXT("Reload sees B heading"),Reload->HeadingUp);
    // An explicit API selection must not be undone by subsequent geography setup.
    TestTrue(TEXT("Set before configuration"),Map->SetPreferences(TEXT("satellite"),false));
    FFileHelper::SaveStringToFile(TEXT("{\"version\":1,\"mode\":\"street\",\"heading_up\":true}"),*Path);
    Map->ConfigureGeography(TEXT("{}"));
    TestEqual(TEXT("Geography does not override explicit preferences"),Map->Mode,EDynaBasemap::Satellite);
    TestFalse(TEXT("Geography does not override explicit heading"),Map->HeadingUp);
    for(const FString Broken:{FString(TEXT("not json")),FString(TEXT("{}")),
        FString(TEXT("{\"version\":2,\"mode\":\"hybrid\",\"heading_up\":false}")),
                FString(TEXT("{\"version\":\"1\",\"mode\":\"hybrid\",\"heading_up\":false}")),
                FString(TEXT("{\"version\":1,\"mode\":\"Hybrid\",\"heading_up\":false}")),
        FString(TEXT("{\"version\":1,\"mode\":\"bogus\",\"heading_up\":false}")),
        FString(TEXT("{\"version\":1,\"mode\":\"hybrid\",\"heading_up\":\"false\"}"))})
    {
        FFileHelper::SaveStringToFile(Broken,*Path);
        Reload=Create(); Reload->ConfigureGeography(TEXT("{}"));
        TestEqual(TEXT("Malformed settings fail safe Street"),Reload->Mode,EDynaBasemap::Street);
        TestTrue(TEXT("Malformed settings fail safe heading up"),Reload->HeadingUp);
        TestTrue(TEXT("Malformed settings report readable error"),Reload->PreferencesError.Contains(TEXT("Invalid preferences")));
        FString Actual; FFileHelper::LoadFileToString(Actual,*Path);
        TestEqual(TEXT("Malformed file retained for diagnosis"),Actual,Broken);
    }
    IFileManager::Get().Delete(*Path);
    IFileManager::Get().MakeDirectory(*Path);
    Reload=Create(); Reload->ConfigureGeography(TEXT("{}"));
    TestEqual(TEXT("Unreadable settings fail safe Street"),Reload->Mode,EDynaBasemap::Street);
    TestTrue(TEXT("Unreadable settings fail safe heading up"),Reload->HeadingUp);
    TestTrue(TEXT("Read failure is explicit"),Reload->PreferencesError.Contains(TEXT("Cannot read preferences")));
    TestFalse(TEXT("Unwritable destination is rejected"),Reload->SetPreferences(TEXT("hybrid"),false));
    TestEqual(TEXT("Write failure does not change mode"),Reload->Mode,EDynaBasemap::Street);
    TestTrue(TEXT("Write failure does not change heading"),Reload->HeadingUp);
    TArray<FString> Leftovers; IFileManager::Get().FindFiles(Leftovers,*(Path+TEXT(".*.tmp")),true,false);
    TestEqual(TEXT("No temporary preference files leaked"),Leftovers.Num(),0);
    IFileManager::Get().DeleteDirectory(*Temp,false,true);
    return true;
}
#endif
