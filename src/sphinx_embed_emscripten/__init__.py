"""sphinx-embed-emscripten — embeds Emscripten WASM apps in Sphinx HTML pages via iframe.

Directive usage (MyST)::

    ```{emscripten} path/to/artifact.zip
    :entry: demo/newbase_demo.html
    :caption: My Game
    :aspect-ratio: 16/9
    ```

``path/to/artifact.zip`` is resolved relative to the document that contains the directive.

At HTML build time all files in the same zip directory as the HTML entry are extracted into
``_build/html/_static/emscripten/<uid>/`` so the source tree is never modified.  The app is
loaded in an ``<iframe>`` — start creates the iframe, stop removes it, giving the browser full
responsibility for tearing down the JS context, audio threads, and WASM heap cleanly.

Options
-------
``entry``        Path of the ``.html`` page *inside* the zip (e.g. ``demo/game.html``).
                 Required when the zip contains more than one ``.html`` file.
``caption``      Optional caption shown below the embed.
``aspect-ratio`` Canvas aspect ratio, default ``16/9``. Any CSS ``aspect-ratio`` value.
``no-fullscreen`` Flag — hide the fullscreen button.
``url-params``   Extra query string appended to the iframe's URL (e.g.
                 ``nb_fullscreen_btn=0`` to hide the shell's own built-in
                 fullscreen button, independent of ``no-fullscreen`` which
                 only controls the embed chrome's button).
``size-hint-param`` One ``file->param`` mapping per line. For each entry, the
                 extracted file's byte size is appended to the iframe URL as
                 ``param=<size>``, merged with ``url-params``. Lets a shell
                 page that doesn't otherwise know a bundled file's size
                 (e.g. because it's served pre-compressed with no
                 ``Content-Length``) receive it as a hint.
"""

from __future__ import annotations

import logging
import random
import string
import zipfile
from pathlib import Path, PurePosixPath
from typing import ClassVar

from docutils import nodes
from docutils.parsers.rst import directives
from sphinx.application import Sphinx
from sphinx.util.docutils import SphinxDirective

__title__ = "sphinx-embed-emscripten"
__version__ = "0.3.0"
__author__ = "Lucas Pires Camargo"
__license__ = "GPLv3"

logger = logging.getLogger(__name__)

CONFIG_ITEMS: dict[str, tuple] = {
    "emscripten_default_aspect_ratio": ("16/9", "html"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_uid(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _expand_caption(caption: str, zip_path: Path) -> str:
    """Expand $FILENAME$, $FILENAME_NO_EXT$, $DATE$ in caption strings."""
    if not caption or "$" not in caption:
        return caption

    filename = zip_path.name
    filename_noext = zip_path.stem

    date_str = ""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            candidates = [i for i in zf.infolist() if i.filename.endswith(".wasm")]
            if not candidates:
                candidates = zf.infolist()
            if candidates:
                info = max(candidates, key=lambda i: i.date_time)
                from datetime import date
                date_str = date(*info.date_time[:3]).strftime("%Y-%m-%d")
    except Exception:
        pass

    return (
        caption
        .replace("$FILENAME$", filename)
        .replace("$FILENAME_NO_EXT$", filename_noext)
        .replace("$DATE$", date_str)
    )


def _sniff_html_entry(zf: zipfile.ZipFile) -> str | None:
    """Return the path of the single HTML page in the zip, or None if ambiguous."""
    candidates = [
        n for n in zf.namelist()
        if n.endswith(".html") and not zf.getinfo(n).is_dir()
    ]
    return candidates[0] if len(candidates) == 1 else None


def _parse_size_hint_params(raw: str) -> list[tuple[str, str]]:
    """Parse a (possibly multi-line) ``file->param`` option value.

    Blank lines are ignored. Raises ValueError on a malformed entry.
    """
    pairs = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "->" not in line:
            raise ValueError(f"malformed size-hint-param entry {line!r} (expected 'file->param')")
        file_name, _, param_name = line.partition("->")
        file_name = file_name.strip()
        param_name = param_name.strip()
        if not file_name or not param_name:
            raise ValueError(f"malformed size-hint-param entry {line!r} (expected 'file->param')")
        pairs.append((file_name, param_name))
    return pairs


def _extract_html_dir(zf: zipfile.ZipFile, entry: str, dest_dir: Path) -> list[str]:
    """Extract all files in the same zip directory as the HTML entry.

    Returns list of extracted filenames (basenames only).
    """
    entry_parent = str(PurePosixPath(entry).parent)

    extracted = []
    for name in zf.namelist():
        info = zf.getinfo(name)
        if info.is_dir():
            continue
        p = PurePosixPath(name)
        if str(p.parent) != entry_parent:
            continue
        dest_file = dest_dir / p.name
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_bytes(zf.read(name))
        extracted.append(p.name)

    return extracted


# ---------------------------------------------------------------------------
# Docutils node
# ---------------------------------------------------------------------------

class EmscriptenNode(nodes.General, nodes.Element):
    """Represents an embedded Emscripten WASM application."""

    @staticmethod
    def visit_html(translator, node: EmscriptenNode) -> None:
        uid = node["uid"]
        zip_abspath: str = node.get("zip_abspath", "")
        zip_arg: str = node.get("zip_arg", zip_abspath)
        entry: str = node.get("entry", "")
        caption: str = node.get("caption", "")
        aspect_ratio: str = node.get("aspect_ratio", "16/9")
        allow_fullscreen: bool = node.get("allow_fullscreen", True)
        url_params: str = node.get("url_params", "")

        # --- resolve zip and extract to outdir ---
        entry_file = ""
        error_msg = ""

        if not zip_abspath or not Path(zip_abspath).is_file():
            error_msg = f"emscripten: zip file not found: {zip_arg}"
        else:
            try:
                with zipfile.ZipFile(zip_abspath) as zf:
                    if not entry:
                        entry = _sniff_html_entry(zf) or ""
                    if not entry:
                        names = [n for n in zf.namelist() if n.endswith(".html")]
                        error_msg = (
                            f"emscripten: zip contains multiple .html files — "
                            f"specify :entry: option. Found: {names}"
                        )
                    elif entry not in zf.namelist():
                        error_msg = f"emscripten: entry '{entry}' not found in zip"
                    else:
                        dest_dir = (
                            Path(translator.builder.outdir)
                            / "_static" / "emscripten" / uid
                        )
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        extracted = _extract_html_dir(zf, entry, dest_dir)
                        entry_file = PurePosixPath(entry).name
                        if not extracted:
                            error_msg = f"emscripten: no files extracted for entry '{entry}'"
                        else:
                            logger.info(
                                f"emscripten [{uid}]: extracted {extracted} → {dest_dir}"
                            )
                            size_hint_qs = []
                            for file_name, param_name in node.get("size_hint_params", []):
                                if file_name not in extracted:
                                    logger.warning(
                                        f"emscripten: size-hint-param file '{file_name}' "
                                        f"not found among extracted files {extracted}"
                                    )
                                    continue
                                size = (dest_dir / file_name).stat().st_size
                                size_hint_qs.append(f"{param_name}={size}")
                            if size_hint_qs:
                                url_params = "&".join(
                                    [p for p in [url_params, *size_hint_qs] if p]
                                )
            except zipfile.BadZipFile as exc:
                error_msg = f"emscripten: bad zip file {zip_abspath}: {exc}"

        if error_msg:
            logger.warning(error_msg)
            translator.body.append(
                f'<div class="border my-4 p-3 bg-danger-subtle text-danger-emphasis border-danger-subtle">'
                f'<code style="font-size:.85em">{error_msg}</code></div>\n'
            )
            raise nodes.SkipNode

        # --- compute URL base (relative from current page to _static/) ---
        current_docname = translator.builder.current_docname
        n_up = current_docname.count("/")
        prefix = "../" * n_up
        base_url = f"{prefix}_static/emscripten/{uid}/"

        # --- render HTML ---
        fs_btn = ""
        if allow_fullscreen:
            fs_btn = (
                f'<button class="btn btn-sm btn-outline-secondary" '
                f'id="emscripten-fs-{uid}" disabled '
                f'aria-label="Fullscreen">&#x26F6; Fullscreen</button>'
            )

        caption_html = ""
        if caption:
            caption_html = f'<div class="px-3 py-1 border-top text-body-secondary small">{caption}</div>'

        translator.body.append(f"""\
<div class="border my-4" id="emscripten-wrap-{uid}">
  <div class="d-flex align-items-center gap-2 p-2 bg-body-tertiary border-bottom">
    <button class="btn btn-sm btn-outline-secondary"
            id="emscripten-toggle-{uid}">&#x25B6; Start</button>
    <span class="flex-grow-1"></span>
    {fs_btn}
  </div>
  <div class="position-relative w-100" id="emscripten-viewport-{uid}"
       style="aspect-ratio:{aspect_ratio};display:none;background:#000"></div>
  {caption_html}
</div>
<script>
(function () {{
  var baseUrl   = '{base_url}';
  var entryFile = '{entry_file}';
  var urlParams = '{url_params}';

  var viewport  = document.getElementById('emscripten-viewport-{uid}');
  var btnToggle = document.getElementById('emscripten-toggle-{uid}');
  var btnFs     = document.getElementById('emscripten-fs-{uid}');

  var running = false;
  var iframe  = null;

  function onStart() {{
    running = true;
    viewport.style.display = 'block';
    btnToggle.textContent = '⏹ Stop';
    if (btnFs) btnFs.disabled = false;

    iframe = document.createElement('iframe');
    iframe.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;border:none;display:block;';
    iframe.allow = 'autoplay; fullscreen';
    iframe.allowFullscreen = true;
    iframe.src = baseUrl + entryFile + (urlParams ? '?' + urlParams : '');
    viewport.appendChild(iframe);
  }}

  function onStop() {{
    if (!running) return;
    running = false;
    viewport.style.display = 'none';
    btnToggle.textContent = '▶ Start';
    if (btnFs) btnFs.disabled = true;
    if (iframe) {{
      iframe.parentNode.removeChild(iframe);
      iframe = null;
    }}
  }}

  btnToggle.addEventListener('click', function () {{
    if (!running) {{ onStart(); }} else {{ onStop(); }}
  }});

  if (btnFs) {{
    btnFs.addEventListener('click', function () {{
      if (!iframe) return;
      if (document.fullscreenElement) {{
        document.exitFullscreen();
      }} else {{
        if (iframe.requestFullscreen)            iframe.requestFullscreen();
        else if (iframe.webkitRequestFullscreen) iframe.webkitRequestFullscreen();
      }}
    }});
  }}

  viewport.addEventListener('mousedown', function (e) {{
    if (e.button === 1 && running) e.preventDefault();
  }});
}})();
</script>
""")

    @staticmethod
    def depart_html(translator, node: EmscriptenNode) -> None:
        pass

    @staticmethod
    def visit_unsupported(translator, node: EmscriptenNode) -> None:
        node.replace_self(nodes.Text(
            "[Emscripten embed — only available in the HTML version of this document.]"
        ))

    @staticmethod
    def depart_unsupported(translator, node: EmscriptenNode) -> None:
        pass


# ---------------------------------------------------------------------------
# Directive
# ---------------------------------------------------------------------------

class EmscriptenDirective(SphinxDirective):
    """Embed an Emscripten-built WASM app.

    Required argument: path to the artifact zip file (relative to this document).
    """

    required_arguments = 1
    optional_arguments = 0
    final_argument_whitespace = False
    has_content = False

    option_spec: ClassVar[dict] = {
        "entry":             directives.unchanged,
        "caption":           directives.unchanged,
        "aspect-ratio":      directives.unchanged,
        "no-fullscreen":     directives.flag,
        "url-params":        directives.unchanged,
        "size-hint-param":   directives.unchanged,
    }

    def run(self) -> list[nodes.Node]:
        zip_arg = self.arguments[0].strip()

        doc_dir = Path(self.env.srcdir) / Path(self.env.docname).parent
        if Path(zip_arg).is_absolute():
            search_base = Path(zip_arg.rstrip("/"))
            matches = sorted(search_base.parent.glob(search_base.name))
        else:
            matches = sorted(doc_dir.glob(zip_arg))

        if len(matches) == 0:
            logger.warning(f"emscripten: no file matched glob '{zip_arg}'")
            return [EmscriptenNode(uid=_random_uid(), zip_abspath="", zip_arg=zip_arg,
                                   entry="", caption="", aspect_ratio="16/9",
                                   allow_fullscreen=True)]
        if len(matches) > 1:
            logger.warning(
                f"emscripten: glob '{zip_arg}' matched {len(matches)} files "
                f"({[str(m) for m in matches]}) — expected exactly one"
            )
            return [EmscriptenNode(uid=_random_uid(), zip_abspath="", zip_arg=zip_arg,
                                   entry="", caption="", aspect_ratio="16/9",
                                   allow_fullscreen=True)]

        zip_abspath = str(matches[0].resolve())

        aspect_ratio = (
            self.options.get("aspect-ratio")
            or self.env.config.emscripten_default_aspect_ratio
        )

        node = EmscriptenNode()
        node["uid"]            = _random_uid()
        node["zip_abspath"]    = zip_abspath
        node["zip_arg"]        = zip_arg
        node["entry"]          = self.options.get("entry", "")
        node["caption"]        = _expand_caption(self.options.get("caption", ""), matches[0])
        node["aspect_ratio"]   = aspect_ratio
        node["allow_fullscreen"] = "no-fullscreen" not in self.options
        node["url_params"]     = self.options.get("url-params", "")

        try:
            node["size_hint_params"] = _parse_size_hint_params(
                self.options.get("size-hint-param", "")
            )
        except ValueError as exc:
            logger.warning(f"emscripten: {exc}")
            node["size_hint_params"] = []

        return [node]


# ---------------------------------------------------------------------------
# Static asset injection
# ---------------------------------------------------------------------------


def on_html_page_context(
    app: Sphinx,
    pagename: str,
    templatename: str,
    context: dict,
    doctree: nodes.document,
) -> None:
    pass


# ---------------------------------------------------------------------------
# Extension setup
# ---------------------------------------------------------------------------

def setup(app: Sphinx) -> dict:
    for name, (default, rebuild) in CONFIG_ITEMS.items():
        app.add_config_value(name, default, rebuild)

    app.add_node(
        EmscriptenNode,
        html=(EmscriptenNode.visit_html, EmscriptenNode.depart_html),
        text=(EmscriptenNode.visit_unsupported, EmscriptenNode.depart_unsupported),
        latex=(EmscriptenNode.visit_unsupported, EmscriptenNode.depart_unsupported),
    )

    app.add_directive("emscripten", EmscriptenDirective)
    app.connect("html-page-context", on_html_page_context)

    return {
        "version": __version__,
        "parallel_read_safe": True,
        "parallel_write_safe": False,
    }
