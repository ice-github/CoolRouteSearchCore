---
name: 3d-html-validation
description: Use when checking generated 3D-rendered HTML outputs in this repository. Follow this skill to inspect Plotly 3D HTML with Dockerized Playwright/Chromium only, capture screenshots from the containerized browser, and judge whether the 3D result is visually reasonable by comparing it against the corresponding 2D sampling preview and point-cloud comparison images.
---

# 3D HTML Validation

Use this skill whenever a task asks whether a generated 3D HTML output "looks right" or needs human-style visual confirmation.

## Rules

- Do not use a host browser for validation.
- Use Dockerized Playwright/Chromium only.
- Treat a result as acceptable only after comparing the 3D output against the matching 2D sampling preview.
- Prefer the point-cloud comparison image for human-facing shape validation before trusting the final surface rendering.

## Find the target files

For a target such as `..._sampling_surface_mean.html`, also locate:

- the matching `..._sampling_preview_mean.png`
- the matching `..._sampling_point_cloud_human_compare.png` when present
- any `..._sampling_surface_recheck_*_topdown.png` or `..._human_compare.png` artifacts when present

Pick the newest HTML if the user asks for the latest output.

Example file discovery:

```bash
find workspace -type f \( -name '*.html' -o -name '*.png' \) -printf '%TY-%Tm-%Td %TH:%TM:%TS %p\n' | sort
```

## Render the HTML in Docker Chromium

Use the Playwright Docker image already used by this repository. If Playwright Python is unavailable in the container, use the Chromium binary bundled in that image directly.

Recommended command pattern:

```bash
docker run --rm \
  -v /home/ubuntu/workspace/CoolRouteSearchCore:/work \
  -w /work \
  mcr.microsoft.com/playwright/python:v1.58.0-noble \
  bash -lc '"/ms-playwright/chromium-1208/chrome-linux64/chrome" \
    --headless \
    --no-sandbox \
    --allow-file-access-from-files \
    --enable-webgl \
    --use-angle=swiftshader \
    --enable-unsafe-swiftshader \
    --ignore-gpu-blocklist \
    --window-size=1800,1400 \
    --run-all-compositor-stages-before-draw \
    --virtual-time-budget=5000 \
    --screenshot="/work/<output.png>" \
    "file:///work/<target.html>"'
```

Replace `<target.html>` and `<output.png>` with repository-relative paths under `/work`.

## How to judge the result

Check these in order:

1. The HTML actually renders a visible 3D result in Docker Chromium.
2. The outline and occupied area agree with the 2D preview or top-down recheck image.
3. Hot and cool regions are in broadly the same places as the 2D preview.
4. The point-cloud comparison still looks aligned before trusting the final surface.
5. There is no obvious failure mode such as blank canvas, only a colorbar, WebGL unsupported text, or a surface shifted away from the sampled area.

## Failure criteria

Report the HTML output as not yet acceptable if any of these occur:

- `WebGL is not supported` appears in the screenshot
- only axes or a colorbar appear and the surface is missing
- the rendered shape does not line up with the 2D sampling preview
- the visible temperature pattern materially disagrees with the 2D sampling preview

## Response pattern

When reporting back:

- identify the exact HTML file checked
- state that validation used Dockerized Playwright/Chromium
- name the comparison images used
- separate `data pattern looks plausible` from `HTML render is acceptable`

If the data distribution looks reasonable but the HTML render itself is broken, say so explicitly.
