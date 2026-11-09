# RL for mobility: literature review and proposal

Requested 3 November 2026: focus on mobility. Find a reinforcement-learning approach that
counters mobility problems as they happen, works in any city, and is trained on Beirut
with the simulation this repository already runs (SUMO through TraCI, `traffic_sumo.py`).

Three searches went in parallel: RL signal control that transfers across cities; RL for
disruption response, rerouting, gating and transit; and local work on Beirut and Lebanon.
The key papers below were checked against arXiv metadata or the publisher's page, and the
code licences against GitHub. Items marked *unconfirmed* could not be checked.

## Recommendation

**Signal control that stays good when the network breaks, learned on Beirut and run
anywhere.** One shared policy runs at every signalised junction. It is a graph network
over traffic movements, so it does not depend on the junction's layout or the city's
size. It is trained in the Beirut SUMO model on episodes that randomise the hour,
the demand and a disruption: a crash, a closure, a flood or a dark signal. It is judged
on disruptions and cities it never saw.

Why this one:

- **It is RL, and it acts as things happen.** Every few seconds each junction picks its
  next phase from what it sees now. A disruption changes the traffic, and the policy
  answers it.
- **It works in any city.** Inductive graph policies with shared parameters already
  transfer zero-shot to unseen networks: IG-RL, TransferLight, GESA and X-Light. Nothing
  in the model is tied to Beirut's junction count or shapes.
- **Our simulator can train it today.** The SUMO adapter already builds the Beirut
  network with 138 OSM signals and 26 turn bans, generates demand from the Tari'ak hourly
  speeds and CDR counts, and applies closures, crashes, floods and slowdowns through
  TraCI. Controlling signals is one more TraCI call (`trafficlight.setPhase`).
- **The gap is real.** Transferable controllers have not been tested under incidents.
  The incident benchmark T-REX (2025) shows that most RL controllers, the pressure-based
  shared ones included, degrade sharply when an incident hits, and it proposes no fix.
  Nobody has trained against dark signals, which Beirut has lived with since 2020. No
  RL mobility work on Beirut exists at network level.

### What would be new

1. **Transfer and incident robustness together.** TransferLight transfers but ignores
   incidents; T-REX tests incidents but has no method that transfers. Training on
   randomised disruptions with an inductive policy joins the two.
2. **Dark signals as a disruption.** About three-quarters of Beirut's lights had failed by
   July 2020 (Al Jazeera, 13 July 2020) and about half by late July (The National, 29 July
   2020): the maintenance contract lapsed and power cuts followed. Training episodes can
   switch random junctions to uncontrolled priority. The working neighbours then learn to
   absorb the failure, a case no RL signal paper covers.
3. **Flood depth in the state.** Water slows and closes roads (the Pregnolato curve, now
   in `traffic.road_disruption`). Junctions could see depth on their approaches. RL has
   been used for post-flood repair order and for evacuation, but not for traffic control
   that conditions on water depth.
4. **Probe-only sensing.** Beirut has no usable detector network. The policy sees what a
   connected-vehicle or probe fleet would report: counts and speeds per movement, with
   random masking (UniTSA's augmentation), so it never depends on loop detectors.

### How it would be trained and judged

| Part | Choice | From |
|---|---|---|
| State per junction | vehicles and halting vehicles per incoming and outgoing movement, current phase, time in phase; optional flood depth per approach; random masking | FRAP / MPLight movements, UniTSA masking |
| Action | next phase, every 5–10 s, with minimum green and yellow kept by the environment | RESCO, sumo-rl |
| Reward | negative pressure (needs only counts), plus a penalty for trips left with no route | PressLight; repo `vehicle_hours` and cut-off counts |
| Policy | one graph network shared by all junctions (heterogeneous over lanes, movements and phases) | IG-RL, TransferLight; GESA's automatic mapping onto a standard 4-approach junction |
| Algorithm | PPO with parameter sharing, or DQN as RESCO's strong simple baseline | X-Light, RESCO |
| Training episodes | one zone-sized subnetwork (a sector and its neighbours, from `zones.py`), a sampled hour, demand from the existing generator, one random disruption | repo zones and disruption layer; T-REX incident model |
| Baselines | SUMO's default fixed-time program, max-pressure, greedy (longest queue), no control at dark junctions | RESCO, T-REX |
| Gate | ship only if it beats max-pressure on held-out disruptions *and* held-out cities; otherwise report the negative result | as the repo's forecasting gate |
| "Any city" test | leave-one-out over Beirut zones, then zero-shot on RESCO Cologne and Ingolstadt (SUMO, calibrated demand) | X-Light protocol, RESCO |
| Robustness metrics | travel time and its rise under incidents, time to recover, trips cut off | T-REX |

### Risks, stated up front

- **Demand is not calibrated.** After ten simulated minutes the simulated-to-observed speed
  ratio is 1.14 at 08:00, 0.96 at 13:00 and 0.87 at 17:00, and the fleet is still growing
  (`docs/traffic-sumo.md`). The policy learns on plausible Beirut traffic, not measured
  traffic. That is why it is judged on transfer to RESCO's calibrated cities and on
  robustness, and not claimed as a Beirut traffic forecast.
- **Signal phases are SUMO's guesses.** No Beirut timing plans exist. OSM tags 138
  signals; reports put the real count at about 200–231. Phase structure comes from SUMO's
  geometry.
- **The citywide run is too slow to train on.** 29,000 cars advance 2 s in about 1.8 s.
  Training on zone subnetworks, several in parallel, with `libsumo` instead of a TraCI
  socket, is the plan; this needs to be measured before the first experiment.
- **Mixed traffic is missing.** Motorcycles and stopping vans are not in the demand.
  SUMO's sublane model could add them later; no RL signal paper has validated that.
- **Licences.** sumo-rl (MIT), X-Light (MIT), UniTSA (MIT) and LA-Light (Apache-2.0) can be
  built on. RESCO is GPL-3. TransferLight, GESA, FRAP, MPLight and CoLight have no licence
  file: read them, do not copy them.

### Steps

1. A Gymnasium / PettingZoo environment over `traffic_sumo.Simulation` for one zone: signal
   control, demand, disruptions, reward. Install `gymnasium`, `torch`, `stable-baselines3`.
2. Baselines in that environment: fixed-time, max-pressure, greedy. Measure steps per second.
3. A shared-parameter policy on one zone without disruptions, against max-pressure.
4. Randomised disruptions: crash, closure, flood depth, dark signals.
5. Transfer: held-out zones, then RESCO Cologne and Ingolstadt.
6. Later, a second lever: route advisories followed by a share of drivers (Shou et al. 2022),
   sharing the same reward.

## Alternatives considered

| Approach | Example | Verdict for this project |
|---|---|---|
| Compliance-limited rerouting / route advice | Shou et al. 2022, TR-C 137:103560 | Strong second lever; TraCI rerouting already exists. Transfer is harder: actions are tied to a network's links. Step 6. |
| Perimeter (zone) gating | Zhou & Gayah 2021, 2023 (TR-C); antifragile perimeter control, arXiv:2402.12665 | Fits the zones, but the literature is macroscopic (MFD); in SUMO it would be new, yet harder to transfer and to judge. |
| Emergency-vehicle passage | EMVLight, AAAI 2022 | A narrower case; could reuse the same policy later. |
| Bus holding / transit priority | Wang & Sun 2020 (TR-C 116); Kwesiga et al. arXiv:2411.19359 | Assumes schedules and fixed stops. Beirut's vans have neither (Samaha & Mohtar 2020). Novel but the data is not there. |
| Tolls, ramp metering, variable speed limits | Pandey et al. 2020; Belletti et al. 2018 | Beirut has no such infrastructure. |
| LLM supervisor over a controller | LA-Light, arXiv:2403.08337 (Apache-2.0, SUMO) | Handles blockages and sensor outages; a possible layer on top, not the RL core. |
| Repair order after a disaster | Fan et al. 2023, J. Infrastructure Preservation and Resilience | Strategic, not real-time mobility control. |
| Offline RL | DTLight, DataLight | Needs logged signal operations, which Beirut does not have. |

## Literature

### Signal control that transfers

- **FRAP**: Zheng et al., CIKM 2019, DOI 10.1145/3357384.3357900. Phase competition over movements; invariant to rotation and flips. The encoding most later models reuse.
- **PressLight**: Wei et al., KDD 2019, DOI 10.1145/3292500.3330949. Pressure as reward, from max-pressure theory (Varaiya 2013, TR-C 36).
- **MPLight**: Chen et al., AAAI 2020, DOI 10.1609/aaai.v34i04.5744. One shared DQN for 2,510 Manhattan signals (CityFlow).
- **AttendLight**: Oroojlooy et al., NeurIPS 2020, arXiv:2010.05772. First universal model; pays about 13% for generality, recovered by fine-tuning.
- **IG-RL**: Devailly, Larocque, Charlin, IEEE T-ITS, arXiv:2003.05738. Inductive graph RL; trained on synthetic networks, zero-shot to 3,971 Manhattan signals.
- **GESA**: Jiang et al., IEEE T-ITS 25(9), 2024, DOI 10.1109/TITS.2024.3377106. Maps any junction geometry to a standard template; co-trained on seven SUMO scenarios; zero-shot gain on a new one.
- **X-Light**: Jiang et al., IJCAI 2024, arXiv:2404.12090. Transformer on transformer; leave-one-city-out in SUMO, +7.9% average zero-shot. Code MIT (`jiang-haoyuan/X-Light`).
- **UniTSA**: Wang et al., IEEE T-VT 2024, arXiv:2312.05090. Junction matrix plus augmentation including random masking; SUMO; code MIT (`wmn7/Universal-Light`).
- **TransferLight**: Schmidt, Dreyer, Hashimi, AAAI-25 MALTA workshop, arXiv:2412.09719. Heterogeneous graph network trained only on random networks; zero-shot on RESCO Cologne beats max-pressure, falls behind on Ingolstadt21. Code has no licence.
- **CityLight**: Zeng et al., arXiv:2406.02126 (CIKM 2025 per arXiv; *unconfirmed*). Up to 13,952 junctions, but in the MOSS simulator, not SUMO.
- **MetaLight** (AAAI 2020) and **GeneraLight** (CIKM 2020): meta-learning across demand patterns; GeneraLight's generated flows matter when there is no OD matrix.

### Robustness, missing data, disruption

- **Rodrigues & Azevedo**, arXiv:1904.08353 (2019). Controllers become robust to incidents only when incidents are in training.
- **T-REX**: Nguyen, Azevedo, Toledo, Rodrigues, arXiv:2506.13836 (2025). SUMO incident benchmark on RESCO networks; most methods degrade, hierarchical FMA2C holds up best; incident-trained policies were not always better.
- **Mei et al.**, IJCAI 2023, arXiv:2304.10722. Signal control with junctions that have no sensors.
- **DiffLight**, NeurIPS 2024, arXiv:2410.22938. Diffusion model for control and imputation together.
- **LA-Light**: Wang et al., arXiv:2403.08337. LLM over a controller, in SUMO; handles blockages, emergency vehicles and sensor outages.
- **EMVLight**: Su et al., AAAI 2022, arXiv:2109.05429. Emergency-vehicle routing and signal pre-emption.
- **Zeinaly et al.**, Sustainability 15(2):1329, 2023. DQN at one junction under an accident, in SUMO.
- **Aslani, Mesgari, Wiering**, TR-C 85:732, 2017, DOI 10.1016/j.trc.2017.09.020. Actor-critic in SUMO on downtown Tehran with jaywalking, parking and disruption events: the closest regional template.

### Benchmarks and tools

- **RESCO**: Ault & Sharon, NeurIPS 2021 Datasets and Benchmarks. SUMO; Cologne and Ingolstadt at 1, 3/7 and 8/21 junctions; found published methods fragile under realistic sensing. Code GPL-3.
- **sumo-rl**: Alegre, `LucasAlegre/sumo-rl`, MIT. Gymnasium and PettingZoo wrapper; ships the RESCO networks.
- **LibSignal**: Mei et al., Machine Learning 113, 2024. SUMO and CityFlow under one API (no licence).

### Other levers

- **Shou, Chen, Fu, Di**, TR-C 137:103560, 2022, arXiv:2011.10915. Vehicles as learning agents in dynamic traffic assignment; controllable and background traffic, an accident case.
- **Zhou & Gayah**, TR-C 124:102949 (2021) and TR-C 148:104033 (2023). RL perimeter control, macroscopic.
- **Sun et al.**, antifragile perimeter control, arXiv:2402.12665. Gating under demand surges and capacity loss.
- **Wang & Sun**, TR-C 116:102661, 2020. Multi-agent RL bus holding.
- **Fan et al.**, J. Infrastructure Preservation and Resilience 4, 2023, DOI 10.1186/s43065-023-00072-x. GCN RL for repair order after an earthquake or flood.

## Local work

No RL mobility work on Beirut at network or corridor level was found.

- **Hamadeh et al.**, arXiv:2106.02315 (2021), Lebanese University. The only confirmed SUMO
  model of Beirut: the Saëb Salam junctions, with rule-based agents (not RL), using
  Traffic Management Center data. Time lost fell about 50% in their scenarios.
- **Natafgi et al.**, IEEE IMCET 2018, DOI 10.1109/IMCET.2018.8603041. One junction, RL on
  "Lebanese traffic data" by its abstract; the site and the method are *unconfirmed* (a
  later paper describes it as supervised learning).
- **Tari'ak** is from AUST, not AUB: Barbar, Ismail, Khawandi, NOF 2014,
  DOI 10.1109/NOF.2014.7119786. Its dataset card gives 2014 in one place and 2015 in
  another as the start date.
- **AUB transport research** (Abou-Zeid, Kaysi and others) is behaviour and demand: mode
  choice, bus upgrade preferences (Chalak et al. 2016, TR-A 88), shared taxis, parking at
  AUB, and the World Bank congestion model (Anas et al. 2017, WP 8158). The AUB 2007, 2010
  and 2017 travel surveys could become a demand source for the simulator.
- **Informal transit**: Samaha & Mohtar 2020 (J. Transport Geography 85) on Van line 4,
  about 56,000 riders a day; the Bus Map Project's route mapping (Assaf et al. 2021).
- **Crisis mobility**: Hatoum & Barraj 2023 (TRIP) on travel behaviour in the crisis;
  earthquake evacuation agent models of Beirut (Iskandar et al. 2024, Simulation).
- **Files on this machine**: an RL course syllabus (Yonsei CAS4160) and the CVPR 2025
  URBAN-SIM micromobility paper (Wu et al.). URBAN-SIM trains robots moving through
  streets in its own simulator; it does not fit network traffic in SUMO.
