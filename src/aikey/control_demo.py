"""Credentials-free, read-only synthetic preview; no device/provider startup."""

from __future__ import annotations

import argparse

from aiohttp import web

from .control_site import _page


def demo_page() -> web.Response:
    """Fixed invented examples, never configuration, media or model output."""
    response = _page("Synthetic demo — not connected", """
<a href='#main'>Skip to demo content</a>
<header>
  <h1>Synthetic demo — not connected</h1>
  <p><strong>Read-only fictional preview. No camera, controller or model is connected.
  All results below are written examples, not inference or native acceptance.</strong></p>
  <p>No credentials, adoption, configuration, media or state files are loaded.
  This preview cannot save settings, discover devices or send model requests.</p>
  <nav aria-label='Demo sections'>
    <a href='#roles'>Model roles</a> · <a href='#cameras'>Camera policies</a> ·
    <a href='#results'>Fictional results</a> · <a href='#copies'>Data copies</a>
  </nav>
</header>
<main id='main'>
  <section id='roles' aria-labelledby='roles-title'>
    <h2 id='roles-title'>Model roles — examples only</h2>
    <p>These labels are not recommended models, valid endpoints or active settings.</p>
    <dl>
      <dt>AI Key caption</dt><dd>Fictional local vision model · selected frames and prompt</dd>
      <dt>AI Port detection</dt><dd>Fictional local detector · selected per-camera frames</dd>
      <dt>Search embedding</dt><dd>Fictional encoder · images or text according to the pinned index</dd>
      <dt>Speech</dt><dd>Off · no audio sharing or transcription</dd>
      <dt>Face and plate recognition</dt><dd>Off · separate explicit camera opt-ins required</dd>
    </dl>
    <p>In the real site, changing a vision model is not an embedding/index migration.
    Saving settings does not test or activate a deployment. A remote provider can
    receive the selected role input and charge separately; review its destination first.</p>
  </section>
  <section id='cameras' aria-labelledby='cameras-title'>
    <h2 id='cameras-title'>Camera policies — invented cameras</h2>
    <ul>
      <li>Demo walkway: captions allowed in this fictional example; speech/face/plates off.</li>
      <li>Demo driveway: AI Port object detections allowed in this fictional example;
      no face, plate or audio processing.</li>
      <li>Demo private room: all processing off.</li>
    </ul>
    <p>Illustrative rolling-hour budget: 3 of 12 caption reservations used.
    No real quota was reserved and no charge occurred. A paid or uncertain attempt
    must not be silently refunded or automatically retried.</p>
  </section>
  <section id='results' aria-labelledby='results-title'>
    <h2 id='results-title'>Fictional results — not model predictions</h2>
    <dl>
      <dt>Written caption example</dt><dd>A fictional delivery box is beside the demo door.</dd>
      <dt>Written detection example</dt><dd>Object: package. Illustrative confidence: 0.93.</dd>
      <dt>Written search example</dt><dd>“package beside a door” → one invented result.</dd>
      <dt>Native persistence</dt><dd>Not tested by this demo.</dd>
      <dt>Recognition quality, latency and provider cost</dt><dd>Not measured.</dd>
    </dl>
    <p>There are no real images, recordings, identities, transcripts, plate strings,
    event identifiers or saved native results in this page.</p>
  </section>
  <section id='copies' aria-labelledby='copies-title'>
    <h2 id='copies-title'>Data copies and retention</h2>
    <p>This preview creates no application state. In a real deployment, temporary
    decode files, active caption journals, replay markers, live search databases,
    Protect events, provider storage and backups have independent lifetimes.
    Local deletion is not deletion of every copy. File permissions are not encryption.</p>
    <p>The signed-in control site has a separate read-only data-flow inventory.
    The synthetic preview neither chooses a retention period nor deletes anything.</p>
  </section>
</main>
<footer><p>Demo only. Closing this server ends the preview; there is no adoption
identity or application data to restore.</p></footer>
""")
    # The shared real-site page permits same-origin forms. This preview has no
    # mutation routes, and additionally refuses all form submission in the browser.
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'none'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    return response


def demo_app() -> web.Application:
    app = web.Application(client_max_size=1024)

    async def index(request: web.Request) -> web.Response:
        return demo_page()

    app.router.add_get("/", index)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only fictional control-site preview; no connections")
    parser.add_argument("--port", type=int, default=8766, help="Loopback port (1024–65535)")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    web.run_app(demo_app(), host="127.0.0.1", port=args.port, access_log=None,
                print=lambda _: print(f"Synthetic read-only demo: http://127.0.0.1:{args.port}/ · no live connections"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
