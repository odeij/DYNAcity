#pragma once
#include <cmath>
#include <vector>

// WGS84 / UTM zone 36 north (EPSG:32636), not a lon/lat offset approximation.
// Snyder inverse transverse Mercator series; double precision throughout.
namespace DynaMapMath
{
constexpr double Pi = 3.14159265358979323846;
constexpr double Radius = 6378137.0;
struct Point { double X = 0, Y = 0; };
inline Point Utm36ToLonLat(double East, double North)
{
    constexpr double E2 = 0.0066943799901413165, K = .9996;
    const double Ep2 = E2 / (1-E2), E1 = (1-std::sqrt(1-E2))/(1+std::sqrt(1-E2));
    const double Mu = (North/K)/(Radius*(1-E2/4-3*E2*E2/64-5*E2*E2*E2/256));
    const double Phi = Mu + (3*E1/2-27*std::pow(E1,3)/32)*std::sin(2*Mu)
        +(21*E1*E1/16-55*std::pow(E1,4)/32)*std::sin(4*Mu)
        +151*std::pow(E1,3)/96*std::sin(6*Mu)+1097*std::pow(E1,4)/512*std::sin(8*Mu);
    const double S=std::sin(Phi), C=std::cos(Phi), T=std::tan(Phi)*std::tan(Phi), Q=Ep2*C*C;
    const double N=Radius/std::sqrt(1-E2*S*S), R=Radius*(1-E2)/std::pow(1-E2*S*S,1.5);
    const double D=(East-500000)/(N*K);
    const double Lat=Phi-(N*std::tan(Phi)/R)*(D*D/2-(5+3*T+10*Q-4*Q*Q-9*Ep2)*std::pow(D,4)/24
        +(61+90*T+298*Q+45*T*T-252*Ep2-3*Q*Q)*std::pow(D,6)/720);
    const double Lon=33*Pi/180+(D-(1+2*T+Q)*std::pow(D,3)/6
        +(5-2*Q+28*T-3*Q*Q+8*Ep2+24*T*T)*std::pow(D,5)/120)/C;
    return {Lon*180/Pi,Lat*180/Pi};
}
inline Point LonLatToMercator(Point LL)
{
    const double Lat=std::fmax(-85.05112878,std::fmin(85.05112878,LL.Y))*Pi/180;
    return {Radius*LL.X*Pi/180,Radius*std::log(std::tan(Pi/4+Lat/2))};
}
inline Point Utm36ToMercator(double E, double N) { return LonLatToMercator(Utm36ToLonLat(E,N)); }
inline double AngleDelta(double From, double To) { return std::remainder(To-From,2*Pi); }
inline double FollowAlpha(double Dt, double Rate) { return 1-std::exp(-std::fmax(0.0,Dt)*Rate); }
inline int RasterZoom(double PixelsPerMercatorMeter,int MaxZoom)
{
    const int Zoom=int(std::ceil(std::log2(2*Pi*Radius*PixelsPerMercatorMeter/256)));
    return Zoom<0?0:Zoom>MaxZoom?MaxZoom:Zoom;
}

struct Vertex { Point Position, UV; };
// Sutherland-Hodgman; interpolate UVs at every intersection, then triangulate.
// Used both to determine visible requests and to clip the actual raster geometry.
inline std::vector<Vertex> ClipTile(std::vector<Vertex> Polygon, double Size)
{
    for(int Edge=0;Edge<4 && !Polygon.empty();++Edge)
    {
        auto Distance=[&](const Vertex& V) { return Edge==0?V.Position.X:Edge==1?Size-V.Position.X:Edge==2?V.Position.Y:Size-V.Position.Y; };
        std::vector<Vertex> Out;
        Vertex A=Polygon.back(); double DA=Distance(A);
        for(const Vertex& B:Polygon)
        {
            const double DB=Distance(B);
            if((DA>=0)!=(DB>=0))
            {
                const double T=DA/(DA-DB);
                Out.push_back({{A.Position.X+(B.Position.X-A.Position.X)*T,A.Position.Y+(B.Position.Y-A.Position.Y)*T},
                    {A.UV.X+(B.UV.X-A.UV.X)*T,A.UV.Y+(B.UV.Y-A.UV.Y)*T}});
            }
            if(DB>=0)Out.push_back(B);
            A=B; DA=DB;
        }
        Polygon=std::move(Out);
    }
    double Area=0;
    if(Polygon.size()>=3)
        for(size_t I=0;I<Polygon.size();++I)
        {
            const Point A=Polygon[I].Position,B=Polygon[(I+1)%Polygon.size()].Position;
            Area+=A.X*B.Y-B.X*A.Y;
        }
    if(std::abs(Area)<1e-8)Polygon.clear(); // Border-only contact is not a visible tile.
    return Polygon;
}
}
