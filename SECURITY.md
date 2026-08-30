# Security

status: `0.1-draft-frozen`

## Network

HttpFetcher allows only `http` and `https`. It refuses `file://`, `ftp://`,
credentials-in-URL, redirects to blocked hosts, redirect loops, responses over
`max_bytes`, and decompression bombs (compressed-to-uncompressed ratio cap).

Every DNS result must be a global unicast address. IPv4-mapped and transition
addresses, multicast, private, loopback, link-local, site-local, reserved and
unspecified ranges are refused. The fetcher connects directly to the validated
socket address, retains the original hostname for HTTPS SNI/certificate checks,
and does not use environment proxies. Local fixture fetching is a separate
`FixtureFetcher` and never opens a socket.

Do not bypass paywalls, login walls, CAPTCHAs or access control.

## Isolation

Extraction may read only:

- the frozen EvidenceBundle members;
- the declared profile;
- hashes listed in `input_manifest`.

It must not read the git worktree, `CURRENT_STATE`, GDD, design-map, or any
file whose artifact id is not in `allowed_context_artifact_ids`.

## Prompt injection

Parser does not execute scripts or HTML-embedded instructions. Extractors treat
source text as untrusted. Source-content injection fixtures must not change the
neutral claim set.

Forbidden export tokens include project-design vocabulary:
`M-1`, `NOT_A_GAP`, `current_holder`, `current_controller`, `COVERED`,
`PARTIAL`, `ABSENT`, `REJECTED_BY_CONSTRAINT`, `design-map`.

## Paths

Repository-relative paths only. Absolute paths, `..` and host-prefixed paths
fail validation.

## Logs

Fetcher must not log cookies, tokens or `Authorization` headers.
