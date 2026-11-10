# Decisions

Verified 2026-11-10. Anything that could not be reached or confirmed is marked NOT CONFIRMED; nothing is cited from memory.

## Dependencies

- **uvicorn[standard]** (>=0.30): the ASGI server for `fastapi`. FastAPI does not bundle one, and `[standard]` adds the usual speedups (uvloop, httptools, watchfiles) on Linux. It is a runtime dependency because the backend must be started by something. Same choice as the sibling projects.
- **httpx** (>=0.27), dev only: required by Starlette's `TestClient`. It is not used for outbound requests. Outbound HTTP to adsb.lol uses the standard library (`urllib.request`) with a timeout, per the project constraint.
- No other third-party runtime dependency beyond fastapi, pyyaml, python-multipart and pyjwt.

## 1. adsb.lol API

Sources: OpenAPI document at https://api.adsb.lol/api/openapi.json (fetched 2026-11-10; the human docs at https://api.adsb.lol/docs are a JavaScript shell), https://www.adsb.lol/docs/open-data/api/, and one live request (below).

- **Base URL:** `https://api.adsb.lol`. Only host the backend may contact. The host is an allow-list constant, never taken from input.
- **Point/radius endpoint:** `GET /v2/point/{lat}/{lon}/{radius}`. OpenAPI types: `lat` number in [-90, 90], `lon` number in [-180, 180], `radius` **integer**, maximum 250, minimum 0. The summary says "up to 250nm", so radius is in nautical miles. An equivalent alias exists: `/v2/lat/{lat}/lon/{lon}/dist/{radius}`. Decision: use `/v2/point/...` with `lat` and `lon` formatted from floats and `radius` from an int, so only numeric characters reach the URL (no SSRF).
- **Not used:** `/v2/mil` (military aircraft), per the civil-airspace constraint.
- **Live confirmation:** one request, `GET /v2/point/33.94/-118.41/25` at 2026-11-10 08:31 UTC returned HTTP 200, `content-type: application/json`, `cache-control: no-store`, 15,635 bytes, 36 aircraft. Saved outside the repo (not committed).
- **Response envelope (observed):** `{"ac": [...], "msg": "No error", "now": <epoch milliseconds>, "total": <int>, "ctime": <ms>, "ptime": <number>}`. `now` was `1794299503501`, which is milliseconds. This differs from readsb's own `aircraft.json`, where `now` is seconds. The normaliser must treat adsb.lol `now` as milliseconds. `msg`, `total`, `ctime` and `ptime` are not documented in the sources read; treat them as informational only.
- **Rate limit / usage:** NO numeric rate limit is published. The OpenAPI description says the API is free, that "in the future" a key will be required (obtainable by feeding adsb.lol), and that production users should contact the maintainer. Our own etiquette stands: at most 1 upstream request per region per 10 s per instance, 3 s timeout, fixed host, descriptive `User-Agent`. The demo should be described as a small non-commercial portfolio demo. NOT CONFIRMED: whether a Vercel-fronted demo counts as "production"; revisit before launch.
- **Aircraft fields.** Units and meanings are from the readsb JSON documentation (https://github.com/wiedehopf/readsb/blob/dev/README-json.md, fetched 2026-11-10). Keys are omitted when data is unavailable.

| Field | Unit / values |
|---|---|
| `hex` | 24-bit ICAO address, 6 hex digits; a leading `~` marks a non-ICAO address (e.g. TIS-B) |
| `type` | data source, see below |
| `flight` | callsign, 8 characters, space padded (observed: `"ANA105  "`) |
| `alt_baro` | feet, or the string `"ground"` |
| `alt_geom` | feet, WGS84 height |
| `gs` | knots |
| `track` | degrees, true track over ground, 0-359 |
| `baro_rate`, `geom_rate` | feet per minute |
| `squawk` | 4 octal digits, string |
| `emergency` | `none`, `general`, `lifeguard`, `minfuel`, `nordo`, `unlawful`, `downed`, `reserved` |
| `category` | emitter category, `A0`-`D7` (see section 4) |
| `lat`, `lon` | decimal degrees |
| `nic`, `nac_p`, `nac_v`, `sil` | integers (integrity / accuracy categories); `sil_type` is `unknown`, `perhour` or `persample` |
| `rc` | metres, radius of containment |
| `seen_pos` | seconds before `now` since the position was last updated |
| `seen` | seconds before `now` since the last message |
| `version` | ADS-B version, 0, 1 or 2 (3-7 reserved) |
| `mlat`, `tisb` | lists of field names derived from MLAT / TIS-B data (empty list = none) |
| `rssi` | dBFS, always negative |
| `true_heading` | degrees; usually transmitted only on the ground |
| `r`, `t` | registration and type code from adsb.lol's database (readsb: `--db-file` only) |
| `dbFlags` | bitfield, readsb: present only with `--db-file`: 1 military, 2 interesting, 4 PIA, 8 LADD |

  Also seen in the live sample but NOT in the readsb document read: `nav_qnh`, `nav_altitude_mcp`, `nav_altitude_fms`, `nav_heading`, `nav_modes`, `nic_baro`, `gva`, `sda`, `alert`, `spi`, `messages`, `dst`, `dir`. `dst`/`dir` appear to be distance and bearing from the query point (nautical miles, degrees); that is inferred, not documented, so do not rely on them.
- **`type` values** (readsb docs, in order of preference): `adsb_icao`, `adsb_icao_nt` (non-transponder emitter such as a ground vehicle), `adsr_icao`, `tisb_icao`, `adsc`, `mlat`, `other`, `mode_s`, `adsb_other`, `adsr_other`, `tisb_other`, `tisb_trackfile`. The live sample contained only `adsb_icao` (34) and `adsr_icao` (2).
- **Observed in the sample:** `alt_baro` was the string `"ground"` for 3 aircraft; `alt_geom` present on only 8 of 36; `track` on 13 of 36 (aircraft on the ground often carry `true_heading` instead); `category` absent on 5; `flight` absent on 7; `squawk` absent on 2. Nearly every field can be missing, which is why detections must treat missing inputs as "not evaluated".
- **`dbFlags` was ABSENT from all 36 records in the live sample**, even though `r` and `t` were present. So with this endpoint `dbFlags` cannot be assumed to be present. CONCERN for Task 2: the plan's rule "exclude aircraft flagged military in `dbFlags`" only works when the flag is delivered. NOT CONFIRMED whether the point endpoint ever returns `dbFlags`. Task 2 must (a) apply `dbFlags & 1` when present, (b) record how coverage was checked on real captures, and (c) decide on a fallback if the flag never appears. This was not tested with a second request (the brief allowed one).
- **Licence:** the OpenAPI document states the licence for the API and all data adsb.lol makes public is the Open Data Commons Open Database License (ODbL) v1.0, https://opendatacommons.org/licenses/odbl/1-0/. https://www.adsb.lol/docs/open-data/api/ says the same. adsb.lol publishes no attribution wording of its own.
- **ODbL obligations (summary of https://opendatacommons.org/licenses/odbl/1-0/ sections 4.2-4.7; not legal advice):**
  - A Produced Work that is publicly used (the map, the UI) must carry a notice that it contains information from the database under the ODbL. The licence's suggested form is "Contains information from DATABASE NAME, which is made available here under the Open Database License (ODbL)", with the name linked to the database and "Open Database License (ODbL)" linked to the licence. We use: `Contains information from adsb.lol (https://adsb.lol), which is made available here under the Open Database License (ODbL) (https://opendatacommons.org/licenses/odbl/1-0/).` Show it in the app footer, on the map and in the README.
  - Redistributing the database or a derived one (the committed recorded captures) requires the licence or its URI with the data and documentation, preserved notices, and share-alike under the ODbL: ship `data/LICENSE-ODbL.md`. The code stays MIT; the data is ODbL.
  - Internal use of a derivative database is not public use (section 4.5), and a Produced Work alone does not create a derivative database.

## 2. ICAO 24-bit address allocation by state

- **Primary source:** ICAO Annex 10, Volume III (Communication Systems), Part I, Appendix to Chapter 9, "Table 9-1. Allocation of aircraft addresses to States". A reproduction of the Appendix is in an ICAO working paper (APIRG/26 WP/03 F12), https://www.icao.int/sites/default/files/ESAF/APIRG/APIRG-26/FR/WPs-FRE/APIRG26-WP03F12-App-Guidelines-for-ICAO-24-Bits-Adresses-Assignment.pdf (fetched 2026-11-10; the table starts on its PDF page 5). It describes blocks defined by a fixed pattern of the first 4, 6, 9, 12 or 14 address bits, giving blocks of 1,048,576 / 262,144 / 32,768 / 4,096 / 1,024 addresses. The all-zero address must not be used.
- **Form:** the table gives each State's block as a bit pattern ("a dash represents a bit value equal to 0 or 1"), not as hex ranges, for roughly 190 allocations. Example read from the page image: Canada `1100 00` followed by dashes (262,144 addresses); Argentina `1110 00`; Brazil `1110 01`.
- **Full hex table NOT obtained from a citable open source.** The widely used hex lists are in GPL projects (tar1090 `html/flags.js`, an `ICAO_Ranges` array; the file has no licence header but says it is based on dump1090 code, which is GPL). Those cannot be copied into an MIT repository.
- **Decision on shipping:** build our own table (`backend/ghosttrack/facts/`) by transcribing Table 9-1 bit patterns into `(start, end, state)` ranges ourselves, with the source cited in a header. Use tar1090's `ICAO_Ranges` ONLY as an independent cross-check (compare values; copy no code or data files). Spot checks read from tar1090, to be re-verified against Table 9-1 during transcription: United States `A00000-AFFFFF`, Canada `C00000-C3FFFF`, Germany `3C0000-3FFFFF`, France `380000-3BFFFF`, Japan `840000-87FFFF`, Australia `7C0000-7FFFFF`, Mexico `0D0000-0D7FFF`. tar1090 shows the UK as a catch-all `400000-43FFFF` with smaller territory blocks nested inside; resolve with longest-match. Canada's `1100 00` pattern above is consistent with `C00000-C3FFFF`.
- **Transcription done (Task 3):** `backend/ghosttrack/facts/icao_table9_1.py` holds all 186 State rows and the 3 "Other allocations" (ICAO) rows of Table 9-1, transcribed by hand from the working paper's page images (PDF pages 5-10, Annex pages I-9-5 to I-9-10) as the printed bit patterns. Tests check that each pattern length matches its block-size column and that no blocks overlap. All seven tar1090 spot checks above match. The edition reproduced is older (it names Yugoslavia, Swaziland, the former Yugoslav Republic of Macedonia; it has no Serbia, Montenegro or South Sudan). Addresses outside every block are "unknown". Finding: `899000-8993FF`, which tar1090's list (cross-check only; NOT CONFIRMED from a primary source) labels Taiwan, is in this table an ICAO block "allocated for special use in the interest of flight safety". Such addresses resolve to an ICAO block with no State, and GT-006 does not evaluate them.
- **Registration prefixes:** `backend/ghosttrack/facts/registration_prefixes.py` maps the nationality marks of 51 States (North and South America, Europe, the Middle East, Asia-Pacific) to Table 9-1 State names. Nationality marks are assigned under ICAO Annex 7 from the ITU call-sign series; the specific marks were cross-checked on 2026-11-10 against https://en.wikipedia.org/wiki/List_of_aircraft_registration_prefixes (facts only, our own format). Marks that list flags as "not in the ICAO document" (Thailand `U`, Russia `RF`, Spain `EM`, UAE `DU`) are left out. Unlisted prefixes are "unknown".
- **Licence of the table:** ICAO publishes Annex 10 with copyright reserved; the working paper carries no explicit licence text that was visible. The allocations are factual data. Ship only the minimal derived facts (address range to State name) with attribution to Annex 10 Vol III Part I Appendix to Ch. 9. NOT CONFIRMED: any ICAO licence for redistribution; the residual risk is recorded in THIRD_PARTY.md.

## 3. US N-number to ICAO address mapping

- **No public description of the algorithm was found.** What is public is the extent of the block, `A00001` (N1) to `ADF7C7` (N99999), and a set of registration/address pairs: the README of https://github.com/guillaumemichel/icao-nnumber_converter (GPL-3.0; PyPI `icao-nnumber-converter-us`) lists 9 pairs and calls the mapping "somewhat intuitive, but not convenient to implement", without describing it. A Java gist, https://gist.github.com/jwoschitz/9a5195b36f0cc7d25455283923df083f, is reported by a search summary to check the same range (not read). The FAA registry pages (https://registry.faa.gov/AircraftInquiry/Search/NNumberResult?nNumberTxt=...) show "Mode S Code (Base 16 / Hex)" but returned HTTP 403 to automated fetch, so FAA pages could not be used to cite pairs.
- **The algorithm in `backend/ghosttrack/icao.py` is our own derivation**, from the structure of US registrations below, and is validated only by the published and observed pairs. It is not a transcription of an official specification.
- **Licence consequence:** the converter is GPL-3.0, so its code must not be copied. We implement the algorithm ourselves from the structure below and use that project only for its published reference pairs (facts).
- **Structure, derived and checked independently (scratch implementation, outside the repo):** N-numbers are `N` + a first digit 1-9 + up to 4 more characters. Letters exclude `I` and `O` (24 letters), at most two letters, and only after 1-3 digits; after 4 digits at most one letter; after 5 digits none. Within a prefix the order is: the number itself, then its letter suffixes in order (`A`, `AA`..`AZ`, `B`, `BA`..), then the children with next digit 0-9. Block sizes: suffix block 601, 5-digit leaf 1, 4-digit node 35 (itself + 24 letters + 10 leaves), 3-digit node 951, 2-digit node 10111, 1-digit node 101711. Nine first digits x 101,711 = 915,399 addresses, ending at `A00000 + 915399 = ADF7C7`.
- **Validation:** Task 1's scratch implementation (outside the repo) reproduced all 38 pairs below. Task 3 re-derived the algorithm in `backend/ghosttrack/icao.py`; `backend/tests/test_icao.py` checks all 38 pairs in both directions, edge cases that follow from the published pairs (N1ZZ is immediately before N10 = a0025a; N9999Z is 10 before N99999 = adf7c7; N1000A = N1000 + 1; N1AA, N10AA, N9ZZ, N99), rejection of invalid registrations, and that address -> N-number -> address round-trips across the block (all 915,399 addresses were checked once during development; the test samples every 7th).
- **Reference pairs for tests.** Source A = the converter README (9 pairs). Source B = the adsb.lol live sample of 2026-11-10 (`r` registration with `hex`); adsb.lol registration data is not the FAA registry itself, so these are secondary evidence, but each independently agreed with the algorithm.

| N-number | ICAO hex | Source |
|---|---|---|
| N1 | a00001 | A |
| N1A | a00002 | A |
| N10 | a0025a | A |
| N1000 | a0070c | A |
| N2 | a18d50 | A |
| N9 | ac6a79 | A |
| N99999 | adf7c7 | A |
| N12345 | a061d9 | A |
| N86QU | abcdef | A |
| N738TJ | a9ea75 | B |
| N383HA | a4671f | B |
| N8806Q | ac210e | B |
| N523DE | a69463 | B |
| N8738K | ac04f5 | B |
| N8779Q | ac13f9 | B |
| N652FR | a894b5 | B |
| N664FR | a8c3a2 | B |
| N893XB | ac5288 | B |
| N438AN | a540e3 | B |
| N415AN | a4e6c0 | B |
| N990AU | add2b6 | B |
| N191UW | a16db9 | B |
| N467AL | a5b3a7 | B |
| N574DZ | a75da8 | B |
| N983JT | adb723 | B |
| N987JT | adc5ff | B |
| N951WN | ad3a5f | B |
| N8650F | abe3c8 | B |
| N824NW | ab40f8 | B |
| N400XB | a4aeb0 | B |
| N916US | acaec8 | B |
| N306XR | a337b9 | B |
| N519DN | a68336 | B |
| N73259 | a9d5a2 | B |
| N542FX | a6dfed | B |
| N223LA | a1ed21 | B |
| N420MP | a4fcc0 | B |
| N956LA | ad4bec | B |

## 4. ADS-B emitter category meanings

- **Source:** https://mode-s.org/1090mhz/content/ads-b/2-identification.html (identification message, TC/CA table; (c) 2021 Junzi Sun, no licence named on the page; fetched 2026-11-10). It is a secondary source; the primary standard is RTCA DO-260B section 2.2.3.2.5.2 (not obtained; NOT CONFIRMED against the standard text). The mapping of type code to set letter (TC 4 to set A, TC 3 to set B, TC 2 to set C, TC 1 to set D) is not stated on that page; it is inferred and agrees with the live sample (`C1` and `C2` on ground vehicles, `A3`/`A5` on airliners).
- `A0` / `B0` / `C0` / `D0`: no category information.
- **Set A (TC 4):** `A1` Light (< 7,000 kg; < 15,500 lb); `A2` Medium 1 (7,000 to 34,000 kg; 15,500 to 75,000 lb, "Small" in US wording); `A3` Medium 2 (34,000 to 136,000 kg; 75,000 to 300,000 lb, "Large"); `A4` High vortex aircraft (the B757 is the usual example); `A5` Heavy (> 136,000 kg; >= 300,000 lb); `A6` High performance (> 5 g and > 400 kt); `A7` Rotorcraft.
- **Set B (TC 3):** `B1` Glider / sailplane; `B2` Lighter-than-air; `B3` Parachutist / skydiver; `B4` Ultralight / hang-glider / paraglider; `B5` reserved; `B6` Unmanned aerial vehicle; `B7` Space or transatmospheric vehicle.
- **Set C (TC 2):** `C1` Surface emergency vehicle; `C2` Surface service vehicle; `C3` Ground obstruction; `C4`-`C7` reserved.
- **Set D (TC 1):** reserved. readsb documents the category field as `A0`-`D7`.
- The lb thresholds for A1/A2/A3/A5 come from search-result excerpts of ADS-B Exchange and airplanes.live pages (both returned HTTP 403 when fetched directly), and agree with the kg thresholds on mode-s.org (7,000 kg is 15,432 lb; 34,000 kg is 74,957 lb; 136,000 kg is 299,824 lb, so the kg values are rounded versions of the lb ones).
- Ground-traffic rule (Review Focus 2): `C1`, `C2` and `C3` are treated as ground traffic.

## 5. Natural Earth

- **Public-domain statement:** https://www.naturalearthdata.com/about/terms-of-use/ (fetched 2026-11-10) says the vector and raster map data is in the public domain, may be used for personal, educational and commercial purposes with no permission needed, and that credit is unnecessary (suggested line: "Made with Natural Earth").
- **Datasets (1:50m):** coastline `ne_50m_coastline`, version 4.0.0 listed on https://www.naturalearthdata.com/downloads/50m-physical-vectors/50m-coastline/ (445.25 KB zip): https://www.naturalearthdata.com/http//www.naturalearthdata.com/download/50m/physical/ne_50m_coastline.zip . Admin-1 `ne_50m_admin_1_states_provinces`, version 5.1.1 on https://www.naturalearthdata.com/downloads/50m-cultural-vectors/50m-admin-1-states-provinces/ (889.9 KB zip): https://www.naturalearthdata.com/http//www.naturalearthdata.com/download/50m/cultural/ne_50m_admin_1_states_provinces.zip . These are the URLs as printed on the site (the doubled `http//` is how the site writes them). The datasets were NOT downloaded in this task; 1:110m was not checked.
- **Decision:** a later task pre-clips the data to the demo region, simplifies it to SVG path data and commits only that derived outline, with a "Made with Natural Earth" credit as a courtesy (not required).

## 6. Ingest and recordings (Task 2)

- **Region:** `socal`, centred on LAX (33.94, -118.41), radius 60 NM, in an allow-listed `REGIONS` dict. The URL is `https://api.adsb.lol/v2/point/{lat:.4f}/{lon:.4f}/{radius:d}`; only numbers reach the path and the host is a constant.
- **Military exclusion is best-effort.** Task 1 found `dbFlags` absent from every record in a live sample. A record is therefore dropped if `dbFlags & 1` (when present) OR its ICAO address is in the US DoD block `AE0000-AFFFFF`. Military aircraft with non-US addresses, or US military aircraft using addresses outside that block, will not be caught. Both paths are unit-tested; dropped records are counted under `military`. Recording coverage check: `dbFlags` presence is not relied on.
- **Sanitisation:** per record, invalid `hex` (not 6 hex chars, so `~` non-ICAO addresses too), non-object records, missing or out-of-range lat/lon (+-90/+-180) and duplicate ICAOs drop the record; out-of-range optional fields (`gs` 0-2000, altitudes -2000..60000, rates, `track`, `seen_pos`, `rssi`, `nic`/`nac_p`) are cleared to `None`; text fields must match strict patterns. All are counted in `dropped_by_reason`. Bodies over 5 MB (Content-Length precheck plus streamed cap), non-JSON and wrong envelopes raise `UpstreamError`; timeout 3 s.
- **Units/semantics:** altitude ft, speed kt, rates ft/min (as delivered). `alt_baro: "ground"` gives `on_ground=True`, `alt_baro_ft=None`. `source` is `mlat` if `type == "mlat"` or `"lat"` is in the `mlat` list; `tisb` likewise; `adsb_icao`, `adsb_icao_nt`, `adsr_icao` give `adsb`; everything else `other`. Snapshot `ts` is `now / 1000` (adsb.lol `now` is milliseconds).
- **Recordings:** `backend/data/recordings/*.jsonl.gz` (one normalised Snapshot per line, 5 s polling, duplicate upstream frames skipped) plus `manifest.json` (name, start/end UTC, snapshots, aircraft, sha256). Recorded with `backend/scripts/record.py`.
- **Capture conditions:** three contiguous 20-minute windows recorded 2026-11-10 08:39-09:39 UTC (00:39-01:39 Pacific, overnight, so traffic is light: 48-60 distinct aircraft per window). They are not spread across times of day. About 59 polls in total returned HTTP errors (likely upstream throttling at a 5 s cadence) and were backed off, so each file holds about 200 of a possible 240 snapshots with occasional 10-20 s gaps. Re-record at other times of day with `scripts/record.py` for a broader false-positive baseline.

## 7. Tracker and detections (Task 3)

- **Tracker (`backend/ghosttrack/tracker.py`).** Tracks are keyed by ICAO address. Each point carries its true position time, `snapshot ts - seen_pos`; with 5 s polling the snapshot time alone would turn late or repeated positions into fake jumps. Positions older than 60 s are stale and never feed kinematics; a position without `seen_pos` has an unknown age and is not used for jumps. Repeated positions (same position time and coordinates as the previous point) are marked and skipped by position checks. A gap over 5 minutes (between appearances or between position times) starts a new segment; detections never compare across segments. Distances are haversine on a sphere (`geo.py`), so the antimeridian and poles need no special cases.
- **Detections (`backend/ghosttrack/detections/`).** GT-001 to GT-010 as pure functions `(track, context)` (GT-009: `(all tracks, context)`) returning an `Evaluation`: findings plus coverage (checks run and not-evaluated reasons). Status is "flagged", "clean" (something was checked) or "not_evaluated" (nothing could be checked); a detection never reports clean for inputs it did not have. Findings carry `detection_id, icao, ts_first, ts_last, severity, confidence, evidence[{field, observed, threshold, ts}], explanation, benign_causes`. Violations of one detection closer than 60 s are merged into one finding (an episode).
- **Thresholds** live only in `detections/catalog.yml` (loaded by `ghosttrack/catalog.py`), including the tracker's timing rules (`common.segment_gap_s`, `stale_position_s`, `repeat_position_time_s`, injected into the tracker as `TrackerSettings`), every confidence value and the evidence cap. Tests edit catalog values and check that behaviour changes, with rationale, false-positive notes, benign causes and references per detection, and per emitter category limits (A1-A7, B1-B7, unknown) with rationale. Limits are generous ceilings, not typical performance. Ground traffic (on-ground reports, C-category, including reports that lack a category on a C-category track) is excluded from airborne kinematics. MLAT/TIS-B data gets a documented 1.5x factor on kinematic thresholds, a 1 NM position allowance per point and 0.6x confidence. Points without a category use the track's most common category.
- **Persistence rules** (all in the catalog, all generic noise suppression): GT-001/002/003 need two violations in an episode; GT-002 ignores a single altitude spike that returns; GT-005 needs two out-and-back jumps faster than Mach 3 in an episode (`min_alternations: 2`; one out-and-back is a single bad decode and is GT-004 only), or two reports in the same snapshot; GT-007 needs 3 reports over 15 s; GT-008 needs an emergency code held for 2 minutes (2 reports for a surface vehicle) and is informational only. GT-009 requires every pair of cluster members to be similar, and reports each address in at most one cluster.
- **False-positive budget scope (controller ruling for Tasks 4/5):** the budget counts only findings of severity medium or high. GT-008 is always `info` (confidence 0.2); GT-003 and GT-010 are `low`. They are shown but not budgeted.
- **GT-007 band:** 1,500 ft pressure band (QNH 958-1068 hPa; a near-surface figure, generous aloft) + 0.08 x pressure altitude (altimeter temperature error of about 4 % per 10 C, for a +-20 C mean column deviation; Task 3 review lowered it from 0.12) + 350 ft geoid.
- **GT-006 is conservative:** it flags only when a valid US N-number converts to a different address, or when both the address block State (Table 9-1) and the registration's nationality-mark State are known and differ. Unknown prefixes, unallocated addresses and ICAO blocks are not evaluated. adsb.lol's `r` comes from adsb.lol's database keyed by the address, so on live data GT-006 mostly finds database or re-registration inconsistencies.
- **Alerts on the committed recordings** (`python scripts/detect_recordings.py`): 0 findings for every detection on all three recordings (socal-a 60 tracks, socal-b 54, socal-c 48). Coverage per recording: 26-30 airborne tracks evaluated by the kinematic checks (the rest are on the ground or surface vehicles), every track evaluated by GT-006 (all clean), 6-7 genuinely new arrivals evaluated by GT-009. Investigation:
  - With every persistence rule relaxed to a single sample, exactly one raw event appears: N224LA (`a1f0d8`, AS50 helicopter, socal-a) reported geometric altitude -925/-975 ft against 1150 ft pressure altitude for its first two reports (5 s), with NIC 1; from the third report NIC rose to 5 and the difference returned to +50 ft. This is a real data anomaly with a known benign cause (GNSS height not yet converged, flagged by the aircraft's own low NIC), so the 3-report / 15 s persistence rule is kept and the event is not alerted.
  - Headroom: scaling all kinematic limits and the GT-007 band down, the first alerts appear at 0.6x (GT-001: widebodies at 510 kt against a 480 kt scaled A5 ceiling), so the ceilings are not tuned to the edge of the data. The largest implied speed between positions was 524 kt (B772, gs 510 kt), the largest vertical rate 4,736 fpm, the largest along-track acceleration 0.57 g (a take-off transition, excluded by rule), the largest geometric/pressure difference outside the startup event +1,300 ft at FL310, 0.30 of its 4,330 ft tolerance (re-measured after the 0.08 change; still 0 alerts). The startup event is 1.09x its tolerance (2,125 ft vs 1,942 ft) for 2 reports, below the 3-report rule.
  - No emergency squawks and no NIC/NACp drops occurred in the recordings, so GT-008 and GT-010 have no real-traffic exercise yet.
  - No threshold was changed to suppress an observed alert. CONCERN: three overnight windows with light traffic (all ADS-B, no MLAT/TIS-B positions) are a thin baseline; daytime recordings (and MLAT-heavy traffic) are needed before the Task 4/5 budget is final.

### Ingest hardening (Task 2 fix round 1)

- **Dedicated opener:** `upstream.make_opener()` builds an `OpenerDirector` with an empty `ProxyHandler({})` (environment proxies ignored), HTTPS only (plain HTTP and file/ftp handlers are not registered), and a redirect handler that refuses every 3xx (`UpstreamError`, status kept). `fetch_raw` also checks the URL is `https://api.adsb.lol`. The base URL can only be changed through the underscore-prefixed `_base_url` argument, which only tests pass (to a 127.0.0.1 test server); no config or environment variable reaches it.
- **Limits:** 3 s per socket operation plus an 8 s wall-clock cap on the whole body read (`read1` is used so a slow drip cannot sit inside one blocking read), 5 MB body cap.
- **Errors carry the HTTP status** (429, 5xx, refused 3xx, or "timeout"). The recorder honours `Retry-After` on 429 (seconds form only, capped at 120 s) and otherwise doubles its delay up to 60 s. Default poll interval is 10 s (5 s hard floor); `--region` is restricted to the allow-list; an existing recording is not overwritten without `--force`; stale or interrupted `.tmp` files are removed.
- **`adsb_other` / `adsr_other` map to `other`:** these types carry a non-ICAO (anonymous or self-assigned) address, whose position quality differs from a regular ICAO ADS-B/ADS-R source, so kinematic tolerances must not treat them as plain `adsb`. They are also usually dropped anyway because such addresses start with `~` and fail the 6-hex check.
- **Stored `ts` bounds:** the envelope `now` must be 0..4e12 ms (1970..2096); this only rejects garbage, the value is still untrusted and falls back to local time when absent or invalid.

## 8. Scenarios and validation (Task 4)

- **Scenarios (`backend/ghosttrack/scenarios/`).** Seven documented, seeded attacks injected into a recorded window (`socal-a`, from 240 s in, after GT-009's warm-up): single ghost (1,350 kt airliner), ghost flood (30 aircraft in a 6 x 5 grid, 1.5 NM apart, same speed and track), ICAO hijack (a real recorded address also reported 45 NM away on alternate polls), position offset (a real track shifted 20 NM for the rest of the flight), teleport (150 NM jump), altitude spoof (geometric 8,000 ft above pressure, band 3,450 ft), registration mismatch (N12345 on a non-matching address; a British mark on a US address). Each states what it simulates, a reference and the expected detection. References are published research only (Costin and Francillon, Black Hat USA 2012; Schaefer et al., ACNS 2013; Strohmeier et al., IEEE Commun. Surveys & Tutorials 2015; Humphreys et al., ION GNSS 2008), cited for the attack class, not for any parameter. Scenarios are data only; nothing is transmitted.
- **Labelling.** `State.injected` (default False; serialised; old recordings lack the key and load as False; `upstream` always sets False). Every state a scenario adds or alters has `injected=True`; the tests compare against the recording to prove nothing else changes. Invented aircraft use addresses `ADF800`-`ADFFFF` (the unallocated tail of the US block, above N99999 = ADF7C7, below the DoD block), which a test asserts never occur in any committed recording. Hijack and offset keep a real recorded address by design.
- **Validation (`ghosttrack/validate.py`, `python -m ghosttrack validate`).** `run_validation()` returns `ValidationResult(scenario, expected, fired, passed, time_to_detect_s)` (plus a collateral count of medium+ findings on untouched aircraft, which also fails the scenario in the CLI). A finding is attributed to a scenario by its address (GT-009 by its member addresses). Time to detect is found by replaying prefixes of the snapshots (binary search) to the first poll at which every expected detection has fired, measured from the start of the injected behaviour (for the teleport, from the jump). The command prints both tables and exits 1 on a failed scenario or an exceeded budget.
- **False-positive budget (controller ruling).** Only findings of severity medium or high count (GT-003/GT-010 low and GT-008 info are listed, not budgeted). Budget = max over recordings of (counted findings / recorded hours) x 1.5, never below 1 per hour, stored as `validation.fp_budget_per_hour` in `catalog.yml`; `test_false_positive_budget` reads every recording in `manifest.json`, so recordings added later count automatically.
- **Measured 2026-11-10:** socal-a 0.33 h, 60 tracks, 0 counted; socal-b 0.33 h, 54 tracks, 0; socal-c 0.33 h, 48 tracks, 0 (no findings of any severity). Max rate 0.00 per hour, so the floor applies: budget **1.0 per hour**. Note that one finding in a 20-minute recording is 3 per hour, so a single false positive on any recording fails the test until the budget is revisited.
- **CONCERN / TODO before release:** the baseline is overnight (about 01:40-02:40 local), ADS-B only, light traffic. Daytime and MLAT-heavy recordings will be added (controller) and the budget must be re-measured with the rule above; the 1.0 per hour floor is a placeholder, not evidence.
- **Scenario results (all 7 pass):** time to detect: single ghost 15 s, flood 0 s, hijack 10 s, offset 0 s, teleport 0 s, altitude spoof 20 s, registration 0 s. Single ghost and hijack also trigger GT-004 (expected: the same positions are also impossible jumps).

## 9. Storage, auth, live cache and demo (Task 5)

- **Auth** (`auth.py`, `config.py`): ported from CMMC-Ready with the `GHOSTTRACK_` prefix and cookie `ghosttrack_session`; same properties (PBKDF2-SHA256 600k, signed JWT in an httpOnly SameSite=Strict cookie that never appears in a response body, algorithm pinned, per-IP login limiter, dummy-hash verification for unknown users, fail closed when unconfigured, guests only in demo mode and never writers). The CMMC-Ready auth tests are ported and run against a small FastAPI app. `python -m ghosttrack create-admin` prints the variables (it writes nothing).
- **Schema** (`db.py`, SQLite, WAL): `snapshots` (rolling 30-minute window per source and region, unique on source/region/ts), `alerts(id, fingerprint, source, detection_id, severity, icao, callsign, first_ts, last_ts, injected, confidence, explanation, benign_json, evidence_json, status, notes)`, `runs`, `meta`. Alerts older than 24 h and snapshots older than the window are pruned on every catch-up or fetch. `source` is `recorded` (demo replay) or `live`.
- **Alert identity.** Fingerprint = source, detection, address and the 5-minute bucket of the episode start. Because a sliding window can move an ongoing episode's start into another bucket, an existing alert of the same source, detection and address that touches the new interval within 120 s is extended instead of duplicated (tested over a window sliding one snapshot at a time). `status` and `notes` are never overwritten by re-detection. Severity medium or high starts as `new`; low and info findings are stored as `info` (shown, not triaged, not budgeted).
- **`injected` on an alert** is 1 when any state of the finding's address(es) in the finding's time range (plus 65 s) is injected; a fleet finding uses its member addresses. Tests prove no alert in a full demo cycle is un-injected.
- **Live view** (`live.py`). One upstream request per region per interval (`GHOSTTRACK_LIVE_INTERVAL_S`, default and floor 10 s), serialised by a lock so concurrent requests share one fetch; a failure is cached for the same interval so an outage is not retried per request. Every success is stored in the window, the detections run over the window and alerts are upserted. Responses carry `Cache-Control: s-maxage=10, stale-while-revalidate=30` (exposed as `live.CACHE_CONTROL` / `traffic_view()["headers"]` for the API layer). On any upstream failure, timeout or 429 the view is the recorded replay with `source: "recorded"` and `notice: "Live feed unavailable; showing recorded traffic"`. The live view overlays the replay's currently injected aircraft (all `injected: true`), so a visitor always sees labelled injected traffic; upstream states are never injected.
- **Demo replay** (`demo.py`). The three recordings are concatenated into one 3,596 s cycle (602 snapshots) that repeats on the wall clock each 3,600 s: snapshot at offset `o` is stamped `floor(now/3600)*3600 + o`, a pure function of the clock, so catch-up is idempotent across the hour boundary and restarts and a cold start equals a stepped run (tested). The seven scenarios run in the cycle every 420 s from 300 s, in the order single ghost, flood, hijack, teleport, altitude spoof, registration mismatch and (last, because it persists to the end of the cycle) the 20 NM position step. Each is applied to the rest of the cycle starting 240 s before its injection time; every added or altered state is `injected`.
- **Precomputed alerts.** `scripts/build_demo.py` runs tracker and detections over the whole cycle (not a rolling window) and writes `data/demo_fixture.json`: 10 alerts per cycle (GT-001, GT-004 x4, GT-005, GT-006 x2, GT-007, GT-009), every one on injected data, none on untouched traffic. `--check` runs in CI and in the suite; the fixture also stores a digest of its inputs (recordings, catalog, schedule), and a stale digest at runtime makes the instance compute the templates itself. An alert is stamped when its episode has ended (`last_ts` <= now), which is later than a live detector would raise it (e.g. the 1,350 kt ghost is on screen about 145 s before its alert); 240 alerts are loaded on a cold start (24 h). The cold-start seed is one `BEGIN IMMEDIATE` transaction with a re-check under the lock (rolled back wholly on failure; concurrent seeders produce exactly one seed).
- **Rolling-window detection vs the fixture.** The live mode computes on the fly over the rolling window; the recorded replay uses the offline full-cycle result. The cycle's seam (last snapshot to first) is never analysed, so recorded aircraft jumping there raise nothing.
- **Fixture determinism.** Floats are rounded to 3 decimals, the GT-009 member list is sorted (it was set-ordered, which made the output depend on the hash seed), and the digest ignores CRLF in `catalog.yml`. NOT CONFIRMED: byte-identical trig results across platforms; the CI `--check` on Linux is the test.

### Task 4 follow-ups

- **Position offset is a step.** The scenario text now says so (a 20 NM sideways step in one report, caught by GT-004 at the jump); the id is unchanged.
- **`gradual_drift` is a documented gap**, in `GAP_SCENARIOS` (not scheduled into the demo, not part of the seven): a real track dragged 0.1 NM per poll up to 20 NM (more than 10 NM within the 20-minute recording). No detection fires on it; `python -m ghosttrack validate` prints `GAP HOLDS`, and a gap that starts firing is reported `GAP CLOSED` and fails validation, so the scenario must then be promoted with an expectation. A consistent slow drag-off within the kinematic limits is a known blind spot of single-receiver plausibility checks (it needs multilateration or an independent receiver).
- **Time to detect resolution = the poll interval.** TTD is measured at snapshot granularity (about 5-6 s in the recordings, 10 s live), so every value is a multiple of the snapshot spacing and a value of 0 means "detected on the first injected report".
- **Budget check.** A test asserts the catalog `fp_budget_per_hour` is at least `fp_budget(measured)`, and `validate` prints a NOTE when the catalog budget is looser than the rule gives. Currently 1.0 per hour (the floor) for both.
