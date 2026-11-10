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
- **Licence of the table:** ICAO publishes Annex 10 with copyright reserved; the working paper carries no explicit licence text that was visible. The allocations are factual data. Ship only the minimal derived facts (address range to State name) with attribution to Annex 10 Vol III Part I Appendix to Ch. 9. NOT CONFIRMED: any ICAO licence for redistribution; the residual risk is recorded in THIRD_PARTY.md.

## 3. US N-number to ICAO address mapping

- **Public description:** the US block is `A00001` (N1) to `ADF7C7` (N99999), mapped sequentially. This range and a set of pairs are described in the README of https://github.com/guillaumemichel/icao-nnumber_converter (GPL-3.0; PyPI `icao-nnumber-converter-us`), which calls the mapping "somewhat intuitive, but not convenient to implement". A Java gist, https://gist.github.com/jwoschitz/9a5195b36f0cc7d25455283923df083f, is reported by a search summary to check the same range (not read). The FAA registry pages (https://registry.faa.gov/AircraftInquiry/Search/NNumberResult?nNumberTxt=...) show "Mode S Code (Base 16 / Hex)" but returned HTTP 403 to automated fetch, so FAA pages could not be used to cite pairs.
- **Licence consequence:** the converter is GPL-3.0, so its code must not be copied. We implement the algorithm ourselves from the structure below and use that project only for its published reference pairs (facts).
- **Structure, derived and checked independently (scratch implementation, outside the repo):** N-numbers are `N` + a first digit 1-9 + up to 4 more characters. Letters exclude `I` and `O` (24 letters), at most two letters, and only after 1-3 digits; after 4 digits at most one letter; after 5 digits none. Within a prefix the order is: the number itself, then its letter suffixes in order (`A`, `AA`..`AZ`, `B`, `BA`..), then the children with next digit 0-9. Block sizes: suffix block 601, 5-digit leaf 1, 4-digit node 35 (itself + 24 letters + 10 leaves), 3-digit node 951, 2-digit node 10111, 1-digit node 101711. Nine first digits x 101,711 = 915,399 addresses, ending at `A00000 + 915399 = ADF7C7`.
- **Validation:** this implementation reproduced all 38 pairs below after one correction found by running it (the 4-digit-plus-letter step is `1 + letter index`, not `1 + 25 * index`). The scratch script is at `D:\Projects\_plans\security-five\sdd\ghosttrack\sources\nnum_scratch.py` (not in the repo). The task implementing the converter must re-derive it and test against these pairs.
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
