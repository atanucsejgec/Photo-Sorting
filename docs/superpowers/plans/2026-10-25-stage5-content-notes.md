# Stage 5: Content and Notes & Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two modes that use OpenCLIP:
- **Content:** each photo goes to its best category folder. The categories are an editable list in Settings.
- **Notes & Pages:** photos of handwritten notes, book pages, printed documents and receipts go to `Notes & Pages/<kind>/`.

In both modes, unsure photos are reviewed first, and photos that fit nothing stay put. Both go through the existing summary, Go, Done and Undo flow.

**Architecture:**
- **ClipEngine.** `clip.py` turns a photo into a 512-number OpenCLIP ViT-B/32 image embedding and a text into a text embedding. It uses ONNX Runtime through `ai/runtime.make_session` and the `tokenizers` package for the CLIP tokenizer.
- **Cache.** Image embeddings are cached in `.photosort/cache.db` (kind `"clip-v1"`). Changing the category list never re-reads photos.
- **Classifying.** Zero-shot classification (softmax of 100 × cosine over the prompts) happens at plan time in `modes/content.py` and `modes/notes.py`. Each gives a sure/unsure/none verdict per photo.
- **Review.** One shared review page (`ui/category_review_page.py`) shows the unsure photos in a grid. Each has a category picker set to the suggestion, with an option to leave it put.

**Tech Stack:** The stage 3 stack, plus `tokenizers>=0.23.3` (already added on this branch, at 3 MB). The model is immich-app `ViT-B-32__openai`, ONNX, and is already downloaded to `~/.local/share/photo-sorter/models/ViT-B-32__openai/` on this laptop.

**Spec:** `docs/design.md` §5.5 (Notes & Pages) and §5.7 (Content), with §2, §3, §4, §6 (Settings: content categories), §8 and §9.

**Measured by the controller before planning** (CLIP spike: 120 photos from the test copy, RTX 3050 Ti):
- **Model I/O.** Both models have a **fixed batch of 1**.
  - Visual input: `image` float32 `[1,3,224,224]`. Output: `embedding` `[1,512]`.
  - Textual input: `text` **int32** `[1,77]`. Output: `embedding` `[1,512]`.
- **Preprocessing** (`visual/preprocess_cfg.json`):
  - resize the shortest side to 224 with bicubic, then centre-crop 224;
  - RGB/255, then subtract the mean `[0.48145466, 0.4578275, 0.40821073]` and divide by the std `[0.26862954, 0.26130258, 0.27577711]`.
- **Tokenizer.** `tokenizers.Tokenizer.from_file(textual/tokenizer.json)` gives ids that already include `49406 … 49407`. Pad with 0 to 77 and truncate to 77, keeping 49407 last.
- **Speed.** 181 ms per photo on the GPU, decoding the full-size JPEG. Use the JPEG draft mode for speed.
- **Zero-shot quality.** With prompts `"a photo of {category}"`:
  - photos at softmax ≥ 0.8 were right in 8 of 8 checked;
  - photos at 0.3–0.6 were often ambiguous (people in snow went to "Events");
  - a real document scored 0.97 for "a photo of a printed document".

## Global Constraints

- **Earlier rules still hold.** Everything in the stage 1–3 Global Constraints still applies:
  - never delete, never overwrite, journal before every move;
  - relative POSIX paths, `naming.fold` folder matching, `safe_name`, no symlinks;
  - subfolders off by default, nothing moves before Go;
  - copies only during development; all tunables live in `photosort/thresholds.py`.
- **Photos never leave the machine.** The only network use is the model download from the URLs in `ai/models.py`, after the owner says yes (`ui/model_download.ensure_models`).
- **Model files** (in `models_dir()`; the sizes and SHA-256 sums below were checked after downloading):
  - `ViT-B-32__openai/visual/model.onnx`: 351,613,724 bytes, sha256 `33a3df41ceef21acdf371af00f6dd0456ec1f9eba24d03a7720f9c3734e40859`;
  - `ViT-B-32__openai/textual/model.onnx`: 254,193,396 bytes, sha256 `b80cf0af751533a6712d92247f0ddc0c95208748bc59a1a27f33e67be6864e3b`;
  - `ViT-B-32__openai/textual/tokenizer.json`: 3,642,073 bytes, sha256 `6d9109cc838977f3ca94a379eec36aecc7c807e1785cd729660ca2fc0171fb35`;
  - all three come from URLs of the form `https://huggingface.co/immich-app/ViT-B-32__openai/resolve/main/<path>`.
- **Videos are skipped** in both modes (§4). Unreadable photos are counted, stay put and are never cached.
- **Content categories (Settings `content_categories: list[str]`).**
  - Default, in this order: `Food`, `Pets & Animals`, `Nature & Landscapes`, `Selfies & People`, `Buildings & City`, `Vehicles`, `Events & Celebrations`, `Text & Documents`.
  - Valid: 1–30 names, each non-empty after `safe_name`, unique by `naming.fold`. An invalid list in an old settings file gives the default.
  - The folder is `naming.fit_name(name, FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES)` at the root. A category named like a skipped folder gets the `_` prefix from `fit_name`.
- **Notes & Pages folders:** `Notes & Pages/Handwritten`, `Notes & Pages/Book Pages`, `Notes & Pages/Printed Documents` and `Notes & Pages/Receipts`.
- **Verdicts**, all limits in thresholds:
  - **Content.** `p` is the softmax probability of the best category over the categories and the background prompt `CONTENT_BACKGROUND_PROMPTS` (`"a photo of something else"`). A photo the background prompt fits better than every category stays put. `p ≥ CONTENT_SURE` (0.60) is sure. `CONTENT_UNSURE` (0.40) ≤ p < `CONTENT_SURE` is unsure. Below that, the photo stays put. Tuned on 732 real photos (2026-10-25): 319 sure, 221 unsure, 192 stay.
  - **Notes.** Softmax over the 4 paper prompts plus the non-paper prompts `"a photo of people"`, `"a photo of a place"`, `"a photo of food"`, `"a photo of an animal"`, `"a photo of an object"`, `"a screenshot of a phone screen"`, `"a photo of a sign"`, `"a photo of a bank card"`, `"a photo of a computer screen"`, `"a photo of an old printed photograph"`. `paper` is the sum of the 4 paper probabilities, and the kind is the best of the 4. `paper ≥ NOTES_SURE` (0.80) is sure. `NOTES_UNSURE` (0.50) ≤ paper < `NOTES_SURE` is unsure. Below that, the photo stays put. Tuned on the same 732 photos: 13 sure (all paper), 2 unsure.
  - **Prompts.** Content gives each category one prompt per part of its name: the name is split on `" & "` and on `" and "` (whole word, any case), each part stripped and empty parts dropped (the whole name if nothing is left), and each part's prompt is `f"a photo of {part.casefold()}"` (`"Selfies & People"` → `"a photo of selfies"`, `"a photo of people"`). The category's text embedding is the mean of its parts' embeddings, made unit length again (`content.prompt_sets`, `content.category_embeddings`). Notes uses `"a photo of handwritten notes"`, `"a photo of a page from a book"`, `"a photo of a printed document"` and `"a photo of a shop receipt"`.
  - The tuning is the real-data task's job.
- **Already in place:** a photo whose parent folder already equals its target folder (compared with `fold`) counts as unchanged. The same rule as stage 4.

## Review Focus

1. **The owner edits the category list after a run** (renames "Food" to "Meals", adds "Documents"). The rerun uses the cached embeddings, with no re-read of photos, and the new names. Photos already in `Food/` are re-judged like any other photo when subfolders are on. Test in Task 2: the cache hit, plus a category change gives new folders.
2. **A category named "Bad Photos", ".x", "a/b" or 200 characters long, a duplicate in another case, or an empty list.** The editor refuses it, or it is cleaned; the settings loader never crashes. Test in Task 4 (editor) and Task 2 (folder name).
3. **Notes & Pages on a folder with no paper at all** (a selfie folder). Nothing moves, no review appears, and the summary says so. Test in Task 3.
4. **Models missing, or declined at the download question.** The app goes back Home and nothing else happens. Test in Task 4.
5. **Huge or odd photos** (a 10000×500 panorama, an RGBA PNG, a 1×1 image, an EXIF-rotated photo) are embedded without error, the right way up. Test in Task 1.

---

## File Structure

```
src/photosort/ai/models.py             + clip_visual, clip_textual, clip_tokenizer entries; CLIP_MODELS key list
src/photosort/clip.py                  NEW  ClipEngine (image_embedding, text_embeddings), preprocess, tokenize, CLIP_KIND cache helpers
src/photosort/thresholds.py            + CONTENT_SURE/UNSURE, NOTES_SURE/UNSURE, CLIP_LOGIT_SCALE, CONTENT_BACKGROUND_PROMPTS, NOTES_*_PROMPTS, CLIP_ENGINE_FAILS, category limits
src/photosort/settings.py              + content_categories
src/photosort/modes/_clip_scan.py      NEW  embed every photo (cache, progress, cancel) → ClipScan
src/photosort/modes/content.py         NEW  classify + plan_content
src/photosort/modes/notes.py           NEW  classify + plan_notes
src/photosort/modes/__init__.py        MODES flags; MODE_SPECS kinds "content" and "notes"
src/photosort/ui/category_review_page.py NEW  grid of unsure photos with a category picker each
src/photosort/ui/category_editor.py    NEW  the Settings category list editor
src/photosort/ui/settings_dialog.py    + "Content categories…" button
src/photosort/ui/main_window.py        wiring
```

---

### Task 1: ClipEngine

**Files:**
- Create: `src/photosort/clip.py`, `tests/test_clip.py`
- Modify: `src/photosort/ai/models.py`, `tests/test_models.py`, `src/photosort/thresholds.py`

**Interfaces:**
- Consumes: `ai.runtime.make_session(path, use_gpu)`, `ai.models.model_path` and `imageinfo.open_image` / `UnreadableImage` (EXIF orientation applied).
- Produces:
  - `models.MODELS` gets the keys `"clip_visual"`, `"clip_textual"` and `"clip_tokenizer"`, using the exact values in the Global Constraints. `models.CLIP_MODELS = ["clip_visual", "clip_textual", "clip_tokenizer"]`.
  - `clip.preprocess(img: PIL.Image.Image) -> np.ndarray` gives float32 `(3,224,224)`, following the measured recipe. A sub-224 image is upscaled.
  - `clip.load_for_clip(path: Path) -> PIL.Image.Image` opens the photo with `img.draft("RGB", (224, 224))` for JPEGs, keeping the shortest side ≥ 224, with EXIF orientation applied. It raises `UnreadableImage`.
  - `clip.ClipEngine(use_gpu: bool)`:
    - sessions open lazily under a lock, like `faces.FaceEngine`, with the same thread contract;
    - `image_embedding(path) -> np.ndarray` gives `(512,)` float32 of unit length, or raises `UnreadableImage`;
    - `text_embeddings(texts: list[str]) -> np.ndarray` gives `(n, 512)` unit rows, one run per text because the batch is 1, with an in-memory cache per text string.
  - `clip.tokenize(tokenizer, text) -> np.ndarray` gives int32 `(1, 77)`.
  - `clip.CLIP_KIND = "clip-v1"`. `clip.pack(e) -> bytes` and `clip.unpack(b) -> np.ndarray` (512 `<f4`); `unpack` raises `ValueError` on the wrong length.
  - `thresholds.CLIP_LOGIT_SCALE = 100.0`.

- [ ] **Step 1: Write failing tests:**
  - `preprocess`:
    - a 448×224 image gives `(3,224,224)`;
    - a 100×50 image is upscaled;
    - a uniform grey (128) pixel equals `(128/255 - mean)/std` per channel.
  - `load_for_clip`:
    - an EXIF orientation-6 JPEG comes back rotated;
    - a 4000×3000 JPEG decodes with its shortest side ≥ 224 and < 3000 (draft used);
    - a 10000×500 panorama, an RGBA PNG and a 1×1 PNG all work;
    - an unreadable file raises.
  - `tokenize("a photo of food")` gives `[49406, 320, 1125, 539, 1559, 49407, 0, …]` of length 77. A 200-word text is cut to 77 with 49407 last.
  - `pack`/`unpack` round-trip, and a bad length raises.
  - With fake sessions (monkeypatched `make_session` returning objects with `get_inputs()`/`run()`), `image_embedding` and `text_embeddings` return unit vectors, and text runs once per distinct string.
  - Real model, `@pytest.mark.skipif(models.missing(CLIP_MODELS), reason="CLIP model not downloaded")`, CPU: a white image with large black printed text scores higher for `"a photo of a printed document"` than for `"a photo of food"`.
- [ ] **Step 2:** Run `uv run pytest tests/test_clip.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Import `tokenizers` inside `ClipEngine` (lazily), like onnxruntime, so app start never loads it.
- [ ] **Step 4:** Run `uv run pytest -W error`. Expected: all pass. The real-model test runs on this laptop.
- [ ] **Step 5: Commit** `feat: OpenCLIP image and text embeddings`.

---

### Task 2: Content mode logic

**Files:**
- Create: `src/photosort/modes/_clip_scan.py`, `src/photosort/modes/content.py`, `tests/test_content.py`
- Modify: `src/photosort/settings.py`, `tests/test_settings.py`, `src/photosort/thresholds.py`

**Interfaces:**
- Consumes: Task 1's `ClipEngine`, `CLIP_KIND`, `pack` and `unpack`; `AnalysisCache`; `scanner.scan`; `media.kind`; and `naming.fit_name`, `fold` and `TargetAllocator`. If `fit_name` is not on this branch yet, add it to `naming.py` with the same contract as the stage 4 branch: cut to N characters and M UTF-8 bytes at code points, strip a trailing combining mark only when the name was cut, then `safe_name`, then a `_` prefix when `scanner.skipped_folder`. Merging will reconcile the two.
- Produces:
  - `_clip_scan.ClipScan`, a dataclass with `embeddings: dict[str, np.ndarray]` (rel to embedding), `photos: int` and `unreadable: int`.
  - `_clip_scan.embed_photos(root, include_subfolders, embed: Callable[[Path], np.ndarray], progress=None, cancel=None, cache=None) -> ClipScan | None`:
    - photos only; it uses the cache under `CLIP_KIND`, and a bad cache entry is re-embedded;
    - `UnreadableImage` is counted;
    - any other exception follows face mode's rule: log it and count the photo unreadable, but re-raise if the first 5 photos all failed;
    - it returns None if cancelled.
  - `content.Verdict`, a dataclass with `rel`, `best: str`, `prob: float` and `ranked: list[tuple[str, float]]` (all categories, best first).
  - `content.judge(scan: ClipScan, categories: list[str], category_embeddings: np.ndarray, background_embeddings: np.ndarray) -> tuple[dict[str, str], list[Verdict]]` gives the sure map (rel to category) and the unsure verdicts, sorted by rel. Photos below `CONTENT_UNSURE` appear in neither.
  - `content.plan_content(root, scan, sure: dict[str, str], answers: dict[str, str | None]) -> Plan`:
    - mode `"content"`;
    - `answers` maps an unsure rel to a category name, or to None to leave it put;
    - an unanswered unsure photo stays;
    - the folder is `fit_name(category)` at the root;
    - summary: `f"{n} photos → {c} category folders"`, `f"{s} photos stay where they are"`, `f"{u} photos couldn't be read"` and `f"{k} already in place"`, each line only when its count is > 0, except the first.
  - `Settings.content_categories: list[str]`, with the default and validation from the Global Constraints.

- [ ] **Step 1: Write failing tests** (fake embeddings: unit basis vectors for photos and categories, so the probabilities are known exactly):
  - `judge` with a photo aligned to "Food":
    - sure when p ≥ 0.60 (`CONTENT_SURE`);
    - a photo between two categories is unsure, with `ranked` correct;
    - an orthogonal photo goes nowhere.
  - `embed_photos`:
    - a video is never embedded;
    - an unreadable photo is counted and not cached;
    - the second run is all cache hits (the embed fake counts its calls);
    - a garbage cache entry is re-embedded;
    - a cancel returns None;
    - the 5-failure rule.
  - `plan_content`:
    - the sure photos move to `Food/` and `Pets & Animals/`;
    - an unsure photo answered "Food" moves, and one answered None stays;
    - an unanswered unsure photo stays;
    - `Food/a.jpg` with subfolders on is unchanged;
    - a category `"Bad Photos"` gives `_Bad Photos/`;
    - the summary text is exact.
  - **Review Focus 1:** after a run, changing the categories and re-judging the same `ClipScan` gives the new folders, with no embed calls.
  - Settings:
    - the default list and its order;
    - an old file without the field loads the default;
    - `[]`, `["", "x"]`, `["Food", "food"]` and 31 names each give the default.
- [ ] **Step 2:** Run `uv run pytest tests/test_content.py tests/test_settings.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `uv run pytest -W error`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: content mode matching and planning`.

---

### Task 3: Notes & Pages logic

**Files:**
- Create: `src/photosort/modes/notes.py`, `tests/test_notes.py`
- Modify: `src/photosort/thresholds.py`

**Interfaces:**
- Consumes: Task 2's `ClipScan`, `embed_photos` and `Verdict`; `TargetAllocator`.
- Produces:
  - `notes.KINDS = ["Handwritten", "Book Pages", "Printed Documents", "Receipts"]`, `notes.PAPER_PROMPTS` (4 prompts, in the same order) and `notes.OTHER_PROMPTS` (10 prompts). All values are from the Global Constraints.
  - `notes.judge(scan, text_embeddings_paper, text_embeddings_other) -> tuple[dict[str, str], list[Verdict]]`:
    - the sure map is rel to kind;
    - each unsure `Verdict` has `best` = kind, `prob` = paper, and `ranked` = the 4 kinds by probability.
  - `notes.plan_notes(root, scan, sure, answers) -> Plan`:
    - mode `"notes"`, with targets `f"Notes & Pages/{kind}/<basename>"`;
    - summary: `f"{n} photos → Notes & Pages"` and `f"{s} photos stay where they are"`, plus the unreadable and already-in-place lines as in content;
    - **Review Focus 3:** no paper found gives the first line `"0 photos → Notes & Pages"` and no review.
- [ ] **Step 1: Write failing tests** (basis-vector fakes):
  - a receipt-aligned photo is sure and goes to `Notes & Pages/Receipts/`;
  - a photo split between "printed document" and "people" with paper 0.6 is unsure;
  - a people photo goes nowhere;
  - the already-in-place rule holds;
  - an existing `notes & pages/receipts/` (other case) is reused;
  - the summary text is exact.
- [ ] **Step 2:** Run `uv run pytest tests/test_notes.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `uv run pytest -W error`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: notes and pages matching and planning`.

---

### Task 4: Review page, category editor and main window wiring

**Files:**
- Create: `src/photosort/ui/category_review_page.py`, `src/photosort/ui/category_editor.py`, `tests/test_ui_content.py`
- Modify: `src/photosort/modes/__init__.py`, `src/photosort/ui/main_window.py`, `src/photosort/ui/settings_dialog.py`, `tests/test_ui_flow.py`, `tests/test_ui_dialogs.py`, `tests/test_date_mode.py` (`test_modes_list`)

**Interfaces:**
- Consumes: Tasks 1–3; `ensure_models(parent, CLIP_MODELS)`; the `review_page` off-thread thumbnail pattern; `ui/qimage.pil_to_qimage`.
- Produces:
  - `MODE_SPECS["content"]` is kind `"content"` and `MODE_SPECS["notes"]` is kind `"notes"`. The main window runs both itself (like face), so `prepare` raises `NotImplementedError`. `MODES` marks both available.
  - `CategoryReviewPage(QWidget)`, `load(root, verdicts: list[Verdict], choices: list[str], answers: dict[str, str | None] | None = None)`:
    - a scrollable grid of cards; each card has a thumbnail (off-thread, generation token), the file name, `f"{best}? {round(prob*100)}% sure"`, and a `QComboBox` with `choices` plus `"Leave it where it is"`;
    - the combo is preset to `best`, or to a previous answer;
    - double-click opens the photo large; reuse the stage 4 `PhotoPreview` if it is on this branch, otherwise a minimal `QDialog` with the photo fitted;
    - buttons: `leave_all_button` "Leave all where they are", `back_button` "Back" and `continue_button` "Continue";
    - signals: `finished(dict)` (rel to category or None) and `cancelled()`.
  - `CategoryEditor(QDialog)`, `CategoryEditor(categories: list[str], parent)`:
    - a list with Add…, Rename…, Remove, Move up, Move down and "Reset to defaults";
    - it refuses an empty name, a duplicate by `fold`, a name that `scanner.skipped_folder` rejects, a name over 50 characters, or a 31st name, each with an inline message;
    - Remove is disabled when 1 name is left;
    - `categories() -> list[str]`.
  - Settings dialog: a `"Content categories…"` button opens the editor and saves to `Settings.content_categories`.
  - Main window flow:
    - Content or Notes → Start → `ensure_models(self, CLIP_MODELS)` (declined goes back to Home with nothing else happening);
    - progress "Looking at photos… i of n";
    - a worker `Task` runs `embed_photos`, building the `ClipEngine(settings.use_gpu)` on the worker and opening `AnalysisCache` there;
    - it then makes the text embeddings, also on the worker, and judges;
    - unsure verdicts go to `CategoryReviewPage`; none go straight to `plan_content`/`plan_notes`, then the summary;
    - Back on the summary returns to the review page with the answers kept, or Home when there was no review;
    - `OSError` while planning goes Home and calls `show_error`.

- [ ] **Step 1: Write failing tests** (fake engine factory, injectable like `engine_factory` in face):
  - review page:
    - the cards and the preset combos;
    - "Leave all where they are" sets every combo to leave;
    - Continue emits the answers;
    - Back emits `cancelled`;
  - the editor rules (each refusal), with Reset restoring the defaults;
  - `test_content_end_to_end`: tmp photos, a fake engine with chosen embeddings, Content → Start → review → change one answer → Continue → summary → Go → files in category folders; Undo restores them and the folders are gone;
  - `test_notes_end_to_end`: the same flow with one sure receipt and nothing unsure (no review page);
  - with models missing (`ensure_models` monkeypatched to False), Content goes back Home;
  - `test_modes_list` includes content and notes.
- [ ] **Step 2:** Run `uv run pytest tests/test_ui_content.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `uv run pytest -W error`, and run the UI files 3×. Expected: all pass.
- [ ] **Step 5: Commit** `feat: content and notes review, category editor and wiring`.

---

### Task 5: Real-data check (controller)

- [ ] **Step 1:** On `~/PhotoSortTest/Camera` (a copy), from a scratch script, run `embed_photos` on the GPU and then on the CPU (record the times), then rerun with the cache. Judge with the default categories and with Notes.
- [ ] **Step 2:** Look at contact sheets of 15 sure photos per mode and of all the unsure ones.
  - **Tuning rule:** if more than 20 % of the sure photos are wrong, or more than 40 % of the photos end up unsure, re-tune `CONTENT_*`/`NOTES_*`. Record the ruling, then rerun.
- [ ] **Step 3:** Plan with every unsure photo accepted as suggested, execute, undo, and check the SHA-256 of every file against a fresh snapshot.
- [ ] **Step 4:** Record everything in the ledger. Merge with stage 4 when both are done.
