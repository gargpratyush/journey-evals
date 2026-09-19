"""Local-browser freshness/execution regressions. No model calls or external websites."""

import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from journey_evals.isolation import OwnedSession  # noqa: E402

HTML = """<!doctype html><title>Guard checks</title>
<style>body{margin:30px}button{width:180px;height:50px}#outside{position:absolute;top:3000px}</style>
<p id="context">Cart total: $10</p>
<button id="target" onclick="window.clicks=(window.clicks||0)+1">Continue</button>
<label>City<input id="field" value="Zurich"></label>
<label><input id="toggle" type="checkbox">Refundable</label>
<select aria-label="Category"><option>All</option><option>Design</option></select>
<p id="outside">Unrelated offscreen text</p>"""


def main():
    """Every guard runs inside one owned browser session, never a personal profile."""
    with tempfile.TemporaryDirectory() as diagnostics:
        with OwnedSession(diagnostics):
            return guards()


def guards():
    from jev_ultrafast.browser import Browser, StalePage

    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Continue")
        browser.evaluate("document.querySelector('#target').style.transform='translateX(200px)'")
        assert browser.fresh(page), "Movement should use fresh geometry, not another model call"
        browser.act(action, page)
        assert browser.evaluate("window.clicks") == 1
        passed.append("moving target clicked at its current location")

        browser.evaluate("document.querySelector('#outside').textContent='Updated outside the viewport'")
        assert browser.fresh(page)
        passed.append("unrelated offscreen text does not invalidate")

        mutations = {
            "visible context": "document.querySelector('#context').textContent='Cart total: $100'",
            "accessible label": "document.querySelector('#target').setAttribute('aria-label','Delete account')",
            "field property": "document.querySelector('#field').value='London'",
            "checkbox property": "document.querySelector('#toggle').checked=true",
            "disabled target": "document.querySelector('#target').disabled=true",
            "read-only field": "document.querySelector('#field').readOnly=true",
            "hidden target": "document.querySelector('#target').style.display='none'",
            "replaced node": "document.querySelector('#target').outerHTML=document.querySelector('#target').outerHTML",
            "dropdown option": "document.querySelector('select').options[1].text='Coastal'",
        }
        for label, expression in mutations.items():
            browser.evaluate("document.querySelector('#target').style.display='block'; "
                             "document.querySelector('#target').disabled=false")
            page = browser.observe(screenshot=False)
            browser.evaluate(expression)
            assert not browser.fresh(page), label
            passed.append(label + " invalidates")

        browser.evaluate("document.querySelector('#target').disabled=false; "
                         "document.querySelector('#target').style.display='block'")
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Delete account")
        # A textless overlay does not alter the model's semantic state, but must block a click.
        browser.evaluate("const cover=document.createElement('div'); "
                         "cover.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; "
                         "document.body.append(cover)")
        assert browser.fresh(page)
        try:
            browser.act(action, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Covered target was clicked")
        assert browser.evaluate("window.clicks") == 1
        passed.append("overlay blocked before input")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <form><p id="price">Total $10</p>
          <button type="button" id="buy">Buy</button>
          <label>Search <input id="query" role="combobox" aria-controls="suggestions"></label>
          <div role="listbox" id="suggestions"></div>
          <label><input id="check" type="checkbox">Enabled</label>
          <label><input id="radio" type="radio">Choice</label>
          <input id="readonly" aria-label="Read only" readonly>
          <input id="secret" type="password" value="never expose this">
          <button id="off" disabled>Disabled</button>
          <select id="category" aria-label="Category">
            <option>All</option><option>Design</option><option disabled>Unavailable</option>
          </select></form><aside id="unrelated">News</aside>
        """))
        page = browser.observe(screenshot=False)
        buy = next(a for a in page["actions"] if a["label"] == "Buy")
        browser.evaluate("document.querySelector('#unrelated').textContent='New unrelated news'")
        assert browser.fresh(page, buy)
        assert not browser.fresh(page)
        passed.append("click guard accepts unrelated visible updates; terminal guard rejects them")
        for label, expression in {
            "nearby price": "document.querySelector('#price').textContent='Total $100'",
            "form value": "document.querySelector('#query').value='changed'",
            "form toggle": "document.querySelector('#check').checked=true",
            "target replacement": "document.querySelector('#buy').outerHTML=document.querySelector('#buy').outerHTML",
        }.items():
            page = browser.observe(screenshot=False)
            buy = next(a for a in page["actions"] if a["label"] == "Buy")
            browser.evaluate(expression)
            assert not browser.fresh(page, buy), label
            passed.append(label + " invalidates action-specific guard")

        page = browser.observe(screenshot=False)
        actions = page["actions"]
        for role in ("checkbox", "radio"):
            assert {a["kind"] for a in actions if a.get("role") == role} == {"click"}
        assert {a["kind"] for a in actions if a["label"] == "Read only"} == {"click"}
        assert not any(a["label"] == "Disabled" or a.get("value") == "never expose this" for a in actions)
        assert [a["value"] for a in actions if a["kind"] == "select"] == ["Design"]
        passed.append("native controls expose only supported operations and safe values")

        select = next(a for a in actions if a["kind"] == "select")
        browser.act(select, page)
        assert browser.evaluate("document.querySelector('#category').value") == "Design"
        passed.append("native dropdown selects an observed option")

        browser.evaluate("document.querySelector('#query').addEventListener('input',()=>setTimeout(()=>{"
                         "document.querySelector('#suggestions').innerHTML='<div role=option>Generated</div>'"
                         "},60))")
        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a["kind"] == "fill")
        browser.act(field, page, text="Generated")
        page = browser.observe(screenshot=False)
        value = browser.evaluate("document.querySelector('#query').value")
        assert value == "Generated", repr(value)
        assert any(a.get("role") == "option" for a in page["actions"])
        passed.append("real text input waits for asynchronous combobox suggestions")
        browser.call("Page.navigate", url="about:blank")
        assert not browser.fresh(page, field)
        passed.append("navigation invalidates the old document")
    finally:
        browser.close()
    passed.extend(telemetry_guards())
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


EVALUATION_HTML = """<!doctype html><title>Evaluation state</title>
<style>
body{margin:20px}
#frame{height:24px;overflow:hidden;position:relative}
#cover{position:absolute;inset:0;background:#fff}
#offscreen{position:absolute;top:4000px}
</style>
<p id="status" role="status"></p>
<div id="frame"><button id="confirm">Confirm booking</button><div id="cover"></div></div>
<button id="off" disabled>Disabled action</button>
<button id="offscreen">Far below</button>
<script>
document.getElementById('status').textContent = 'Ready';
</script>"""


def _serve(html):
    """A loopback origin for the guard document: telemetry needs a real page, not a data URL."""
    body = html.encode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def telemetry_guards():
    """Evidence-collection guarantees: continuity, geometry, and what survives a navigation.

    These are separate from the freshness guards because they protect a different claim. Freshness
    decides whether an action may still be executed; this decides whether the record of what
    happened is complete enough to judge at all.
    """
    import tempfile

    from jev_ultrafast.browser import Browser
    from journey_evals.evidence import Collector
    from journey_evals.report import Journal

    passed = []
    directory = tempfile.mkdtemp(prefix="guards-")
    journal = Journal(directory)
    collector = Collector(journal)
    # Telemetry is installed for every future document, so it must be in place before the
    # document under test is loaded, and the document must come from a real origin: a data URL
    # has no origin a binding can be installed into.
    server = _serve(EVALUATION_HTML)
    origin = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    browser = Browser("about:blank")
    try:
        collector.install(browser)
        browser.call("Page.navigate", url=origin)
        time.sleep(0.5)
        first = collector.checkpoint("initial")
        assert first["complete"], "A freshly installed collector must report complete evidence"
        assert first["sequence"] >= 1
        passed.append("telemetry reports a complete window on a quiet document")

        second = collector.checkpoint("second")
        assert second["sequence"] > first["sequence"], "Watermarks must advance"
        assert second["documentId"] == first["documentId"]
        passed.append("telemetry watermarks advance without a document change")

        elements = {e["label"]: e for e in second["evaluation"]["controls"]}
        confirm = elements.get("Confirm booking")
        assert confirm, "A clipped control must still appear in evaluation state"
        assert confirm["ancestorClipped"] or confirm["occluded"], confirm
        passed.append("a clipped and covered control is measured rather than dropped")

        page = browser.observe(screenshot=False)
        labels = {a["label"] for a in page["actions"]}
        assert "Disabled action" not in labels, "A disabled control is not an offered action"
        assert "Disabled action" in elements, "A disabled control still belongs to the state"
        assert elements["Disabled action"]["disabled"] is True
        passed.append("disabled controls are absent from the action table and present in state")

        assert "Far below" in elements and elements["Far below"]["offscreen"] is True
        passed.append("offscreen controls are reported as offscreen, not as missing")

        browser.call("Page.navigate", url="data:text/html," + quote("<title>Next</title><p>Gone</p>"))
        try:
            after = collector.checkpoint("after_navigation")
        except (RuntimeError, ValueError):
            passed.append("a navigation that loses telemetry is an error, never a silent pass")
        else:
            assert after["documentId"] != first["documentId"], \
                "A new document must produce a new document identity"
            passed.append("a navigation is reported as a new document rather than continuity")
    finally:
        try:
            collector.close()
        finally:
            browser.close()
            journal.close()
    return passed


if __name__ == "__main__":
    main()
