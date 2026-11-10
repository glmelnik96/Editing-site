# Graphite interface implementation plan

> **For agentic workers:** use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** apply the approved simple dark interface to the existing application.

**Architecture:** edit the existing shared CSS and home screen presentation. Preserve desktop editor layout, controls, media processing and APIs. Reuse the current checkout on `codex/graphite-interface`; preserve the pre-existing untracked `web/layout-preview.html`.

**Tech stack:** TypeScript, CSS, Vite, Vitest.

**Spec:** approved design in this conversation, represented by `C:/Users/Глеб/.codex/visualizations/2026/11/10/01a1c530-7325-79e1-8ace-6b85ce1c41cd/outline-editor.html`.

## Global constraints

- Dark neutral graphite, monochrome surfaces, green accents, 2 px corner radius.
- No nested card chrome, decorative numbering or redundant copy.
- Preserve functional labels, errors, warnings, save status and accessibility.
- No commits, pushes or publishing without explicit user authorization.
- This is a reversible presentation change: use existing regression tests and browser checks; do not add tests mirroring CSS declarations.

## Review focus

- Muted text and field borders retain adequate contrast on the lighter graphite surfaces.
- Narrow editor layouts and long file names remain usable.
- Hidden panels remain hidden; selected states and focus remain visible.
- Export, subtitles, file operations and project routes retain their controls.
- Authentication screen and overlays retain opaque surfaces and readable text.

## Task 1: Shared styling and simpler home

**Files:** `web/src/style.css`, `web/src/home.ts`, `web/src/home.test.ts`, `web/src/style-tokens.test.ts`, `web/src/editor.ts`, `web/src/render.ts`, `web/src/subtitles.ts`.

- [x] Update palette and contrast tokens, remove the login radius exception, flatten nested panels into separated sections and file/project lists into rows.
- [x] Remove home step numbers and redundant instructions; shorten the source selection hint without losing its meaning.
- [x] Update existing assertions affected by approved presentation changes. Replace literal color assertions with actual contrast checks.
- [x] Run `npm test` and `npm run build` in `web`.

## Task 2: Review the real UI

- [x] Start the existing Vite server and inspect representative screens with local synthetic API responses, without accessing production data.
- [x] Check desktop and narrow layouts, tabs, selection, empty editor, projects and files.
- [x] Review the diff, record verified results and limitations, and stop the owned preview server.

## Execution notes

- The design has already been approved in the conversation. Proceed inline without another approval round.
- No new dependencies or changes to user data are required.
- Browser QA found a pre-existing zero-width video monitor on narrow screens. Added a stacked layout below 900 px and an optional `stacked` argument to `stageBox` (`web/src/layout.ts`); the new layout test first reproduced the zero size. Desktop behavior is unchanged.
- Final validation: 365 tests across 46 Vitest files pass; TypeScript and Vite build pass; `git diff --check` passes.
- Browser QA: editor at 320/390/899/900/1024/1440 px; home, projects, files, converter, new project and settings at 390/1440 px. No document horizontal overflow. Tested source/subtitle/render tabs, cue selection and 2 px login button corners.
- Independent static review found a height-only resize issue in the stacked layout. Browser reproduced 428 px remaining above a new 400 px height limit; adding a resize listener with stop-time cleanup corrected it to 400 px.
- Limits: browser used synthetic API data without actual media; no live encoding, authentication or production checks were performed. No commit, push or deployment.
- QA screenshots/logs retained under `C:/Users/Глеб/.codex/visualizations/2026/11/10/01a1c530-7325-79e1-8ace-6b85ce1c41cd/graphite-qa/`.

## Revision: reduce visible controls

User feedback: the interface still feels overloaded. Reduce the number of controls shown at once while preserving editing operations.

- [x] Hide the inspector until a clip, sound, overlay or subtitle is selected; give its space to the video monitor.
- [x] Keep undo, playback, split and delete visible. Move redo, duplicate, timeline zoom and keyboard help into “Ещё”. Close on Escape and outside clicks; retain native keyboard activation.
- [x] Move output format into a collapsed header menu. Hide redundant global navigation inside the editor.
- [x] Hide source actions until a file is chosen. Collapse trim/transcript by default, preserving existing browser preferences. Shorten action labels and repeated explanations.
- [x] Hide empty overlay, music and clip-audio rows, including their headings.
- [x] Review and validate changes. Static review found hidden refusal messages and intercepted Space activation; both fixed and verified in the browser. Narrow-screen QA found the format menu extending beyond 320 px; anchored it to the header on narrow screens.

Validation: 366 tests across 46 files pass; TypeScript and Vite build pass. Browser checks at 320/390/899/900/1440 px found no horizontal document overflow. Verified selection/inspector, empty project → add → delete → undo, duplicate/undo, conditional overlay/music rows, fold defaults, menu Space/Escape/outside activation and visible refusal messages. No page errors. Browser used synthetic API data without actual media, so encoding/playback are outside this verification. Screenshots retained in the adjacent `graphite-simple-qa/` folder. No commit, push or publication.

## Continuation: simplify secondary panels

- [x] Render: keep format, resolution, quality and start visible; disclose detailed output settings, omit irrelevant video/bitrate fields, retain estimates and active subtitle state.
- [x] Inspector: show size only for placements that allow it, omit redundant image/audio explanations.
- [x] Subtitles: shorten state copy without removing errors or the warning for clips missing transcripts.
- [x] Check browser interactions, narrow layouts, render submission, existing tests/build and review the changes. Preserve earlier artifacts and local-only scope.

Validation: 366 tests and build pass. Browser verified 320/390/899/900/1440 px layouts, native details keyboard activation, target bitrate visibility, MP4/M4A/WebM transitions preserving options, aspect/FPS/burn controls, render POST payload and queued UI, overlay size visibility/value preservation, cue editing/split/remove. Independent static review found no additional significant issues. Synthetic API data only; actual encoding not tested. Screenshots retained in `graphite-panels-qa/` beside previous QA folders.

## Pre-release verification — 2026-11-10

Result: no technical release blockers found in this change. Changes remain uncommitted on `codex/graphite-interface`; the standard deploy script pulls `origin/main`, so integration into main is still required before deployment. No commit, push or deployment was performed.

- Frontend: 366/366 tests, TypeScript and production Vite build pass.
- Backend: 910/910 tests, including real ffmpeg integration, pass; no skipped tests. Two dependency deprecation warnings remain.
- Ruff and `git diff --check` pass. Removed two redundant `int(round(...))` casts in `server/media/run.py`; full backend tests passed after the change.
- The first backend run on C: had 34 failures because free disk space was below the configured 10% threshold. Repeating against a fresh temporary directory on D: passed all tests without weakening storage checks.
- Local Chrome ran the production frontend against the actual FastAPI application, SQLite and CPU worker, with an isolated test user/database and generated video/audio. Verified chunk upload, analysis/proxy, create project, add clip, playback, duplicate/undo, properties, help/Escape, overlay/audio, cue editing/autosave, format switching, FPS persistence, render and authenticated download. All 64 API calls succeeded; no JavaScript or HTTP errors.
- The downloaded render was 5 seconds, 640×360, H.264 at 30 fps with AAC, 803593 bytes. Full ffmpeg decoding completed without errors.
- Production read-only checks: `/healthz` returned `status=ok`, database healthy, worker seen 0 seconds ago and 83.3% disk free. The 41 OpenAPI routes and all component schemas match the local API after excluding descriptive documentation fields.
- Independent read-only review found no critical/important regressions. Restored a concise request to check subtitles on records lacking transcripts; wording does not falsely claim existing manually authored cues are absent.
- Detailed logs and JSON results: `.cache/release-check-20261110-1/` (ignored test artifacts). Temporary backend data: `D:/Codex-QA/editing-site-20261110-1/`. No production credentials/data used; local session stayed inside the local browser test process.

Limits: external OAuth provider login and paid speech transcription were not invoked. Production was checked read-only; deployment health and authenticated smoke checks must be repeated after the actual rollout.
