# BarkWatch official calendar relay

This directory is designed to be copied into a separate GitHub repository.
That repository contains only three official public calendar documents' hashes
and parsed FOMC/CPI/NFP dates.  It must not contain Bark endpoints, VPS data,
database URLs, source code, order IDs, SSH keys or trading state.

The workflow refreshes every six hours.  Each generated snapshot expires after
36 hours.  BarkWatch additionally enforces a hard 48-hour maximum age and
fails closed when the snapshot is absent, malformed, incomplete or stale.

The application does not read GitHub credentials.  A deployment-side timer can
run the bounded downloader inside the app container:

```text
python -m crypto_shadow.relay_refresh \
  --url https://raw.githubusercontent.com/<OWNER>/barkwatch-official-calendar-relay/main/official-calendar-relay.json \
  --target /data/macro/official-calendar-relay.json
```

The downloader accepts only the pinned public GitHub path, refuses redirects,
limits the response size, validates the full snapshot before use and replaces
the last file atomically.  A failed refresh leaves the prior valid file intact.
