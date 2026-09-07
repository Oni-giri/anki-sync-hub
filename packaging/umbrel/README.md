# Umbrel package status

`anki-sync-hub/` is the package for local umbrelOS testing and an eventual
official App Store submission. The public image is available for both
`linux/amd64` and `linux/arm64`, and every service is pinned to the immutable
multi-architecture manifest digest. The pull-request URL remains a placeholder
until an official Umbrel App Store submission is opened.

The package deliberately exposes only the app proxy port. Anki protocol and
MCP paths bypass Umbrel's browser-session authentication, but remain protected
by sync credentials and scoped MCP bearer tokens respectively.
