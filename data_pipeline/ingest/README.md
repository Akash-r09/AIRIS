# data_pipeline/ingest/

One script per external data source, each self-contained:

- `openaq.py` — historical AQI from the OpenAQ archive
- `openmeteo.py` — historical weather from Open-Meteo
- `firms.py` — fire hotspot data from NASA FIRMS
- `osm.py` — land use and road network from OpenStreetMap via Overpass/OSMnx

Every script fails loudly on a bad response rather than silently writing empty data.
