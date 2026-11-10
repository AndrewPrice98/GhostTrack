# Data licence: Open Database License (ODbL) v1.0

The files under `data/recordings/` are captures of aircraft position data obtained from the
adsb.lol public API (https://api.adsb.lol). The JSON files directly under `data/`
(`demo_fixture.json` and `api_fixture.json`) are computed from those captures and contain
recorded positions, callsigns and registrations (the replay frames and the alert evidence). All
of these are a derived database and are redistributed under the Open Database License (ODbL)
v1.0. The scenario frames in them also contain invented aircraft, labelled as injected test data,
which are not adsb.lol data.

Contains information from adsb.lol (https://adsb.lol), which is made available here under the
Open Database License (ODbL) (https://opendatacommons.org/licenses/odbl/1-0/).

- Full licence text: https://opendatacommons.org/licenses/odbl/1-0/
- Source database: adsb.lol, https://www.adsb.lol/docs/open-data/api/ (licence stated by the API).
- Changes made: records were normalised to a fixed schema (units, field subset), invalid values were
  dropped, and aircraft flagged military or in the US DoD address block were removed before storage.
- Share-alike: any derived database you publish must also be offered under the ODbL.

This notice covers the data only. The GhostTrack source code is licensed separately (MIT, see the
repository `LICENSE`).
