using UnrealBuildTool;

public class DynaCityDrive : ModuleRules
{
    public DynaCityDrive(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine", "InputCore" });
        PrivateDependencyModuleNames.AddRange(new[] { "Json", "JsonUtilities", "Slate", "SlateCore", "WebBrowser", "HTTP", "ImageWrapper" });
    }
}
