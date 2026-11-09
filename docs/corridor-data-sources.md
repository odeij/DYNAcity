# Corridor data found on the web (8 November 2026)

A web search for what the Al Masar al Akhdar corridor work lacks: rainfall to drive
floods, flood events to check them against, traffic counts and trips to calibrate SUMO,
signal timings, the corridor project's own plans, and the cost of measures. It adds to
[flood/data-inventory.md](flood/data-inventory.md) and
[mobility-data-readiness.md](mobility-data-readiness.md) and does not repeat them.

Traffic plans are the core of the work ([simulator-learning-layer.md](simulator-learning-layer.md));
the rainfall and flood sections serve only the optional flood what-if.

The files are in the private data root, never in Git:
`C:\private\dynacity-data\web-sources-20261108\{flood,traffic,corridor}\`, each with a
`sources.json` (URL, retrieval date, SHA-256, licence or terms as stated, description).
Public access does not mean redistribution is allowed; most files state no licence.

## Rainfall: design storms

Three independent Beirut sets, which disagree by about 30% at one hour:

| Source | Basis | 1 h intensity, mm/h (2 / 10 / 100 y) | File |
|---|---|---|---|
| Hawkamaa, EU stormwater network code for Lebanon (dated 30 Nov 2022), Table 2 | not stated | 25.9 / 41.7 / 64.5 | `flood/hawkamaa_…_2023.pdf` |
| Thomas, AUB M.E. thesis (1971), Beirut airport recording gauge 1964–69 | measured; i = 212·T^0.195 / t^0.552 (t in min) | 10 y from the formula: 34.5 | `flood/aub_thomas_1971_…pdf` (scan) |
| Dargham & Andraos, Frontiers in Water (2026), IMERG V07 1998–2025, disaggregated | satellite; Gumbel α 17.65, β 6.58 at 1 h | 20.1 / 32.5 / 47.9 (computed from α, β; the paper prints no intensities) | `flood/frontiers_frwa_2026_1727182.pdf` (CC BY) |

Hawkamaa in full (mm/h; the code requires at least a 10-year design storm):

| Duration | 2 y | 5 y | 10 y | 50 y | 100 y |
|---|---|---|---|---|---|
| 10 min | 74.13 | 98.65 | 118.37 | 159.31 | 181.02 |
| 20 min | 53.33 | 70 | 84.9 | 115.38 | 131.22 |
| 30 min | 41.89 | 55.01 | 66.81 | 91.6 | 102.23 |
| 60 min | 25.94 | 33.31 | 41.69 | 57.86 | 64.54 |
| 90 min | 20 | 25.37 | 31.68 | 43 | 48.76 |
| 120 min | 17.78 | 23 | 27.42 | 35.34 | 40.3 |

The values were read from the PDF's table image and have not been typed in twice.
Read them off the page again before use. The spread between the three sets is itself an
input to the planner: a plan should be judged across them, not on one.

**Climate change.** Zittis, Bruggeman & Lelieveld (2021, Weather and Climate Extremes 34,
CC BY; 33 EURO-CORDEX runs, RCP8.5) give Beirut's 50-year one-day maximum as 148 mm
(1951–2000), 162 mm (2001–2050, +9%) and 154 mm (2051–2100, +4%), while annual totals
fall. Lebanon's Fourth National Communication (2022) projects fewer days over 10 and 20 mm
but gives nothing on maximum or sub-daily rain. No sub-daily change factor for Lebanon was
found.

**Daily series.**
- GHCN-Daily `LEM00040100`, Beirut airport, 1975–2025 with long gaps. The usable years
  are 1994–98 and 2008–16. The largest value is 138.9 mm on 7 January 2013; 262.9 mm on
  12 December 1995 looks suspect.
- Meteostat 40100 (CC BY-NC) is near complete for 2023–25. It has 64.3 mm on
  22 December 2023.
- NASA POWER at the corridor (0.5° reanalysis) smooths out extremes; do not use it for them.

## Flood events on the corridor

The one well-documented event is **22–23 December 2023**, from news reports only. The
Beirut River overflowed at Karantina:
- the Karantina highway "turned into a river", and traffic was cut;
- the Traffic Management Center closed the roads to the fish market and Karantina;
- about 40 cars were stranded (municipality, quoted by Arabic outlets). An-Nahar reports
  3 trucks, 7 cars and a Civil Defence vehicle trapped on the port road by the river;
- depth: "well over a metre" on the Quarantaine road, in French-language press. Arab News'
  "10 metres" is likely an error;
- cause given: an uncleaned riverbed and river mouth.

Earlier reports put water at the Dora roundabout and Quarantaine in 2020 (date not
confirmed), and traffic at a standstill at Dora and Nahr al-Mot in December 2013. Nothing
was found for Mar Mikhael, Charles Helou or Corniche el Nahr itself. The December 2023
event, with Meteostat's 64.3 mm that day, is the one candidate for checking a corridor
flood run. Its closures are qualitative: which roads were cut, not depths along them.

## Traffic counts and trips

**Peak-hour directional counts.** From the BRT ESIA (ELARD for CDR, 2018), Table 4-4,
checked against the PDF. The survey was done for the BRT feasibility study, about 2016–17,
with peaks at 09:00–10:00 and 15:30–16:30. The table gives no unit; vehicles per hour is
the evident reading.

| Road | Direction | AM | PM |
|---|---|---|---|
| Charles Helou | eastbound | 3,705 | 4,731 |
| Charles Helou | westbound | 7,024 | 4,270 |
| Jounieh–Beirut highway | eastbound | 6,532 | 6,708 |
| Jounieh–Beirut highway | westbound | 4,386 | 5,537 |
| Pierre Gemayel 1 | north / south | 2,366 / 1,744 | 2,354 / 1,540 |
| Pierre Gemayel 2 | north / south | 2,852 / 3,033 | 2,866 / 2,605 |

The same table has 22 more count points, mostly on the northern coastal highway. These
counts can set and check the boundary entry rates. Those rates are where the corridor run
fails today: the entry queue grows by about 900 cars a minute
([sumo-internals.md](sumo-internals.md#measured-on-the-corridor-8-november-2026)).
Which count points fall inside the corridor's road box still has to be matched.

**Inside the corridor.** Haddad, AUB thesis (2024, open access), manual counts of about
2023, 07:30–08:30 and 16:30–17:30:

| Road | AM | PM |
|---|---|---|
| Armenia Street, westbound / eastbound | 307 / 277 | 292 / 249 |
| Charles Malek, westbound | 1,294 | 923 |
| Georges Haddad, northbound | 993 | 1,101 |
| Nicolas Turk | 167 | 213 |

**Daily volumes, 1998.** From the BUTP preparatory study (TEAM/IAURIF/SOFRETU, 2000),
Table 6-5:
- Charles Helou east of the Beirut River 120,731;
- Nahr Street at Bourj Hammoud 26,063;
- Dora autostrade 104,790;
- Dora boulevard at Mercedes 112,373;
- Jisr el Bacha 58,877.

**Trips.**
- **The 1994 IAURIF/TEAM household survey** (in the same study): 4,300 households, 1.5 M
  motorised trips a day, 71% by private car and 19% by shared taxi. Its 63-zone system puts
  Mar Mikhael and El Jisr in zone 2, Corniche el Nahr and Jeitaoui in zone 3, and Bourj
  Hammoud in 25–26. Table 6-4 is a 17×17 district daily vehicle trip table for 1998
  (1,428,372 trips).
- **Anas, De Sarkar & Abou-Zeid (World Bank WPS 8158, 2017)**, 2013 base: AM-peak
  (07–08) car trips between municipal and Greater Beirut, plus door-to-door times by
  origin–destination pair. The AM peak hour is 6.71% of daily trips; car occupancy is 1.7.
- **No raw household data is public.** The AUB 2007 and 2010 travel surveys (staff and
  students only) are restricted; the contact listed is Prof. Maya Abou-Zeid.

**Population for trip generation.** All CC BY 4.0, all downloaded:
- WorldPop 2025 constrained, 100 m;
- WorldPop 2020 UN-adjusted, 100 m;
- Meta HRSL 2020, about 30 m;
- Kontur 2023, H3 hexagons of about 400 m.

No employment data at that resolution was found.

**Speeds after 2019:** none. TomTom has no Beirut index page, and the GitHub "Lebanon
traffic dataset" is the Tari'ak data already in use.

## Signals

No timing plans are public. Kapsch's reference sheet (2022) gives 231 signalised
junctions under ITACA adaptive control. BUTP Table 4-3 lists proposed 1998 cycle lengths
of 60–180 s. Its "Mar Mikhael" junction (90 s) is the one in Chiyah, not Gemmayzeh.

## The corridor project

No official project named "Al Masar al Akhdar" was found, in English, Arabic or French.
The name may be this project's own. The official work on the same line:

- **UN-Habitat linear park on the railway in Badawi**, about 670 m. AICS funds it and
  Arcadis did the concept. It includes gardens, cycle lanes, drainage channels and
  permeable paving, and was "under development" in 2024. No street or traffic changes
  are published. The rail bed is used today for informal parking.
- **Mar Mikhael station.** A green public space on 10,000 of 62,000 m² (UN-Habitat, AICS
  €2.3 M, 2022–24) and a UNESCO restoration (AICS €2 M; foundation stone 4 September 2025,
  end planned early 2027). The rest remains a bus depot of the Railway and Public
  Transport Authority.
- **UN-Habitat "Beirut River 2.0"** (2015, Bourj Hammoud and Badawi) proposes blue-green
  streets, a pedestrian bridge and swales in place of parking. The booklet is downloaded.

No GIS or design drawings are public. The concrete plan measures to test therefore come
from these documents (cycle lanes, parking removed from the rail bed, permeable paving,
swales) until the project team supplies its own.

## Not found

- Unit costs in Lebanon or the region for drain cleaning, retention tanks, permeable paving
  or raising roads.
- Any origin–destination matrix after 2013, and turning counts at Dora or on Corniche el
  Nahr.
- Signal timing plans.
- Measured flood depths.
