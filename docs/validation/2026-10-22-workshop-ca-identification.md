# WH3 Workshop identification and publishing fixes

The official legacy CA Launcher identifies modern Workshop items using two
key-value tags, `pack_name` and `game_version`. The uploader previously set
the content and ordinary category tags without these identifiers.

WH3 uploads and updates now include both identifiers in the same Steam update
as the content. The Pack name comes from the actual upload directory; the
version is read from the selected game executable's Windows `FileVersionMS`.
Updates also repair older items that lack either field. Existing key-name
case variants are replaced while unrelated key-value tags and categories are
preserved. Fresh Steam readback must confirm both fields before publication
is reported as successful. Other supported games keep their existing behavior.

The native Steamworks module exposes key-value read/update support and keeps
the previous localized-update support. Its pinned source patches and build
instructions are in
[LOCAL_PATCH.md](../../steam_runtime/steamworks/LOCAL_PATCH.md).

Validation completed before release preparation:

- Regressions first reproduced missing identifiers on new uploads and old-item
  updates, including case variants. The repaired uploader passes those cases.
- Additional checks cover ownership, game isolation, native capability checks,
  metadata-only repair idempotence, and failed readback.
- Fourteen existing owned items were repaired through Steam and read back.
  The actual CA Launcher 3.25.3 list API then returned all fourteen with valid
  Pack paths. All 48 comparison items were returned, while the other 34 items'
  key-value tags stayed unchanged.
- Desktop UI rendering was not checked. Verification used Steam and the
  official launcher's native list API.

The related local-MOD update-eligibility fix restores the author from the
existing game-specific Workshop cache after a saved publishing link is
restored. A publishing link alone is not treated as ownership proof; the
current account must still own the Workshop item. See
[update eligibility validation](2026-10-22-workshop-update-eligibility.md).

Release preparation reuses the completed regressions. The 1.2.9 package is
built with the existing release script and `-SkipTests` as requested.
