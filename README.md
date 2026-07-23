# sphinx-embed-emscripten

A Sphinx extension for embedding [Emscripten](https://emscripten.org/)-compiled WebAssembly
applications in HTML documentation pages via `<iframe>`.

## How it works

At HTML build time the extension reads a zip artifact (e.g. a CI build artifact) containing
an Emscripten-generated HTML shell page and its sibling files (`.js`, `.wasm`, `.data`, etc.).
All files from the same directory as the HTML entry are extracted into
`_build/html/_static/emscripten/<uid>/`, leaving the source tree untouched.

On the rendered page, a **Start** button creates an `<iframe>` pointing at the extracted HTML
page. **Stop** removes the iframe — the browser destroys the entire JS context, audio threads,
Web Workers, and WASM heap cleanly with no extra coordination required.

## Installation

```bash
pip install -e ./sphinx-embed-emscripten
```

Add to `conf.py`:

```python
extensions = [
    ...,
    "sphinx_embed_emscripten",
]
```

## Usage

    ```{emscripten} path/to/artifact.zip
    :entry: demo/mygame.html
    :caption: My Game — build $DATE$
    :aspect-ratio: 16/9
    ```

The zip path is resolved **relative to the document** that contains the directive. Glob
patterns are supported — exactly one match is required.

### Options

| Option | Default | Description |
|---|---|---|
| `entry` | *(auto-detected)* | Path to the HTML shell page **inside** the zip, e.g. `demo/mygame.html`. Required when the zip contains more than one `.html` file. |
| `caption` | *(none)* | Caption shown below the embed. Supports variable substitution (see below). |
| `aspect-ratio` | `16/9` | Viewport aspect ratio — any valid CSS `aspect-ratio` value. |
| `no-fullscreen` | *(flag)* | Hide the fullscreen button. |
| `url-params` | *(none)* | Extra query string appended to the iframe's URL, e.g. `foo=1&bar=2`. |
| `size-hint-param` | *(none)* | One `file->param` mapping per line. For each entry, the extracted file's byte size is appended to the iframe URL as `param=<size>`, merged with `url-params`. Lets the embedded shell page know a bundled file's size without needing to inspect response headers itself. |

### Caption variables

The `:caption:` value may contain the following placeholders, resolved at build time from
the matched zip file:

| Variable | Expands to |
|---|---|
| `$FILENAME$` | Full filename of the zip, e.g. `mygame_emscripten_abc123.zip` |
| `$FILENAME_NO_EXT$` | Same, without the `.zip` extension |
| `$DATE$` | Modification date of the `.wasm` file inside the zip, formatted `YYYY-MM-DD` |

### Extracted files

All files in the same zip directory as the HTML entry are extracted. For an entry of
`demo/mygame.html`, everything under `demo/` is extracted — the HTML shell, JS loader,
`.wasm` bundle, `.data` file, worker scripts, etc.

## Emscripten build requirements

The zip must contain a self-contained Emscripten HTML shell page (the default output of
`emcc -o mygame.html ...`). The HTML page is loaded as-is inside the iframe, so it can use
any Emscripten output mode (`-sMODULARIZE`, pthreads, IDBFS, etc.) — the plugin has no
opinion on how the app is compiled or structured internally.

## Gen-AI Usage Disclaimer

This extension was made with help from Claude Sonnet 4.6 (via Claude Code) and Github Copilot.
