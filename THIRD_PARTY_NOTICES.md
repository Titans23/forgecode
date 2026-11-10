# Third-party notices

The root MIT license covers ForgeCode-owned code that its contributors can license.
It does not replace the licenses of dependencies, bundled third-party files, public
benchmark tasks, datasets, model outputs or user-supplied plugins and business data.

- The DSH dependencies `@deepseek-ai/dsh-sandbox-local`, `@deepseek-ai/dsh-subprocess-local`
  and `@deepseek-ai/dsh-sandbox-windows-acl` at `0.2.1-alpha.1`, plus their Cordis runtime,
  are Copyright (c) 2026 DeepSeek, MIT. Their upstream LICENSE files are retained in the
  installed dependency and in bundled `forge/_sandbox/node_modules` assets.
- Context compaction defaults reference [pi](https://github.com/earendil-works/pi)
  at `e65b3b88c08412bb8ffbd21fb5dc5756064a6e5a`. Request-pressure pruning references
  [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) at
  `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`; implementation boundaries are in
  `docs/implementation/architecture.md`. These references do not imply ownership
  of either upstream project.
- The optional comparison installs the public pi SDK version 1.5.0 with npm's
  dependency lock. Pi's MIT notice credits Mario Zechner (2025); it and all
  transitive dependency notices remain with the installed packages. ForgeCode
  comparison fixtures use public APIs and do not vendor or modify pi's core.
- Python and Node dependencies retain their own package licenses. The ordinary
  SDK does not require the optional native bridge. Linux system tools and official
  benchmark images are external dependencies; ForgeCode's MIT license does not
  relicense them. Benchmark inputs remain subject to their original terms.

Upstream MIT texts for the two referenced agent projects are included in `licenses`.
Dependency versions are recorded in uv.lock, package-lock.json and the optional
benchmark/comparison/package-lock.json; generated dependency inventories remain
build/CI artifacts.
