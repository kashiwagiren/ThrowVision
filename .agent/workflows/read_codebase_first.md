---
description: always read all source files before making any changes to the ThrowVision codebase
---

# Rule: Read All Source Files Before Any Change

// turbo-all

Before making ANY code change, addition, deletion, or fix in this project, you MUST first read and analyse ALL of these source files to fully understand the current state of the code:

1. Read core backend files:
   - `c:\Users\Kieth\Downloads\Test\config.py`
   - `c:\Users\Kieth\Downloads\Test\calibrator.py`
   - `c:\Users\Kieth\Downloads\Test\detector.py`
   - `c:\Users\Kieth\Downloads\Test\scorer.py`
   - `c:\Users\Kieth\Downloads\Test\server.py`
   - `c:\Users\Kieth\Downloads\Test\debug.py`
   - `c:\Users\Kieth\Downloads\Test\annotate.py`
   - `c:\Users\Kieth\Downloads\Test\board_annotator.py`
   - `c:\Users\Kieth\Downloads\Test\board_profile.py`
   - `c:\Users\Kieth\Downloads\Test\main.py`

2. Read frontend files:
   - `c:\Users\Kieth\Downloads\Test\frontend\index.html`
   - `c:\Users\Kieth\Downloads\Test\frontend\app.js`
   - `c:\Users\Kieth\Downloads\Test\frontend\style.css`

3. Only AFTER reading all files:
   - Identify exactly which files are affected by the requested change
   - Confirm the current logic in those files before proposing any edit
   - Make the minimum necessary change — do NOT refactor or restructure code that is unrelated to the fix
   - Do NOT duplicate logic that already exists elsewhere in the codebase

## Why This Matters

Previous bugs were introduced because changes were made without fully understanding existing logic (e.g. duplicating calibration rescaling that `BoardCalibrator.load_cached` already handles, and breaking camera open/close by not reading `_do_close_cameras` and `open_camera` together before editing).

Always read first. Change second.
