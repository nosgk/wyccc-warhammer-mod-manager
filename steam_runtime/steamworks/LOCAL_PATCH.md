# Local Steamworks patch

The bundled native module is based on:

- `ceifa/steamworks.js` commit `80c5fd7c4958ba90cf87403c3f967b8a6d748473`
- `Noxime/steamworks-rs` commit `fbb79635b06b4feea8261e5ca3e8ea3ef42facf9`

The local patches add localized updates and CA Workshop identification:

1. `steamworks-rs::UpdateHandle::update_language` calls
   `SteamAPI_ISteamUGC_SetItemUpdateLanguage`.
2. `steamworks.js::workshop::UgcUpdate` accepts `language` and applies it before
   title and description.
3. `workshop.supportsUpdateLanguage()` identifies a compatible bundled module,
   preventing silent fallback when an older binary is accidentally restored.
4. `UgcUpdate` accepts `keyValueTags` and `removeKeyValueTags`; both are applied
   to the same update handle as the content, before `SubmitItemUpdate`.
5. `WorkshopItemQueryConfig.includeKeyValueTags` requests key-value data and
   `WorkshopItem.keyValueTags` exposes it for case-aware replacement and readback.
6. `workshop.supportsKeyValueTags()` prevents a runtime lacking these fields from
   silently ignoring the requested CA identification.

It was built for `x86_64-pc-windows-msvc` with Rust 1.97.1 and
`-C target-feature=+crt-static`, avoiding a new VC runtime DLL dependency. The matching
`steam_api64.dll` is the redistributable shipped by the pinned `steamworks-sys`
source; the module and redistributable must be updated together.
The existing redistributable remains byte-identical to that pinned SDK.

The complete diffs, including the localized-update patch, are retained in
`patches/steamworks-js.patch` and `patches/steamworks-rs.patch`. Rebuild with:

```powershell
python scripts/build_steamworks_native.py
```

This requires Rust's MSVC target and Visual Studio C++ Build Tools. It checks
both source revisions, refuses unrelated source changes, applies the patches,
uses the pinned Cargo lockfile, and updates only this repository's native module.

Current SHA-256 values:

- `steamworksjs.win32-x64-msvc.node`:
  `9B1B1E448E2C5CE4E510F8F63C3E8EDC49FE66B111F8EC1F1D2A2511A9690E31`
- `steam_api64.dll`:
  `1ADD7F151FA644870A735AE86E68D1F019F296130D8E7C0A7ED3ECC7482DCCBC`

Node loads the module through the stable N-API ABI. The previous GNU-target
binary used the same historical `msvc` filename.

The dependency-query build is intentionally isolated from this localized-update
build. Its provenance and hashes are documented in `../steamworks_dependencies/README.md`.
