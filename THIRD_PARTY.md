# Third-party data and licences

The GhostTrack code is MIT-licensed (see LICENSE). Data below keeps its own licence.

| Item | Source | Licence | Use in GhostTrack |
|---|---|---|---|
| adsb.lol aircraft data | https://api.adsb.lol (`/v2/point/{lat}/{lon}/{radius}`) | Open Database License (ODbL) 1.0, https://opendatacommons.org/licenses/odbl/1-0/ (stated by the adsb.lol API description and https://www.adsb.lol/docs/open-data/api/) | Live fetch (server side only) and recorded captures. Captures committed under `data/` ship under the ODbL with `data/LICENSE-ODbL.md`. Attribution: "Contains information from adsb.lol (https://adsb.lol), which is made available here under the Open Database License (ODbL)." shown in the footer, the map and the README. |
| Natural Earth 1:50m coastline and admin-1 | https://www.naturalearthdata.com/ | Public domain (https://www.naturalearthdata.com/about/terms-of-use/); credit not required | Pre-clipped, simplified outlines drawn as SVG. |
| ICAO 24-bit address allocation (Annex 10 Vol III Part I, Appendix to Ch. 9, Table 9-1) | ICAO; public reproduction at https://www.icao.int/sites/default/files/ESAF/APIRG/APIRG-26/FR/WPs-FRE/APIRG26-WP03F12-App-Guidelines-for-ICAO-24-Bits-Adresses-Assignment.pdf | ICAO copyright; no redistribution licence confirmed. The allocations are factual data. | Our own range table transcribed from Table 9-1 (address range to State), cited. Hex lists from GPL projects (tar1090 / dump1090) are used only to cross-check, never copied. |
| US N-number to ICAO address mapping | Described in https://github.com/guillaumemichel/icao-nnumber_converter | GPL-3.0 (its code is not used) | Own implementation; the repository's published pairs are used as test facts only. |
| ADS-B emitter category meanings | https://mode-s.org/1090mhz/content/ads-b/2-identification.html; standard RTCA DO-260B | Site: copyright Junzi Sun, no licence named; standard: RTCA | Short factual lookup table written in our own words. |
| readsb JSON field documentation | https://github.com/wiedehopf/readsb/blob/dev/README-json.md | Not reviewed | Field names, units and `dbFlags` bit meanings cited as facts. |
