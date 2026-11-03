#include "DynaMapMath.h"

namespace
{
std::vector<const char*> CheckMapMath()
{
    using namespace DynaMapMath;
    std::vector<const char*> Errors;
    auto Check=[&](bool Ok,const char* Message) { if(!Ok)Errors.push_back(Message); };
    // Independent pyproj EPSG:4326 -> EPSG:32636 fixtures, including Beirut and zone edges.
    struct Fixture { double E,N,Lon,Lat; };
    const Fixture Fixtures[]={
        {731350.9384492891,3753198.5857285904,35.5018,33.8938},
        {500000,0,33,0},
        {263553.9738987912,4987329.504698914,30,45},
        {667294.8211244507,6655205.483634564,36,60},
        {729317.9173297666,3753837.3467638763,35.48,33.9}};
    for(const auto& F:Fixtures)
    {
        const Point LL=Utm36ToLonLat(F.E,F.N);
        Check(std::abs(LL.X-F.Lon)<2e-7 && std::abs(LL.Y-F.Lat)<2e-7,"UTM36 inverse differs from independent fixture by >2 cm");
        const Point M=Utm36ToMercator(F.E,F.N),Expected=LonLatToMercator({F.Lon,F.Lat});
        Check(std::hypot(M.X-Expected.X,M.Y-Expected.Y)<.04,"UTM to Web Mercator registration mismatch");
    }
    Check(std::abs(AngleDelta(179*Pi/180,-179*Pi/180)-2*Pi/180)<1e-12,"Heading must cross +180/-180 along shortest angle");
    Check(std::abs(AngleDelta(-179*Pi/180,179*Pi/180)+2*Pi/180)<1e-12,"Reverse heading wrap must use shortest angle");
    for(int Hz:{30,60,144})
    {
        double Value=0;
        for(int I=0;I<Hz;++I)Value+=(1-Value)*FollowAlpha(1.0/Hz,9);
        Check(std::abs(Value-(1-std::exp(-9.0)))<1e-12,"Follow smoothing must be frame-rate independent");
    }
    Check(FollowAlpha(0,9)==0,"Paused HUD must not advance smoothing");
    auto Polygon=ClipTile({{{-50,-50},{0,0}},{{150,-50},{1,0}},{{150,150},{1,1}},{{-50,150},{0,1}}},100);
    Check(Polygon.size()==4,"Enclosing raster tile must clip to four corners");
    for(const auto& V:Polygon)
    {
        Check(V.Position.X>=0 && V.Position.X<=100 && V.Position.Y>=0 && V.Position.Y<=100,"Clipped raster escaped minimap");
        Check(std::abs(V.UV.X-(V.Position.X+50)/200)<1e-12 && std::abs(V.UV.Y-(V.Position.Y+50)/200)<1e-12,"Clipping must interpolate UVs, not stretch imagery");
    }
    Check(ClipTile({{{-30,-30},{0,0}},{{-10,-30},{1,0}},{{-10,-10},{1,1}},{{-30,-10},{0,1}}},100).empty(),"Offscreen tile must not enter request queue");
    Check(ClipTile({{{100,0},{0,0}},{{200,0},{1,0}},{{200,100},{1,1}},{{100,100},{0,1}}},100).empty(),"A tile touching only the border must not be fetched");
    // Arbitrarily rotated quads must retain valid UVs and stay entirely within the map.
    for(int Angle=0;Angle<360;++Angle)
    {
        std::vector<Vertex> Quad;
        for(Point P: {Point{0,0},Point{1,0},Point{1,1},Point{0,1}})
        {
            const double X=(P.X-.5)*140,Y=(P.Y-.5)*140,A=Angle*Pi/180;
            Quad.push_back({{50+std::cos(A)*X-std::sin(A)*Y,50+std::sin(A)*X+std::cos(A)*Y},P});
        }
        const auto Clipped=ClipTile(std::move(Quad),100);
        Check(Clipped.size()>=4,"Rotated enclosing tile disappeared");
        for(const auto& V:Clipped)
        {
            Check(V.Position.X>=-1e-9 && V.Position.X<=100+1e-9 && V.Position.Y>=-1e-9 && V.Position.Y<=100+1e-9,"Rotated raster bleeds past clip rectangle");
            Check(V.UV.X>=-1e-9 && V.UV.X<=1+1e-9 && V.UV.Y>=-1e-9 && V.UV.Y<=1+1e-9,"Rotated clip generated invalid UV");
        }
    }
    return Errors;
}
}

#if defined(DYNA_MAP_STANDALONE)
#include <cstdio>
int main()
{
    const auto Errors=CheckMapMath();
    for(const char* Error:Errors)std::fprintf(stderr,"%s\n",Error);
    if(Errors.empty())std::puts("PASS: UTM36 fixtures, Mercator alignment, heading wrap, 30/60/144 Hz smoothing, UV clipping at 360 rotations");
    return Errors.empty()?0:1;
}
#else
#include "Misc/AutomationTest.h"
#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDynaMinimapMathTest,"DynaCity.Minimap.GeographySmoothingAndClipping",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDynaMinimapMathTest::RunTest(const FString& Parameters)
{
    const auto Errors=CheckMapMath();
    for(const char* Error:Errors)AddError(UTF8_TO_TCHAR(Error));
    return Errors.empty();
}
#endif
#endif
