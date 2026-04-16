# Cleanup And Packaging Plan

This plan is intentionally parked until the user explicitly signals to proceed.

## Goals

- Remove unnecessary code, syntax, folders, and files.
- Improve code structure and efficiency without losing any features.
- Preserve all existing functionality during cleanup and packaging.
- Build the Windows executable and installer while hardening the backend packaging flow.

## Planned Steps

1. Freeze existing behavior with a feature-retention checklist.
2. Inventory runtime dependencies before deleting any files or folders.
3. Remove generated artifacts and tighten repository hygiene in low-risk passes.
4. Formalize build inputs for Python and Node so builds are reproducible.
5. Refactor the backend into clearer modules while keeping API behavior stable.
6. Centralize runtime paths into a writable app-data location.
7. Reduce frontend, Electron, and backend coupling where safe.
8. Update packaging so all required models and assets are explicitly bundled.
9. Hide the backend console/process surface where practical and harden release packaging.
10. Verify everything on a clean install before removing legacy build outputs.

## Known Risks To Revisit

- Runtime model assets and frontend resources must remain bundled correctly.
- Writable-path behavior needs to be unified for settings, stats, and other saved data.
- Large backend files should be split carefully to avoid regressions.
- Hardening local binaries improves resistance, but it is not a substitute for moving secrets or sensitive logic off-device.
