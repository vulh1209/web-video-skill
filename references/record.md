# Recording a take

`scripts/record.py` drives Chromium through the storyboard. Three modes share the same action code, so a passing rehearsal predicts a passing take.

| Mode | Video | Use |
|---|---|---|
| `--discover` | no | before each step, prints visible interactive elements with a suggested selector (also `discover.json` in the cwd) |
| `--rehearse` | no | runs everything fast; prints `ok step N` or `FAIL` plus the element list; for bug-report prints whether the bug step was reached and which errors fired |
| take (`--slug` or `--out`) | yes | full speed with cursor glide; writes the run folder |

Common options: `--headed` (watch it), `--timeout 15000` (slow apps), `--no-flash` (skip the sync flash; offset becomes unknown).

## What a take does

1. New context at the storyboard viewport with `record_video`, HAR (`embed` bodies in bug-report, `omit` in feature-demo), tracing (screenshots + snapshots), optional `storage_state`.
2. Injects a DOM cursor (arrow SVG) that follows `mousemove` and a red ripple on `mousedown`. Playwright video has no cursor of its own.
3. **Sync flash:** dark page 0.7 s, then magenta; logs a `flash` event. `calibrate.py` finds the first magenta frame and stores `video_offset = video time − event time` (typically −0.05 to +0.2 s).
4. Runs steps. Clicks: wait visible → exactly one match → scroll into view → centre inside viewport → `elementFromPoint` hits the element (not covered) → glide (12-45 mouse steps) → 250 ms → log `click` → click. Any failed check stops the take (exit 2) and keeps the artifacts.
5. Logs errors as they happen: console `error`, `pageerror`, HTTP ≥ 400, failed requests (except aborted navigations).
6. On a `bug: true` step logs `bug` after the settle wait, with the box of `bug_target` or the last clicked element.
7. Closes the context in `finally` (flushes video and HAR), moves the video to `raw.webm`, ffprobes it (exit 3 if missing or empty), calibrates, writes `meta.json`.

## Run folder

```
video-out/<slug>/v<N>/
  storyboard.yaml   copy used for this take
  raw.webm          VP8 ~25 fps at the viewport size
  events.json       {version, mode, viewport, video_offset, offset_method, secret_selectors, events[]}
  console.json      every console message with t
  net.har           all requests (bodies in bug-report)
  trace.zip         Playwright trace: open with `npx playwright show-trace trace.zip`
  meta.json         status ok/aborted, failed_step, versions, raw probe, offset, error count
```

Event kinds: `flash`, `flash_end`, `step {i, caption, hidden, bug}`, `step_end {i}`, `nav {url}`, `click {x,y,w,h,target,bbox}`, `hover`, `type {…, text (masked if secret), t_start}`, `fill`, `press {key}`, `select {…, value}`, `scroll {dy}`, `expect {target,bbox}`, `error {source: console|pageerror|http|network, …}`, `bug {i,bbox,errors_so_far}`, `fail {i,reason}`, `end`. `t` is seconds since the page was created (monotonic clock).

## Limits to tell the user

- `record_video` is about 25 fps VP8 at CSS-pixel size. Raising output fps only duplicates frames; zoom above 1.5x shows blur. For a crisper client video see `references/hifi-capture.md`.
- The DOM cursor cannot appear over native `<select>` lists, file pickers, print dialogs, browser permission prompts or OS windows. The video shows the result, not the open list. Say so in the caption, or record that part with the OS recorder (below) and edit it with `edl.py --edit-only`.
- Cross-origin iframes get the cursor only when the mouse is inside them.
- Animations and spinners play in real time; idle parts are sped up later, so do not add long `wait`s "for the video".
- Data changes during a take (the fixture app keeps added invoices until restart). Reset or seed data in hidden steps so every take looks the same.

## Recording something Playwright cannot drive

Record with the OS, always with a time limit so it stops on its own, then
`edl.py --edit-only out.mp4 --out video-out/<slug>/v1`:

| OS | Command |
|---|---|
| macOS | `screencapture -v -V 30 -k out.mov` (needs Screen Recording permission for the app that runs it) |
| Windows | `ffmpeg -f gdigrab -framerate 30 -t 30 -i desktop -pix_fmt yuv420p out.mp4`, or the user presses Win+Alt+R (Game Bar) |
| Linux (X11) | `ffmpeg -f x11grab -framerate 30 -t 30 -i :0.0 -pix_fmt yuv420p out.mp4` |

There is no event log, so no captions, zoom or bug panel: only idle speed-up and trimming.
