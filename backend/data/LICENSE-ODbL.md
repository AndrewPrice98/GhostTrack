# Data licence: Open Database License (ODbL) v1.0

The files under `data/recordings/` are captures of aircraft position data obtained from the
adsb.lol public API (https://api.adsb.lol). They are a derived database and are redistributed
under the Open Database License (ODbL) v1.0.

Contains information from adsb.lol (https://adsb.lol), which is made available here under the
Open Database License (ODbL) (https://opendatacommons.org/licenses/odbl/1-0/).

- Full licence text: https://opendatacommons.org/licenses/odbl/1-0/
- Source database: adsb.lol, https://www.adsb.lol/docs/open-data/api/ (licence stated by the API).
- Changes made: records were normalised to a fixed schema (units, field subset), invalid values were
  dropped, and aircraft flagged military or in the US DoD address block were removed before storage.
- Share-alike: any derived database you publish must also be offered under the ODbL.

This notice covers the data only. The GhostTrack source code is licensed separately (MIT, see the
repository `LICENSE`).
