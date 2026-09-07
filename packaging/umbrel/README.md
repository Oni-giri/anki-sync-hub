# Umbrel package status

`anki-sync-hub/` is the development package for local umbrelOS testing. Before
an App Store submission, the release workflow must publish a public
`linux/amd64` and `linux/arm64` image, and both image references must be pinned
to the matching version tag and multi-architecture manifest digest.
The all-zero development digest and placeholder pull-request URL are
deliberately non-release values and must be replaced together.

The package deliberately exposes only the app proxy port. Anki protocol and
MCP paths bypass Umbrel's browser-session authentication, but remain protected
by sync credentials and scoped MCP bearer tokens respectively.
